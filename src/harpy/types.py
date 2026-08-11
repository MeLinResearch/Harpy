"""Core data structures and the ground-truth isolation guard.

Two fields in this module are ground truth: ``Claim.is_corrupt`` and
``LineagePair.ground_truth``. They exist so runs can be *scored*. HARPY's own
detection path must never read them — if it could, phase 1 would be measuring
an oracle rather than an architecture.

The guard is mechanical, not a convention: both fields are exposed through
property accessors that inspect the call stack and raise
:class:`GroundTruthLeakError` when the access originates inside a
HARPY-side module (``telemetry``, ``sentinel``, ``sampler``, ``suspicion``,
``response``). ``MockSentinel`` is the single whitelisted exception: it
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
    {"telemetry.py", "sentinel.py", "sampler.py", "suspicion.py", "response.py"}
)

#: Qualified-name prefixes that are exempt, keyed by module file name.
WHITELISTED_QUALNAMES: dict[str, tuple[str, ...]] = {"sentinel.py": ("MockSentinel",)}

_THIS_FILE = os.path.basename(__file__)
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _module_name(filename: str) -> str | None:
    """File name if the frame belongs to this package, else None.

    Matching on the full directory rather than the bare file name keeps an
    unrelated third-party ``response.py`` somewhere on the stack from being
    mistaken for HARPY's.
    """
    path = os.path.abspath(filename)
    if os.path.dirname(path) != _PACKAGE_DIR:
        return None
    return os.path.basename(path)


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


@dataclass
class BudgetLedger:
    """Six cost components. Five of them are the price of oversight."""

    worker_dollars: float = 0.0
    sentinel_dollars: float = 0.0
    tier0_dollars: float = 0.0
    discarded_work_dollars: float = 0.0
    rerun_work_dollars: float = 0.0
    human_review_dollars: float = 0.0

    def incremental(self) -> float:
        """Everything the oversight layer costs, i.e. everything but the workers."""
        return (
            self.sentinel_dollars
            + self.tier0_dollars
            + self.discarded_work_dollars
            + self.rerun_work_dollars
            + self.human_review_dollars
        )

    def overhead_pct(self) -> float:
        """Incremental cost as a fraction of worker-fleet cost.

        Zero worker spend means there is nothing to be an overhead *of*; the
        ratio is reported as 0.0 rather than raising or returning infinity.
        """
        if self.worker_dollars <= 0.0:
            return 0.0
        return self.incremental() / self.worker_dollars

    def as_dict(self) -> dict:
        return {
            "worker_dollars": self.worker_dollars,
            "sentinel_dollars": self.sentinel_dollars,
            "tier0_dollars": self.tier0_dollars,
            "discarded_work_dollars": self.discarded_work_dollars,
            "rerun_work_dollars": self.rerun_work_dollars,
            "human_review_dollars": self.human_review_dollars,
            "incremental_dollars": self.incremental(),
            "overhead_pct": self.overhead_pct(),
        }


@dataclass(frozen=True)
class AuditResult:
    agent_id: str
    tick: int
    flagged: bool
    input_tokens: int
    output_tokens: int
