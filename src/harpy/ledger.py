"""Money. Prices come from configs/pricing.yaml; none are written in Python.

The point of HARPY-SIM is a cost curve, so a stray hard-coded price would
quietly invalidate every result. Prices live in one YAML file, that file ships
unverified, and :mod:`harpy.sweep` refuses to run until someone has replaced the
placeholders with figures read off a provider pricing page.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from .types import AuditResult, BudgetLedger

MINUTES_PER_HOUR = 60.0

#: Entries subject to the provider-source requirement. Tier 0 is arithmetic, not
#: a model call, so its unit cost is a spec-fixed estimate and is exempt.
PRICED_MODELS: tuple[str, ...] = ("worker_model", "sentinel_model")


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float
    source: str
    accessed: str

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        ) / 1_000_000.0

    def as_dict(self) -> dict:
        return {
            "input_per_mtok": self.input_per_mtok,
            "output_per_mtok": self.output_per_mtok,
            "source": self.source,
            "accessed": self.accessed,
        }


@dataclass(frozen=True)
class Pricing:
    worker_model: ModelPrice
    sentinel_model: ModelPrice
    tier0_cost_per_message_dollars: float
    verified: bool
    path: str

    def verification_problems(self) -> list[str]:
        """Empty iff this pricing file may back a reported run."""
        problems: list[str] = []
        for name in PRICED_MODELS:
            price: ModelPrice = getattr(self, name)
            if not price.source.strip():
                problems.append(f"{name}.source is empty — paste the provider pricing page URL")
            if not price.accessed.strip():
                problems.append(f"{name}.accessed is empty — paste the date you read that page")
        if not self.verified:
            problems.append(
                "verified is false — flip it only once both entries carry a real URL and date"
            )
        return problems

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "verified": self.verified,
            "worker_model": self.worker_model.as_dict(),
            "sentinel_model": self.sentinel_model.as_dict(),
            "tier0_cost_per_message_dollars": self.tier0_cost_per_message_dollars,
        }


def _model_price(raw: dict, name: str) -> ModelPrice:
    try:
        entry = raw[name]
    except KeyError as exc:  # pragma: no cover - config typo path
        raise ValueError(f"pricing file is missing the {name!r} entry") from exc
    return ModelPrice(
        input_per_mtok=float(entry.get("input_per_mtok", 0.0)),
        output_per_mtok=float(entry.get("output_per_mtok", 0.0)),
        source=str(entry.get("source", "")),
        accessed=str(entry.get("accessed", "")),
    )


def load_pricing(path: str | Path) -> Pricing:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tier0 = raw.get("tier0", {}) or {}
    return Pricing(
        worker_model=_model_price(raw, "worker_model"),
        sentinel_model=_model_price(raw, "sentinel_model"),
        tier0_cost_per_message_dollars=float(tier0.get("cost_per_message_dollars", 0.0000001)),
        verified=bool(raw.get("verified", False)),
        path=str(path),
    )


@dataclass(frozen=True)
class HumanReviewConfig:
    """Analyst time, in minutes and a rate, rather than dollars per alert.

    Every quantity here is dimensionless with respect to fleet size, which the
    flat ``human_review_dollars`` constant this replaces was not: 25 dollars is
    16% of a 30-agent worker bill and 0.016% of a 30,000-agent one, so the old
    constant silently encoded the size of the simulated slice into the overhead
    ratio and put the 10% kill ceiling out of reach the moment an alert fired.
    """

    analyst_cost_per_hour: float
    triage_minutes: float
    investigation_minutes: float
    escalation_rate: float
    batch_window_ticks: int

    def __post_init__(self) -> None:
        for name in ("analyst_cost_per_hour", "triage_minutes", "investigation_minutes"):
            if getattr(self, name) < 0.0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if not 0.0 <= self.escalation_rate <= 1.0:
            raise ValueError(f"escalation_rate must be in [0, 1], got {self.escalation_rate}")
        if self.batch_window_ticks < 0:
            raise ValueError(f"batch_window_ticks must be >= 0, got {self.batch_window_ticks}")

    @classmethod
    def from_config(cls, cfg: dict) -> HumanReviewConfig:
        review = cfg["human_review"]
        return cls(
            analyst_cost_per_hour=float(review["analyst_cost_per_hour"]),
            triage_minutes=float(review["triage_minutes"]),
            investigation_minutes=float(review["investigation_minutes"]),
            escalation_rate=float(review["escalation_rate"]),
            batch_window_ticks=int(review["batch_window_ticks"]),
        )

    def triage_dollars(self) -> float:
        """Charged for every review. Someone looks at every alert."""
        return self.triage_minutes / MINUTES_PER_HOUR * self.analyst_cost_per_hour

    def investigation_dollars(self) -> float:
        """Charged only for the reviews that escalate."""
        return self.investigation_minutes / MINUTES_PER_HOUR * self.analyst_cost_per_hour

    def expected_dollars_per_review(self) -> float:
        """(triage + escalation_rate * investigation) / 60 * rate.

        The *expectation* only. Escalation is drawn per review from the run's
        RNG rather than averaged in, because a run with two alerts does not pay
        a quarter of an investigation twice — it pays for zero, one or two of
        them, and the variance is part of what the sweep is measuring.
        """
        return (
            self.triage_minutes + self.escalation_rate * self.investigation_minutes
        ) / MINUTES_PER_HOUR * self.analyst_cost_per_hour


class CostAccountant:
    """Owns the :class:`BudgetLedger` and the per-agent spend clocks behind it.

    ``discarded_work_dollars`` is the real reason this class exists: throwing an
    agent away costs whatever that agent has spent since its last clean restart,
    which grows over a run. Charging a flat constant instead would make late
    isolations look as cheap as early ones and flatter the architecture.
    """

    def __init__(
        self,
        pricing: Pricing,
        review: HumanReviewConfig,
        rerun_multiplier: float = 1.0,
        review_rng: random.Random | None = None,
        fleet_scale: float = 1.0,
    ) -> None:
        self.pricing = pricing
        self.review = review
        self.rerun_multiplier = float(rerun_multiplier)
        self._review_rng = review_rng if review_rng is not None else random.Random(0)
        self.ledger = BudgetLedger(fleet_scale=float(fleet_scale))
        self._agent_spend_since_restart: dict[str, float] = {}
        #: Tick at which each agent's currently-open review was opened.
        self._review_opened_tick: dict[str, int] = {}
        self.audits_charged = 0
        self.alerts_charged = 0
        self.alerts_batched = 0
        self.reviews_charged = 0
        self.escalations_charged = 0

    # -- worker side -------------------------------------------------------

    def charge_worker(self, agent_id: str, input_tokens: int, output_tokens: int) -> float:
        cost = self.pricing.worker_model.cost(input_tokens, output_tokens)
        self.ledger.worker_dollars += cost
        self._agent_spend_since_restart[agent_id] = (
            self._agent_spend_since_restart.get(agent_id, 0.0) + cost
        )
        return cost

    def agent_spend_since_restart(self, agent_id: str) -> float:
        return self._agent_spend_since_restart.get(agent_id, 0.0)

    def reset_agent_spend(self, agent_id: str) -> None:
        """A clean restart resets the clock; future discards are cheaper again."""
        self._agent_spend_since_restart[agent_id] = 0.0

    # -- oversight side ----------------------------------------------------

    def charge_tier0(self, dollars: float) -> float:
        self.ledger.tier0_dollars += dollars
        return dollars

    def audit_cost(self, input_tokens: int, output_tokens: int) -> float:
        """What one sentinel audit costs. The sampler divides its budget by this."""
        return self.pricing.sentinel_model.cost(input_tokens, output_tokens)

    def charge_audit(self, result: AuditResult) -> float:
        cost = self.audit_cost(result.input_tokens, result.output_tokens)
        self.ledger.sentinel_dollars += cost
        self.audits_charged += 1
        return cost

    def charge_isolation(self, agent_id: str) -> tuple[float, float]:
        """Discard the agent's work-in-progress and pay to redo it."""
        discarded = self.agent_spend_since_restart(agent_id)
        rerun = discarded * self.rerun_multiplier
        self.ledger.discarded_work_dollars += discarded
        self.ledger.rerun_work_dollars += rerun
        return discarded, rerun

    def charge_human_review(self, agent_id: str, tick: int) -> tuple[float, bool]:
        """Charge one alert's worth of analyst time, batched per agent.

        Alerts about the same agent inside ``batch_window_ticks`` of the review
        that is already open collapse into that review and cost nothing extra:
        a person handed the same agent twice in five minutes reads one case, not
        two. The window is anchored on the tick the open review started, so a
        burst longer than the window does open a second one.

        Returns ``(dollars charged, whether this alert opened a new review)``.
        """
        self.alerts_charged += 1
        opened = self._review_opened_tick.get(agent_id)
        if opened is not None and tick - opened < self.review.batch_window_ticks:
            self.alerts_batched += 1
            return 0.0, False

        self._review_opened_tick[agent_id] = tick
        self.reviews_charged += 1
        cost = self.review.triage_dollars()
        self.ledger.triage_dollars += cost
        # Drawn unconditionally, so the stream position depends on the number of
        # reviews and not on the configured rate: a run with escalation_rate 0
        # and one with 0.25 stay comparable draw for draw.
        escalated = self._review_rng.random() < self.review.escalation_rate
        if escalated:
            investigation = self.review.investigation_dollars()
            self.ledger.investigation_dollars += investigation
            self.escalations_charged += 1
            cost += investigation
        return cost, True
