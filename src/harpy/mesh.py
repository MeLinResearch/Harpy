"""The simulated agent mesh: the world HARPY is watching, and the only place
ground truth is created.

Every tick, each active agent emits one message of 1-3 claims to another agent.
Some claims are derived from claims it received, which is what creates the
ground-truth lineage graph and what makes corruption spread: a claim derived
from a corrupt claim is corrupt, whoever emitted it.

Nothing in this module is visible to HARPY except the messages themselves.
"""

from __future__ import annotations

import random
import uuid
from collections import deque
from dataclasses import dataclass

from .faults import FaultProfile, conflicting_payload, perturb_numeric, sample_onset_tick
from .types import Claim, ClaimPayload, LineageEdge, Message, Severity

_NAMESPACE = uuid.UUID("6f6b2f7a-1d4a-5c3b-9f2e-8a7c1b0d5e42")

NUMERIC_FIELDS: tuple[str, ...] = ("price_usd", "temperature_c", "count_units", "score_pct")
DATE_FIELDS: tuple[str, ...] = ("effective_date", "review_date")
ENUM_FIELDS: tuple[str, ...] = ("status", "tier")
ENUM_VALUES: dict[str, tuple[str, ...]] = {
    "status": ("open", "pending", "closed", "escalated"),
    "tier": ("bronze", "silver", "gold"),
}
ALL_FIELDS: tuple[str, ...] = NUMERIC_FIELDS + DATE_FIELDS + ENUM_FIELDS


@dataclass(frozen=True)
class Emission:
    """One message plus the mechanical facts the platform observes about it."""

    message: Message
    rejected_claim_ids: frozenset[str]
    input_tokens: int
    output_tokens: int


class Agent:
    __slots__ = ("agent_id", "active", "outbound_halted", "inbox", "restarts", "messages_sent")

    def __init__(self, agent_id: str, inbox_capacity: int) -> None:
        self.agent_id = agent_id
        self.active = True
        self.outbound_halted = False
        self.inbox: deque[Claim] = deque(maxlen=inbox_capacity)
        self.restarts = 0
        self.messages_sent = 0


