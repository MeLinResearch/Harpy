"""Tier 0: seven mechanical checks over typed fields. Near-free, never semantic.

Tier 0 exists to answer one question cheaply: *is this agent behaving oddly in
ways arithmetic can see?* It is deliberately stupid. It compares typed values,
counts flags, and takes means over rolling windows. It does not read prose,
because it has no prose to read — :class:`~harpy.types.ClaimPayload` has no
free-text field. Every semantic judgement belongs to the sentinel, which costs
real money and is therefore rationed by the sampler.

The seven checks, and nothing else:

1. ``schema_violation``            — schema_version mismatch / missing required field
2. ``malformed_rate``              — ``Message.malformed`` frequency, rolling 50
3. ``tool_error_rate``             — ``Message.tool_error`` frequency, rolling 50
4. ``retry_rate``                  — mean ``retry_count``, rolling 50
5. ``token_len_zscore``            — |z| of ``token_len`` vs the agent's trailing 100
6. ``numeric_range_violation``     — ``value_num`` outside configured per-field bounds
7. ``downstream_rejection_rate``   — frequency a recipient rejects the agent's claims
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .types import Message

CHECK_NAMES: tuple[str, ...] = (
    "schema_violation",
    "malformed_rate",
    "tool_error_rate",
    "retry_rate",
    "token_len_zscore",
    "numeric_range_violation",
    "downstream_rejection_rate",
)


@dataclass(frozen=True)
class Tier0Config:
    window: int = 50
    token_window: int = 100
    token_zscore_cap: float = 4.0
    retry_normalizer: float = 3.0
    expected_schema_version: int = 3
    cost_per_message_dollars: float = 0.0000001
    weights: dict[str, float] = field(default_factory=dict)
    numeric_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict, tier0_price: float) -> Tier0Config:
        tel = cfg["telemetry"]
        ranges = {k: (float(v[0]), float(v[1])) for k, v in tel["numeric_ranges"].items()}
        weights = {str(k): float(v) for k, v in tel["weights"].items()}
        missing = set(CHECK_NAMES) - set(weights)
        extra = set(weights) - set(CHECK_NAMES)
        if missing or extra:
            raise ValueError(
                f"telemetry.weights must name exactly the seven checks; {missing=} {extra=}"
            )
        return cls(
            window=int(tel["window"]),
            token_window=int(tel["token_window"]),
            token_zscore_cap=float(tel["token_zscore_cap"]),
            retry_normalizer=float(tel.get("retry_normalizer", 3.0)),
            expected_schema_version=int(cfg["simulation"]["schema_version"]),
            cost_per_message_dollars=float(tier0_price),
            weights=weights,
            numeric_ranges=ranges,
        )


class _AgentWindow:
    __slots__ = (
        "malformed",
        "tool_error",
        "retries",
        "token_lens",
        "schema_flags",
        "range_flags",
        "rejections",
        "zscore",
    )

    def __init__(self, window: int, token_window: int) -> None:
        self.malformed: deque[int] = deque(maxlen=window)
        self.tool_error: deque[int] = deque(maxlen=window)
        self.retries: deque[int] = deque(maxlen=window)
        self.token_lens: deque[int] = deque(maxlen=token_window)
        self.schema_flags: deque[float] = deque(maxlen=window)
        self.range_flags: deque[float] = deque(maxlen=window)
        self.rejections: deque[float] = deque(maxlen=window)
        self.zscore: float = 0.0


def _mean(values) -> float:
    seq = list(values)
    if not seq:
        return 0.0
    return sum(seq) / len(seq)


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


class Tier0Telemetry:
    """Rolling Tier 0 state for every agent in the mesh.

    Costs ``cost_per_message_dollars`` per observed message and nothing else;
    the caller charges that to :class:`~harpy.types.BudgetLedger.tier0_dollars`.
    """

    def __init__(self, config: Tier0Config) -> None:
        self.config = config
        self._agents: dict[str, _AgentWindow] = {}
        self.messages_observed = 0

    # -- ingestion ---------------------------------------------------------

    def _window_for(self, agent_id: str) -> _AgentWindow:
        win = self._agents.get(agent_id)
        if win is None:
            win = _AgentWindow(self.config.window, self.config.token_window)
            self._agents[agent_id] = win
        return win

    def observe(self, message: Message, rejected_claim_ids: frozenset[str]) -> float:
        """Fold one message into the sender's rolling state. Returns its Tier 0 cost.

        ``rejected_claim_ids`` is supplied by the platform: it is the set of the
        message's claims the recipient mechanically refused (check 7). It is an
        observable, not ground truth — recipients reject on typed conflict and
        structural damage, and are wrong about corruption all the time.
        """
        win = self._window_for(message.sender_id)
        win.malformed.append(1 if message.malformed else 0)
        win.tool_error.append(1 if message.tool_error else 0)
        win.retries.append(int(message.retry_count))

        # Check 5 compares this message against the agent's own trailing history,
        # so the z-score is taken before the new length joins the window.
        win.zscore = self._token_zscore(win, int(message.token_len))
        win.token_lens.append(int(message.token_len))

        claims = message.claims
        n_claims = len(claims)
        schema_bad = 0
        range_bad = 0
        numeric_claims = 0
        for claim in claims:
            payload = claim.payload
            if payload.schema_version != self.config.expected_schema_version:
                schema_bad += 1
            elif (
                payload.value_num is None
                and payload.value_date is None
                and payload.value_enum is None
            ):
                # A claim with no typed value is a missing required field.
                schema_bad += 1
            if payload.value_num is not None:
                numeric_claims += 1
                bounds = self.config.numeric_ranges.get(payload.field_name)
                if bounds is not None and not (bounds[0] <= payload.value_num <= bounds[1]):
                    range_bad += 1

        win.schema_flags.append(schema_bad / n_claims if n_claims else 0.0)
        win.range_flags.append(range_bad / numeric_claims if numeric_claims else 0.0)
        win.rejections.append(len(rejected_claim_ids) / n_claims if n_claims else 0.0)

        self.messages_observed += 1
        return self.config.cost_per_message_dollars

    # -- the seven checks --------------------------------------------------

    def _token_zscore(self, win: _AgentWindow, token_len: int) -> float:
        history = win.token_lens
        n = len(history)
        if n < 5:
            return 0.0
        # Plain arithmetic rather than `statistics`: this runs once per message
        # and the exact-ratio machinery in the stdlib dominated the whole run.
        mean = sum(history) / n
        variance = sum((value - mean) ** 2 for value in history) / n
        if variance <= 0.0:
            return 0.0
        z = abs((token_len - mean) / (variance**0.5))
        return _clamp01(z / self.config.token_zscore_cap)

    def components(self, agent_id: str) -> dict[str, float]:
        """All seven checks for one agent, each already normalised into [0, 1]."""
        win = self._agents.get(agent_id)
        if win is None:
            return dict.fromkeys(CHECK_NAMES, 0.0)
        return {
            "schema_violation": _clamp01(_mean(win.schema_flags)),
            "malformed_rate": _clamp01(_mean(win.malformed)),
            "tool_error_rate": _clamp01(_mean(win.tool_error)),
            "retry_rate": _clamp01(_mean(win.retries) / self.config.retry_normalizer),
            "token_len_zscore": _clamp01(win.zscore),
            "numeric_range_violation": _clamp01(_mean(win.range_flags)),
            "downstream_rejection_rate": _clamp01(_mean(win.rejections)),
        }

    def score(self, agent_id: str) -> float:
        """Configured weighted sum of the seven checks, in [0, 1]."""
        parts = self.components(agent_id)
        total = sum(self.config.weights[name] * parts[name] for name in CHECK_NAMES)
        weight_sum = sum(self.config.weights[name] for name in CHECK_NAMES)
        if weight_sum <= 0.0:
            return 0.0
        return _clamp01(total / weight_sum)

    def scores(self, agent_ids) -> dict[str, float]:
        return {agent_id: self.score(agent_id) for agent_id in agent_ids}

    def reset_agent(self, agent_id: str) -> None:
        """Drop an agent's history — used after ``restart_clean``."""
        self._agents.pop(agent_id, None)
