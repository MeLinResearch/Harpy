"""Core data structures and the ground-truth isolation guard.

Two fields in this module are ground truth: ``Claim.is_corrupt`` and
``LineagePair.ground_truth``. They exist so runs can be *scored*. HARPY's own
detection path must never read them — if it could, phase 1 would be measuring
an oracle rather than an architecture.

The guard is mechanical, not a convention: both fields are exposed through
property accessors that inspect the call stack and raise
:class:`GroundTruthLeakError` when the access originates inside a
HARPY-side module (``telemetry``, ``sentinel``, ``sampler``, ``suspicion``,
``response``, ``alert_budget``). ``MockSentinel`` is the single whitelisted
exception: it
simulates a detector rather than implementing one, so reading ground truth is
the whole of its job. ``tests/test_isolation_guard.py`` pins this behaviour.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum


class GroundTruthLeakError(RuntimeError):
    """Raised when a HARPY-side module reaches for ground truth."""


class Severity(str, Enum):
    OVERT = "OVERT"
    MODERATE = "MODERATE"
    SUBTLE = "SUBTLE"


class Arm(str, Enum):
    NONE = "NONE"
    TIER0_ONLY = "TIER0_ONLY"
    RANDOM = "RANDOM"
    DIRECTED = "DIRECTED"
    HYBRID = "HYBRID"


# --------------------------------------------------------------------------
# Isolation guard
# --------------------------------------------------------------------------

#: Modules that make up HARPY's detection path. Nothing in here may read
#: ground truth, directly or through a helper it calls.
GUARDED_MODULES = frozenset(
    {
        "telemetry.py",
        "sentinel.py",
        "sampler.py",
        "suspicion.py",
        "response.py",
        "alert_budget.py",
    }
)

#: Qualified-name prefixes that are exempt, keyed by module file name.
WHITELISTED_QUALNAMES: dict[str, tuple[str, ...]] = {"sentinel.py": ("MockSentinel",)}

_THIS_FILE = os.path.basename(__file__)
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


#: Memo for :func:`_module_name`. The guard runs on every ground-truth read and
#: walks the whole stack, so this resolves the same handful of code-object
#: filenames tens of thousands of times per run; only absolute paths are cached,
#: since a relative one's answer depends on the current directory.
_MODULE_NAME_CACHE: dict[str, str | None] = {}


def _module_name(filename: str) -> str | None:
    """File name if the frame belongs to this package, else None.

    Matching on the full directory rather than the bare file name keeps an
    unrelated third-party ``response.py`` somewhere on the stack from being
    mistaken for HARPY's.
    """
    cached = _MODULE_NAME_CACHE.get(filename, _MODULE_NAME_CACHE)
    if cached is not _MODULE_NAME_CACHE:
        return cached  # type: ignore[return-value]

    path = os.path.abspath(filename)
    name = os.path.basename(path) if os.path.dirname(path) == _PACKAGE_DIR else None
    if os.path.isabs(filename):
        _MODULE_NAME_CACHE[filename] = name
    return name


def _check_caller(owner: str, attr: str) -> None:
    """Walk outwards; the first guarded-or-whitelisted frame decides.

    Frames from unguarded modules are transparent, so an attempt to launder an
    access through a helper (``telemetry`` -> ``metrics`` -> ``is_corrupt``)
    still trips the guard.
    """
    frame = sys._getframe(2)
    while frame is not None:
        name = _module_name(frame.f_code.co_filename)
        if name is None or name == _THIS_FILE:
            frame = frame.f_back
            continue
        qualname = getattr(frame.f_code, "co_qualname", frame.f_code.co_name)
        for prefix in WHITELISTED_QUALNAMES.get(name, ()):
            if qualname.startswith(prefix):
                return
        if name in GUARDED_MODULES:
            raise GroundTruthLeakError(
                f"{name}:{qualname} tried to read {owner}.{attr}. Ground truth is for "
                "scoring only; HARPY-side modules must work from observables."
            )
        frame = frame.f_back


def _install_guard(cls: type, attr: str) -> None:
    """Replace a dataclass field with a stack-checking property of the same name."""

    def getter(self, _attr: str = attr, _owner: str = cls.__name__):
        _check_caller(_owner, _attr)
        return self.__dict__[_attr]

    def setter(self, value, _attr: str = attr) -> None:
        store = object.__getattribute__(self, "__dict__")
        if _attr in store:  # frozen: __init__ writes once, nobody rewrites
            raise AttributeError(f"cannot assign to field {_attr!r}")
        store[_attr] = value

    setattr(cls, attr, property(getter, setter))


# --------------------------------------------------------------------------
# Claims and messages
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimPayload:
    """Typed claim body. Tier 0 compares these fields and nothing else.

    There is deliberately no free-text field anywhere in a claim: Tier 0 must be
    mechanical, so it must have nothing semantic to reach for.
    """

    subject_id: str
    field_name: str
    value_num: float | None
    value_date: str | None  # ISO 8601
    value_enum: str | None
    schema_version: int

    def as_dict(self) -> dict:
        return {
            "subject_id": self.subject_id,
            "field_name": self.field_name,
            "value_num": self.value_num,
            "value_date": self.value_date,
            "value_enum": self.value_enum,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class Claim:
    claim_id: str  # uuid5 from (seed, tick, agent_id, index)
    origin_agent_id: str
    tick: int
    payload: ClaimPayload
    # GROUND TRUTH. Guarded — see module docstring. Excluded from eq/repr so it
    # cannot leak through comparison or logging either.
    is_corrupt: bool = field(compare=False, repr=False, default=False)


_install_guard(Claim, "is_corrupt")


@dataclass(frozen=True)
class Message:
    message_id: str
    sender_id: str
    recipient_id: str
    tick: int
    claims: tuple[Claim, ...]
    malformed: bool
    tool_error: bool
    retry_count: int
    token_len: int


# --------------------------------------------------------------------------
# Lineage
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageEdge:
    parent_claim_id: str
    child_claim_id: str


@dataclass(frozen=True)
class LineagePair:
    # GROUND TRUTH. Guarded. Scoring only.
    ground_truth: frozenset[LineageEdge] = field(compare=False, repr=False)
    # The lossy projection HARPY actually gets to reason over.
    observable: frozenset[LineageEdge] = frozenset()


_install_guard(LineagePair, "ground_truth")


# --------------------------------------------------------------------------
# Cost and audits
# --------------------------------------------------------------------------


#: Oversight components that are per-agent: a fleet ``fleet_scale`` times the
#: size of the simulated slice runs that many times as many audits, Tier 0
#: passes, discards and reruns, so each of these scales linearly. Worker spend
#: scales for the same reason and is listed separately only because it is the
#: denominator of the overhead ratio rather than a part of its numerator.
PER_AGENT_OVERHEAD_COMPONENTS: tuple[str, ...] = (
    "sentinel_dollars",
    "tier0_dollars",
    "discarded_work_dollars",
    "rerun_work_dollars",
)

#: The two components that do NOT scale with the fleet. A human reviews the
#: alerts HARPY actually raises, and the alert count is a property of the
#: simulated slice, not of how many agents the slice stands in for. This
#: asymmetry is the whole point of sweeping ``fleet_scale``: it is what makes
#: review cost dominant at scale 1 and negligible at scale 1000.
REVIEW_COMPONENTS: tuple[str, ...] = ("triage_dollars", "investigation_dollars")

#: The false-isolation share of two of the per-agent components. These are
#: *parts of* ``discarded_work_dollars`` and ``rerun_work_dollars``, not extra
#: components: adding them to the overhead would double-count them. They exist
#: because "what did this architecture spend throwing away innocent agents"
#: is a different question from "what did containment cost", and a single
#: blended discard figure answers neither.
FALSE_ISOLATION_SPLIT_COMPONENTS: tuple[str, ...] = (
    "discarded_work_dollars_from_false_isolations",
    "rerun_work_dollars_from_false_isolations",
)


@dataclass
class BudgetLedger:
    """Six oversight components plus the worker spend they are measured against.

    Every field holds the **slice** figure — what the simulated mesh actually
    spent. The fleet-level figure is the slice times ``fleet_scale`` for the
    four per-agent components (and for workers), and the slice unchanged for the
    two review components.
    """

    worker_dollars: float = 0.0
    sentinel_dollars: float = 0.0
    tier0_dollars: float = 0.0
    discarded_work_dollars: float = 0.0
    rerun_work_dollars: float = 0.0
    triage_dollars: float = 0.0
    investigation_dollars: float = 0.0
    #: Portions of the two fields above, not additions to them. Filled by
    #: :meth:`~harpy.ledger.CostAccountant.attribute_isolation` from the world's
    #: side of the seam, because the true/false split is ground truth.
    discarded_work_dollars_from_false_isolations: float = 0.0
    rerun_work_dollars_from_false_isolations: float = 0.0
    #: How many times larger the fleet is than the simulated slice.
    fleet_scale: float = 1.0

    # -- slice-level views -------------------------------------------------

    def per_agent_overhead_dollars(self) -> float:
        """Slice cost of the four components that scale with the fleet."""
        return sum(getattr(self, name) for name in PER_AGENT_OVERHEAD_COMPONENTS)

    def review_dollars(self) -> float:
        """Triage plus investigation. Identical at every fleet scale."""
        return sum(getattr(self, name) for name in REVIEW_COMPONENTS)

    # -- the true/false isolation split ------------------------------------

    def discarded_work_dollars_from_true_isolations(self) -> float:
        """The rest of the discard bill: agents that really were the fault."""
        return self.discarded_work_dollars - self.discarded_work_dollars_from_false_isolations

    def rerun_work_dollars_from_true_isolations(self) -> float:
        return self.rerun_work_dollars - self.rerun_work_dollars_from_false_isolations

    def discard_rerun_dollars_from_false_isolations(self) -> float:
        """Everything a false isolation costs in wasted and repeated work.

        Not the whole cost of a false positive — the human review it triggers is
        in the review components, which do not scale with the fleet and so
        cannot be added in here without mixing two different scaling laws.
        """
        return (
            self.discarded_work_dollars_from_false_isolations
            + self.rerun_work_dollars_from_false_isolations
        )

    def discard_rerun_dollars_from_true_isolations(self) -> float:
        return (
            self.discarded_work_dollars_from_true_isolations()
            + self.rerun_work_dollars_from_true_isolations()
        )

    # -- fleet-level views -------------------------------------------------

    def fleet_component(self, name: str) -> float:
        """One component at fleet scale, scaled iff it is a per-agent component."""
        value = getattr(self, name)
        if name in REVIEW_COMPONENTS:
            return value
        return value * self.fleet_scale

    def fleet_worker_dollars(self) -> float:
        return self.worker_dollars * self.fleet_scale

    def incremental(self) -> float:
        """Everything the oversight layer costs, i.e. everything but the workers."""
        return self.per_agent_overhead_dollars() * self.fleet_scale + self.review_dollars()

    def overhead_pct(self) -> float:
        """Incremental cost as a fraction of worker-fleet cost.

        Zero worker spend means there is nothing to be an overhead *of*; the
        ratio is reported as 0.0 rather than raising or returning infinity.
        """
        worker = self.fleet_worker_dollars()
        if worker <= 0.0:
            return 0.0
        return self.incremental() / worker

    def review_cost_share_of_overhead(self) -> float:
        """Fraction of incremental cost that is human review.

        Falls as ``1 / fleet_scale`` by construction, which is the finding the
        fleet_scale sweep exists to make visible.
        """
        incremental = self.incremental()
        if incremental <= 0.0:
            return 0.0
        return self.review_dollars() / incremental

    def as_dict(self) -> dict:
        """Fleet-level dollars, plus the scale they were computed at."""
        out = {
            "fleet_scale": self.fleet_scale,
            "worker_dollars": self.fleet_worker_dollars(),
        }
        for name in PER_AGENT_OVERHEAD_COMPONENTS + REVIEW_COMPONENTS:
            out[name] = self.fleet_component(name)
        # The split, at the same fleet scale as the totals it partitions. Both
        # halves are per-agent, so both scale.
        for name in FALSE_ISOLATION_SPLIT_COMPONENTS:
            out[name] = self.fleet_component(name)
        out["discarded_work_dollars_from_true_isolations"] = (
            self.discarded_work_dollars_from_true_isolations() * self.fleet_scale
        )
        out["rerun_work_dollars_from_true_isolations"] = (
            self.rerun_work_dollars_from_true_isolations() * self.fleet_scale
        )
        out["discard_rerun_dollars_from_false_isolations"] = (
            self.discard_rerun_dollars_from_false_isolations() * self.fleet_scale
        )
        out["discard_rerun_dollars_from_true_isolations"] = (
            self.discard_rerun_dollars_from_true_isolations() * self.fleet_scale
        )
        out["incremental_dollars"] = self.incremental()
        out["overhead_pct"] = self.overhead_pct()
        out["review_cost_share_of_overhead"] = self.review_cost_share_of_overhead()
        return out


@dataclass(frozen=True)
class AuditResult:
    agent_id: str
    tick: int
    flagged: bool
    input_tokens: int
    output_tokens: int