class Mesh:
    def __init__(self, config: dict, severity: Severity, seed: int) -> None:
        sim = config["simulation"]
        self.config = config
        self.seed = int(seed)
        self.severity = Severity(severity)
        self.rng = random.Random((self.seed * 1_000_003) + 11)
        self.n_ticks = int(sim["n_ticks"])
        self.schema_version = int(sim["schema_version"])
        self.claims_min = int(sim["claims_per_message_min"])
        self.claims_max = int(sim["claims_per_message_max"])
        self.derive_probability = float(sim["derive_probability"])
        self.tolerance = float(sim["rejection_numeric_tolerance_pct"])
        self.legitimate_update_probability = float(sim["legitimate_update_probability"])
        self.malformed_token_fraction = float(sim["malformed_token_fraction"])
        self.numeric_ranges = {
            k: (float(v[0]), float(v[1])) for k, v in config["telemetry"]["numeric_ranges"].items()
        }

        worker = config["worker"]
        self.input_tokens_per_message = int(worker["input_tokens_per_message"])
        self.output_tokens_mean = float(worker["output_tokens_mean"])
        self.output_tokens_sd = float(worker["output_tokens_sd"])
        self.output_tokens_min = int(worker["output_tokens_min"])

        self.agents: dict[str, Agent] = {
            f"agent-{i:02d}": Agent(f"agent-{i:02d}", int(sim["inbox_capacity"]))
            for i in range(int(sim["n_agents"]))
        }
        self.subjects: tuple[str, ...] = tuple(
            f"subject-{i:02d}" for i in range(int(sim["n_subjects"]))
        )

        self.profile = FaultProfile.from_config(config, self.severity)
        self.faulty_agent_id = sorted(self.agents)[self.rng.randrange(len(self.agents))]
        self.fault_onset_tick = sample_onset_tick(
            self.n_ticks,
            float(sim["fault_onset_fraction_min"]),
            float(sim["fault_onset_fraction_max"]),
            self.rng,
        )

        self.current_tick = -1
        # Ground truth, written here and read only by scoring code.
        self.lineage_edges: set[LineageEdge] = set()
        self.corrupt_claim_ticks: list[int] = []
        self.total_claims = 0
        self.faulty_messages_after_onset = 0

    # -- queries -----------------------------------------------------------

    def active_agents(self) -> tuple[str, ...]:
        return tuple(sorted(a for a, agent in self.agents.items() if agent.active))

    def is_faulty_now(self, agent_id: str) -> bool:
        return agent_id == self.faulty_agent_id and self.current_tick >= self.fault_onset_tick

    # -- response-layer hooks (each touches exactly one named agent) --------

    def deactivate(self, agent_id: str) -> None:
        agent = self.agents[agent_id]
        agent.active = False
        agent.outbound_halted = True

    def halt_outbound(self, agent_id: str) -> None:
        self.agents[agent_id].outbound_halted = True

    def restart_clean(self, agent_id: str) -> None:
        agent = self.agents[agent_id]
        agent.inbox.clear()
        agent.active = True
        agent.outbound_halted = False
        agent.restarts += 1

    # -- emission ----------------------------------------------------------

    def step(self, tick: int) -> tuple[Emission, ...]:
        """Advance one tick: every active, un-halted agent emits one message."""
        self.current_tick = tick
        senders = [
            agent_id
            for agent_id in sorted(self.agents)
            if self.agents[agent_id].active and not self.agents[agent_id].outbound_halted
        ]
        recipients_pool = self.active_agents()
        if len(recipients_pool) < 2:
            return ()

        built: list[Message] = []
        for agent_id in senders:
            built.append(self._build_message(agent_id, tick, recipients_pool))

        emissions: list[Emission] = []
        for message in built:
            rejected = self._deliver(message)
            output_tokens = message.token_len
            emissions.append(
                Emission(
                    message=message,
                    rejected_claim_ids=rejected,
                    input_tokens=self.input_tokens_per_message,
                    output_tokens=output_tokens,
                )
            )
        return tuple(emissions)

    def _build_message(self, agent_id: str, tick: int, recipients_pool: tuple[str, ...]) -> Message:
        agent = self.agents[agent_id]
        faulty = self.is_faulty_now(agent_id)
        profile = self.profile

        candidates = [r for r in recipients_pool if r != agent_id]
        recipient = candidates[self.rng.randrange(len(candidates))]

        malformed = False
        tool_error = False
        retry_count = self._sample_retries()
        schema_version = self.schema_version
        if faulty and profile.severity is Severity.OVERT:
            malformed = self.rng.random() < profile.malformed_probability
            tool_error = self.rng.random() < profile.tool_error_probability
            retry_count += profile.retry_inflation
            schema_version += profile.schema_version_bump

        n_claims = self.rng.randint(self.claims_min, self.claims_max)
        claims: list[Claim] = []
        for index in range(n_claims):
            claims.append(self._build_claim(agent, tick, index, faulty, schema_version))

        token_len = int(
            max(
                self.output_tokens_min,
                self.rng.gauss(self.output_tokens_mean, self.output_tokens_sd),
            )
        )
        if malformed:
            token_len = max(1, int(token_len * self.malformed_token_fraction))

        agent.messages_sent += 1
        if faulty:
            self.faulty_messages_after_onset += 1

        return Message(
            message_id=str(uuid.uuid5(_NAMESPACE, f"msg|{self.seed}|{tick}|{agent_id}")),
            sender_id=agent_id,
            recipient_id=recipient,
            tick=tick,
            claims=tuple(claims),
            malformed=malformed,
            tool_error=tool_error,
            retry_count=retry_count,
            token_len=token_len,
        )

    def _build_claim(
        self, agent: Agent, tick: int, index: int, faulty: bool, schema_version: int
    ) -> Claim:
        claim_id = str(
            uuid.uuid5(_NAMESPACE, f"{self.seed}|{tick}|{agent.agent_id}|{index}")
        )
        parent: Claim | None = None
        inbox = list(agent.inbox)

        severity = self.profile.severity
        wants_parent = bool(inbox) and (
            self.rng.random() < self.derive_probability
            or (faulty and severity is Severity.MODERATE)
        )
        if wants_parent:
            parent = inbox[self.rng.randrange(len(inbox))]

        corrupt = False
        if parent is not None:
            payload = self._derive_payload(parent, schema_version)
            corrupt = parent.is_corrupt  # corruption propagates through derivation
        else:
            payload = self._fresh_payload(
                schema_version, prefer_numeric=faulty and severity is Severity.SUBTLE
            )

        if faulty:
            payload, corrupted_here = self._apply_fault(payload, parent, schema_version)
            corrupt = corrupt or corrupted_here

        claim = Claim(
            claim_id=claim_id,
            origin_agent_id=agent.agent_id,
            tick=tick,
            payload=payload,
            is_corrupt=corrupt,
        )
        if parent is not None:
            self.lineage_edges.add(LineageEdge(parent.claim_id, claim_id))
        self.total_claims += 1
        if corrupt:
            self.corrupt_claim_ticks.append(tick)
        return claim

    def _apply_fault(
        self, payload: ClaimPayload, parent: Claim | None, schema_version: int
    ) -> tuple[ClaimPayload, bool]:
        profile = self.profile
        if profile.severity is Severity.OVERT:
            # The whole output stream is untrustworthy once the agent is broken.
            return payload, True
        if profile.severity is Severity.MODERATE:
            if parent is None or self.rng.random() >= profile.conflict_probability:
                return payload, False
            return conflicting_payload(parent.payload, schema_version, self.rng), True
        # SUBTLE
        if payload.value_num is None:
            return payload, False
        shifted = ClaimPayload(
            subject_id=payload.subject_id,
            field_name=payload.field_name,
            value_num=perturb_numeric(payload.value_num, profile, self.rng),
            value_date=payload.value_date,
            value_enum=payload.value_enum,
            schema_version=payload.schema_version,
        )
        return shifted, True

    def _derive_payload(self, parent: Claim, schema_version: int) -> ClaimPayload:
        """Restate a received claim. Usually verbatim; occasionally a real update."""
        source = parent.payload
        value_num = source.value_num
        value_date = source.value_date
        if value_num is not None and self.rng.random() < self.legitimate_update_probability:
            # A genuine revision, large enough that the recipient will reject it.
            value_num = value_num * self.rng.uniform(1.2, 1.6)
        return ClaimPayload(
            subject_id=source.subject_id,
            field_name=source.field_name,
            value_num=value_num,
            value_date=value_date,
            value_enum=source.value_enum,
            schema_version=schema_version,
        )

    def _fresh_payload(self, schema_version: int, prefer_numeric: bool = False) -> ClaimPayload:
        subject = self.subjects[self.rng.randrange(len(self.subjects))]
        pool = NUMERIC_FIELDS if prefer_numeric else ALL_FIELDS
        field_name = pool[self.rng.randrange(len(pool))]
        value_num = value_date = value_enum = None
        if field_name in NUMERIC_FIELDS:
            low, high = self.numeric_ranges.get(field_name, (0.0, 100.0))
            # Interior of the configured band: healthy agents do not trip check 6.
            span = high - low
            value_num = round(self.rng.uniform(low + 0.1 * span, high - 0.1 * span), 4)
        elif field_name in DATE_FIELDS:
            year = self.rng.randint(2024, 2027)
            month = self.rng.randint(1, 12)
            day = self.rng.randint(1, 28)
            value_date = f"{year:04d}-{month:02d}-{day:02d}"
        else:
            options = ENUM_VALUES[field_name]
            value_enum = options[self.rng.randrange(len(options))]
        return ClaimPayload(
            subject_id=subject,
            field_name=field_name,
            value_num=value_num,
            value_date=value_date,
            value_enum=value_enum,
            schema_version=schema_version,
        )

    def _sample_retries(self) -> int:
        draw = self.rng.random()
        if draw < 0.90:
            return 0
        if draw < 0.98:
            return 1
        return 2

    # -- delivery ----------------------------------------------------------

    def _deliver(self, message: Message) -> frozenset[str]:
        """Hand the message to its recipient and report what it mechanically refused.

        Rejection is a typed comparison and nothing more: structural damage, a
        schema mismatch, or a value that disagrees with something the recipient
        already holds for the same (subject_id, field_name) by more than the
        configured tolerance.
        """
        recipient = self.agents[message.recipient_id]
        if not recipient.active:
            return frozenset(claim.claim_id for claim in message.claims)
        if message.malformed or message.tool_error:
            return frozenset(claim.claim_id for claim in message.claims)

        held: dict[tuple[str, str], Claim] = {}
        for claim in recipient.inbox:
            held[(claim.payload.subject_id, claim.payload.field_name)] = claim

        rejected: set[str] = set()
        for claim in message.claims:
            payload = claim.payload
            if payload.schema_version != self.schema_version:
                rejected.add(claim.claim_id)
                continue
            existing = held.get((payload.subject_id, payload.field_name))
            if existing is not None and self._conflicts(existing.payload, payload):
                rejected.add(claim.claim_id)
                continue
            recipient.inbox.append(claim)
            held[(payload.subject_id, payload.field_name)] = claim
        return frozenset(rejected)

    def _conflicts(self, held: ClaimPayload, incoming: ClaimPayload) -> bool:
        if held.value_num is not None and incoming.value_num is not None:
            scale = max(abs(held.value_num), 1e-9)
            return abs(incoming.value_num - held.value_num) / scale > self.tolerance
        if held.value_date is not None and incoming.value_date is not None:
            return held.value_date != incoming.value_date
        if held.value_enum is not None and incoming.value_enum is not None:
            return held.value_enum != incoming.value_enum
        return False
