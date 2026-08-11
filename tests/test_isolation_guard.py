"""The ground-truth guard: HARPY-side modules cannot read the answer key.

The probes here compile expressions with a target module's ``__file__`` as the
code object's filename, so the resulting frame is indistinguishable from a real
frame inside that module. That is how each of the five guarded modules gets
tested without adding a test hook to any of them.
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest

from harpy import alert_budget as alert_budget_module
from harpy import mesh as mesh_module
from harpy import metrics as metrics_module
from harpy import response as response_module
from harpy import sampler as sampler_module
from harpy import sentinel as sentinel_module
from harpy import simulation as simulation_module
from harpy import suspicion as suspicion_module
from harpy import telemetry as telemetry_module
from harpy.lineage import make_pair
from harpy.sentinel import MockSentinel
from harpy.types import GroundTruthLeakError, LineageEdge, Severity

from conftest import make_claim, make_message

GUARDED_MODULES = [
    telemetry_module,
    sentinel_module,
    sampler_module,
    suspicion_module,
    response_module,
    alert_budget_module,
]
SCORING_MODULES = [metrics_module, mesh_module, simulation_module]


def probe(module, expression: str, **env):
    """Evaluate ``expression`` in a frame that claims to live in ``module``."""
    code = compile(expression, module.__file__, "eval")
    return eval(code, {"__builtins__": {}}, env)  # noqa: S307 - deliberate


def a_pair():
    return make_pair(
        frozenset({LineageEdge("a", "b")}), fidelity=1.0, spurious_rate=0.0, rng=random.Random(0)
    )


@pytest.mark.parametrize("module", GUARDED_MODULES, ids=lambda m: Path(m.__file__).name)
def test_is_corrupt_is_unreadable_from_every_harpy_module(module):
    with pytest.raises(GroundTruthLeakError, match="is_corrupt"):
        probe(module, "claim.is_corrupt", claim=make_claim(is_corrupt=True))


@pytest.mark.parametrize("module", GUARDED_MODULES, ids=lambda m: Path(m.__file__).name)
def test_ground_truth_lineage_is_unreadable_from_every_harpy_module(module):
    with pytest.raises(GroundTruthLeakError, match="ground_truth"):
        probe(module, "pair.ground_truth", pair=a_pair())


@pytest.mark.parametrize("module", GUARDED_MODULES, ids=lambda m: Path(m.__file__).name)
def test_the_observable_projection_stays_readable(module):
    assert probe(module, "pair.observable", pair=a_pair()) == frozenset({LineageEdge("a", "b")})


@pytest.mark.parametrize("module", SCORING_MODULES, ids=lambda m: Path(m.__file__).name)
def test_scoring_side_modules_may_read_ground_truth(module):
    assert probe(module, "claim.is_corrupt", claim=make_claim(is_corrupt=True)) is True
    assert probe(module, "pair.ground_truth", pair=a_pair())


def test_the_guard_cannot_be_laundered_through_an_unguarded_helper():
    """A guarded frame anywhere below the access still trips the guard."""
    helper_ns: dict = {}
    exec(  # noqa: S102 - deliberate frame forgery
        compile(
            "def helper(claim):\n    return claim.is_corrupt\n",
            metrics_module.__file__,
            "exec",
        ),
        helper_ns,
    )
    with pytest.raises(GroundTruthLeakError):
        probe(telemetry_module, "helper(claim)", helper=helper_ns["helper"], claim=make_claim())
    # ...and the same helper called from an unguarded frame is fine.
    from_scoring = probe(
        simulation_module, "helper(claim)", helper=helper_ns["helper"], claim=make_claim()
    )
    assert from_scoring is False


def test_mock_sentinel_is_whitelisted_and_does_read_ground_truth():
    sentinel = MockSentinel(
        detection_prob={Severity.OVERT: 1.0, Severity.MODERATE: 1.0, Severity.SUBTLE: 1.0},
        false_positive_rate=0.0,
        input_tokens=9000,
        output_tokens=600,
        rng=random.Random(0),
        severity=Severity.MODERATE,
    )
    corrupt = make_message(claims=(make_claim(is_corrupt=True),))
    clean = make_message(claims=(make_claim(is_corrupt=False),))
    # Detection probability 1.0 and false-positive rate 0.0: the only way these
    # two can differ is by having read is_corrupt.
    assert sentinel.audit("agent-00", (corrupt,)).flagged is True
    assert sentinel.audit("agent-00", (clean,)).flagged is False


def test_the_whitelist_is_scoped_to_mock_sentinel_not_to_its_module():
    with pytest.raises(GroundTruthLeakError):
        probe(sentinel_module, "claim.is_corrupt", claim=make_claim(is_corrupt=True))


def test_a_same_named_module_outside_the_package_is_not_guarded(tmp_path):
    """The guard matches on path, not on a bare file name.

    Some unrelated library's ``response.py`` sitting on the stack must not be
    mistaken for HARPY's.
    """
    impostor = compile("claim.is_corrupt", str(tmp_path / "response.py"), "eval")
    assert eval(impostor, {"__builtins__": {}}, {"claim": make_claim(is_corrupt=True)}) is True


def test_is_corrupt_does_not_leak_through_equality_or_repr():
    corrupt = make_claim(is_corrupt=True)
    clean = make_claim(is_corrupt=False)
    assert corrupt == clean
    assert hash(corrupt) == hash(clean)
    assert "is_corrupt" not in repr(corrupt)


def test_claims_are_frozen_against_rewriting_ground_truth():
    claim = make_claim(is_corrupt=False)
    with pytest.raises(AttributeError):
        claim.is_corrupt = True


@pytest.mark.parametrize("module", GUARDED_MODULES, ids=lambda m: Path(m.__file__).name)
def test_no_guarded_module_even_mentions_ground_truth_in_source(module):
    """Static backstop: the guard is runtime, but the source should be clean too.

    ``MockSentinel.audit`` is the sole exception, and it is exempted by name so
    that a second reader appearing in sentinel.py would still fail this test.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr not in {"is_corrupt", "ground_truth"}:
            continue
        function = _enclosing_function(tree, node)
        if module is sentinel_module and function == "MockSentinel.audit":
            continue
        offenders.append(f"{Path(module.__file__).name}:{node.lineno} ({function})")
    assert not offenders, f"ground truth referenced outside the whitelist: {offenders}"


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str:
    """Qualified name of the function containing ``target``, if any."""
    best = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and _contains(child, target):
                    best = f"{node.name}.{child.name}"
        elif isinstance(node, ast.FunctionDef) and _contains(node, target) and best == "<module>":
            best = node.name
    return best


def _contains(node: ast.AST, target: ast.AST) -> bool:
    return any(child is target for child in ast.walk(node))
