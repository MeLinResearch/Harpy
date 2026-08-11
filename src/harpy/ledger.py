"""Money. Prices come from configs/pricing.yaml; none are written in Python.

The point of HARPY-SIM is a cost curve, so a stray hard-coded price would
quietly invalidate every result. Prices live in one YAML file, that file ships
unverified, and :mod:`harpy.sweep` refuses to run until someone has replaced the
placeholders with figures read off a provider pricing page.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .types import AuditResult, BudgetLedger

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
        rerun_multiplier: float = 1.0,
        human_review_dollars: float = 25.0,
    ) -> None:
        self.pricing = pricing
        self.rerun_multiplier = float(rerun_multiplier)
        self.human_review_cost = float(human_review_dollars)
        self.ledger = BudgetLedger()
        self._agent_spend_since_restart: dict[str, float] = {}
        self.audits_charged = 0
        self.alerts_charged = 0

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

    def charge_human_review(self) -> float:
        self.ledger.human_review_dollars += self.human_review_cost
        self.alerts_charged += 1
        return self.human_review_cost
