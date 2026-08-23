"""Money. Prices come from configs/pricing.yaml; none are written in Python.

The point of HARPY-SIM is a cost curve, so a stray hard-coded price would
quietly invalidate every result. Prices live in one YAML file, that file ships
unverified, and every entry point that can produce a number — :func:`run_simulation`
as well as :mod:`harpy.sweep` — refuses to run until someone has replaced the
placeholders with figures read off a provider pricing page.

The worker side is a *registry*, not a single entry: ``worker_models`` maps a
name to a price, so sweeping the worker model is adding YAML entries rather than
editing Python. A file carrying the older singular ``worker_model:`` block is
read as a one-entry registry under the name ``default``, which is what keeps the
shipped placeholder file loadable without being edited.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .types import AuditResult, BudgetLedger

MINUTES_PER_HOUR = 60.0

#: The name a legacy singular ``worker_model:`` block is registered under, and
#: the name :class:`~harpy.simulation.RunSpec` falls back to when nobody has said
#: which worker model a run is priced at.
DEFAULT_WORKER_MODEL_NAME = "default"


class PricingNotVerifiedError(RuntimeError):
    """Raised when anything that produces a number is handed placeholder prices.

    Lives here rather than in :mod:`harpy.sweep` because the guard is now
    enforced one level down, at :func:`~harpy.simulation.run_simulation`: a
    single run costed off placeholders is exactly as misleading as a sweep of
    them, and a guard that only the sweep path honours is a guard that the next
    caller forgets.
    """


class PricingEntryMissingError(KeyError):
    """Raised when a run names a worker model the pricing file does not define.

    Deliberately loud and deliberately not a fallback: silently substituting some
    other tier's price would produce a cost curve labelled with a model that did
    not pay for it.
    """

    def __str__(self) -> str:  # KeyError repr()s its argument; we want the text
        return self.args[0] if self.args else ""


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
    """A worker-model registry, one sentinel, and whether any of it may be reported.

    ``worker_models`` is keyed by the name the sweep's ``worker_model`` axis
    uses. The sentinel is deliberately singular: the experiment varies the worker
    tier against one fixed cross-family detector, and a sentinel axis would make
    the two moving parts inseparable in the results.
    """

    worker_models: dict[str, ModelPrice]
    sentinel_model: ModelPrice
    tier0_cost_per_message_dollars: float
    verified: bool
    path: str
    #: Insertion order of ``worker_models``, which is YAML order, which is the
    #: order the tiers are meant to be read in (budget, mid, flagship).
    worker_model_order: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not self.worker_models:
            raise PricingEntryMissingError(
                f"{self.path} defines no worker models — expected a 'worker_models:' "
                "mapping or a singular 'worker_model:' block"
            )
        if not self.worker_model_order:
            object.__setattr__(self, "worker_model_order", tuple(self.worker_models))

    # -- the worker registry ----------------------------------------------

    def worker_model_names(self) -> tuple[str, ...]:
        """Every worker tier this file defines, in YAML order.

        This is what the sweep's ``worker_model`` axis defaults to, and it is the
        whole of the "adding a tier needs no code change" contract: add an entry
        to the YAML and the grid grows.
        """
        return tuple(self.worker_model_order)

    def worker_price(self, name: str) -> ModelPrice:
        try:
            return self.worker_models[name]
        except KeyError:
            available = ", ".join(self.worker_model_names()) or "(none)"
            raise PricingEntryMissingError(
                f"{self.path} has no worker model named {name!r}. Defined: {available}. "
                "Add it to the pricing file with its own source URL and access date; "
                "prices are never written in Python."
            ) from None

    def default_worker_model_name(self) -> str:
        """The tier to use when a caller did not name one.

        Unambiguous only while the file defines a single tier, or defines one
        called ``default``. With three tiers and no default the answer is a
        choice about what to price, so it is raised back to the caller rather
        than guessed at.
        """
        names = self.worker_model_names()
        if len(names) == 1:
            return names[0]
        if DEFAULT_WORKER_MODEL_NAME in self.worker_models:
            return DEFAULT_WORKER_MODEL_NAME
        raise PricingEntryMissingError(
            f"{self.path} defines {len(names)} worker models ({', '.join(names)}) and none "
            f"named {DEFAULT_WORKER_MODEL_NAME!r}; name one explicitly"
        )

    @property
    def worker_model(self) -> ModelPrice:
        """The sole worker model. Raises once there is more than one.

        Kept for callers that predate the registry. It cannot silently pick a
        tier, because which tier a cost came from is the thing the worker axis
        exists to distinguish.
        """
        return self.worker_price(self.default_worker_model_name())

    # -- verification ------------------------------------------------------

    def priced_entries(self) -> tuple[tuple[str, ModelPrice], ...]:
        """Every entry subject to the provider-source requirement, labelled.

        Tier 0 is arithmetic, not a model call, so its unit cost is a spec-fixed
        estimate and is exempt.
        """
        entries = [
            (f"worker_models.{name}", self.worker_models[name])
            for name in self.worker_model_names()
        ]
        entries.append(("sentinel_model", self.sentinel_model))
        return tuple(entries)

    def verification_problems(self) -> list[str]:
        """Empty iff this pricing file may back a reported run."""
        problems: list[str] = []
        for label, price in self.priced_entries():
            if not price.source.strip():
                problems.append(f"{label}.source is empty — paste the provider pricing page URL")
            if not price.accessed.strip():
                problems.append(f"{label}.accessed is empty — paste the date you read that page")
        if not self.verified:
            problems.append(
                "verified is false — flip it only once every entry carries a real URL and date"
            )
        return problems

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "verified": self.verified,
            "worker_models": {
                name: self.worker_models[name].as_dict() for name in self.worker_model_names()
            },
            "sentinel_model": self.sentinel_model.as_dict(),
            "tier0_cost_per_message_dollars": self.tier0_cost_per_message_dollars,
        }


def require_verified_pricing(
    pricing: Pricing, allow_unverified: bool = False, context: str = "run"
) -> list[str]:
    """Return this file's problems, raising unless the caller waived them.

    The waiver exists for smoke runs and CI only. Every run produced under it is
    stamped ``pricing_verified: false`` in its results JSON and its plot is
    watermarked, so an unverified curve cannot be mistaken for a reported one.
    """
    problems = pricing.verification_problems()
    if problems and not allow_unverified:
        raise PricingNotVerifiedError(
            f"refusing to {context} on unverified pricing:\n  - "
            + "\n  - ".join(problems)
            + f"\nEdit {pricing.path}, or pass --allow-unverified-pricing for a smoke run "
            "whose numbers will be stamped unverified."
        )
    return problems


def _price_from_entry(entry: dict, label: str) -> ModelPrice:
    if not isinstance(entry, dict):
        raise ValueError(f"pricing entry {label!r} must be a mapping, got {type(entry).__name__}")
    return ModelPrice(
        input_per_mtok=float(entry.get("input_per_mtok", 0.0)),
        output_per_mtok=float(entry.get("output_per_mtok", 0.0)),
        source=str(entry.get("source", "")),
        accessed=str(entry.get("accessed", "")),
    )


def _model_price(raw: dict, name: str) -> ModelPrice:
    try:
        entry = raw[name]
    except KeyError as exc:  # pragma: no cover - config typo path
        raise ValueError(f"pricing file is missing the {name!r} entry") from exc
    return _price_from_entry(entry, name)


def _worker_models(raw: dict, path: str) -> dict[str, ModelPrice]:
    """Read the worker registry, accepting either shape.

    ``worker_models:`` is the shape the worker axis is swept over. A file with
    only the older singular ``worker_model:`` block is registered as a one-entry
    registry so it keeps loading unchanged; defining both is a contradiction
    about what the run is priced at, so it is refused rather than resolved.
    """
    plural = raw.get("worker_models")
    singular = raw.get("worker_model")
    if plural is not None and singular is not None:
        raise ValueError(
            f"{path} defines both 'worker_models' and 'worker_model'; keep one. "
            "'worker_models' is the registry the worker axis sweeps."
        )
    if plural is not None:
        if not isinstance(plural, dict) or not plural:
            raise ValueError(
                f"{path}: 'worker_models' must be a non-empty mapping of name -> price"
            )
        return {
            str(name): _price_from_entry(entry, f"worker_models.{name}")
            for name, entry in plural.items()
        }
    if singular is None:
        raise ValueError(
            f"{path} is missing worker pricing — expected a 'worker_models:' mapping "
            "or a singular 'worker_model:' block"
        )
    return {DEFAULT_WORKER_MODEL_NAME: _price_from_entry(singular, "worker_model")}


def load_pricing(path: str | Path) -> Pricing:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    tier0 = raw.get("tier0", {}) or {}
    workers = _worker_models(raw, str(path))
    return Pricing(
        worker_models=workers,
        sentinel_model=_model_price(raw, "sentinel_model"),
        tier0_cost_per_message_dollars=float(tier0.get("cost_per_message_dollars", 0.0000001)),
        verified=bool(raw.get("verified", False)),
        path=str(path),
        worker_model_order=tuple(workers),
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
        worker_model_name: str | None = None,
    ) -> None:
        self.pricing = pricing
        #: Resolved once, here, so a run that names a missing tier fails before
        #: it has simulated anything rather than at the first charge.
        self.worker_model_name = (
            worker_model_name
            if worker_model_name is not None
            else pricing.default_worker_model_name()
        )
        self.worker_price = pricing.worker_price(self.worker_model_name)
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
        cost = self.worker_price.cost(input_tokens, output_tokens)
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
        """Discard the agent's work-in-progress and pay to redo it.

        Charges the total only. Whether this isolation was of the actual fault
        source decides which half of the split it lands in, and that is ground
        truth — see :meth:`attribute_isolation`.
        """
        discarded = self.agent_spend_since_restart(agent_id)
        rerun = discarded * self.rerun_multiplier
        self.ledger.discarded_work_dollars += discarded
        self.ledger.rerun_work_dollars += rerun
        return discarded, rerun

    def attribute_isolation(self, discarded: float, rerun: float, was_faulty: bool) -> None:
        """File an already-charged isolation under true or false, for the split.

        Called by the world (:mod:`harpy.simulation`), never by
        :mod:`harpy.response`: ``was_faulty`` is ground truth, and the guard in
        :mod:`harpy.types` exists precisely to stop the response path learning
        it. The totals are untouched here — this only records how much of the
        discard-and-rerun bill was spent on agents that were not the fault.
        """
        if was_faulty:
            return
        self.ledger.discarded_work_dollars_from_false_isolations += discarded
        self.ledger.rerun_work_dollars_from_false_isolations += rerun

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
