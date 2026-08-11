"""HARPY-SIM — phase 1 of two.

Under what detector characteristics and at what cost is budgeted oversight of a
multi-agent mesh economically viable? Phase 1 answers that with a simulator and
a mock detector whose accuracy is an input. It cannot answer whether such a
detector exists; that is phase 2 (HARPY-REPLAY). See the README.
"""

from .types import (
    Arm,
    AuditResult,
    BudgetLedger,
    Claim,
    ClaimPayload,
    GroundTruthLeakError,
    LineageEdge,
    LineagePair,
    Message,
    Severity,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "Arm",
    "AuditResult",
    "BudgetLedger",
    "Claim",
    "ClaimPayload",
    "GroundTruthLeakError",
    "LineageEdge",
    "LineagePair",
    "Message",
    "Severity",
    "__version__",
]
