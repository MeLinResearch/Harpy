"""Tier 0 is seven mechanical checks on typed fields, and nothing more."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from harpy import telemetry as telemetry_module
from harpy.telemetry import CHECK_NAMES, Tier0Config, Tier0Telemetry
from harpy.types import ClaimPayload

from conftest import make_claim, make_message, make_payload

TYPED_PAYLOAD_FIELDS = {
    "subject_id",
    "field_name",
    "value_num",
    "value_date",
    "value_enum",
    "schema_version",
}


def tier0(**overrides) -> Tier0Telemetry:
    base = {
        "window": 50,
        "token_window": 100,
        "token_zscore_cap": 4.0,
        "retry_normalizer": 3.0,
        "expected_schema_version": 3,
        "cost_per_message_dollars": 0.0000001,
        "weights": dict.fromkeys(CHECK_NAMES, 1.0),
        "numeric_ranges": {"price_usd": (0.0, 10000.0), "score_pct": (0.0, 100.0)},
    }
    base.update(overrides)
    return Tier0Telemetry(Tier0Config(**base))


def feed(telemetry: Tier0Telemetry, messages, rejected=frozenset()) -> None:
    for message in messages:
        telemetry.observe(message, rejected)


# -- the seven, individually ------------------------------------------------


def test_exactly_seven_checks_exist():
    assert len(CHECK_NAMES) == 7
    assert set(tier0().components("agent-00")) == set(CHECK_NAMES)


def test_1_schema_violation_on_version_mismatch():
    telemetry = tier0()
    claim = make_claim(payload=make_payload(schema_version=4))
    feed(telemetry, [make_message(claims=(claim,))])
    assert telemetry.components("agent-00")["schema_violation"] == 1.0


def test_1_schema_violation_on_a_missing_required_field():
    telemetry = tier0()
    empty = make_payload(value_num=None, value_date=None, value_enum=None)
    feed(telemetry, [make_message(claims=(make_claim(payload=empty),))])
    assert telemetry.components("agent-00")["schema_violation"] == 1.0


def test_2_malformed_rate_is_a_rolling_frequency():
    telemetry = tier0()
    feed(telemetry, [make_message(malformed=i < 20) for i in range(100)])
    # Window is 50 and the malformed run has rolled out of it entirely.
    assert telemetry.components("agent-00")["malformed_rate"] == 0.0

    telemetry = tier0()
    feed(telemetry, [make_message(malformed=i % 2 == 0) for i in range(50)])
    assert telemetry.components("agent-00")["malformed_rate"] == pytest.approx(0.5)


def test_3_tool_error_rate_is_a_rolling_frequency():
    telemetry = tier0()
    feed(telemetry, [make_message(tool_error=i < 10) for i in range(40)])
    assert telemetry.components("agent-00")["tool_error_rate"] == pytest.approx(0.25)


def test_4_retry_rate_is_a_normalised_rolling_mean():
    telemetry = tier0()
    feed(telemetry, [make_message(retry_count=3) for _ in range(50)])
    assert telemetry.components("agent-00")["retry_rate"] == 1.0

    telemetry = tier0()
    feed(telemetry, [make_message(retry_count=0) for _ in range(50)])
    assert telemetry.components("agent-00")["retry_rate"] == 0.0


def test_5_token_len_zscore_is_relative_to_the_agents_own_history():
    # History of 400/410/420/430/440: mean 420, population sd ~14.1.
    telemetry = tier0()
    feed(telemetry, [make_message(token_len=400 + 10 * (i % 5)) for i in range(60)])
    feed(telemetry, [make_message(token_len=420)])
    assert telemetry.components("agent-00")["token_len_zscore"] == pytest.approx(0.0, abs=0.02)
    feed(telemetry, [make_message(token_len=40)])
    assert telemetry.components("agent-00")["token_len_zscore"] == 1.0


def test_5_token_len_zscore_is_scaled_by_the_configured_cap():
    telemetry = tier0(token_zscore_cap=4.0)
    feed(telemetry, [make_message(token_len=400 + 10 * (i % 5)) for i in range(60)])
    feed(telemetry, [make_message(token_len=420 + 28)])  # ~2 sd out of 4
    assert telemetry.components("agent-00")["token_len_zscore"] == pytest.approx(0.5, abs=0.03)


def test_5_token_len_zscore_needs_history_before_it_fires():
    telemetry = tier0()
    feed(telemetry, [make_message(token_len=9999)])
    assert telemetry.components("agent-00")["token_len_zscore"] == 0.0


def test_6_numeric_range_violation_uses_the_configured_bounds():
    telemetry = tier0()
    over = make_claim(payload=make_payload(field_name="score_pct", value_num=140.0))
    feed(telemetry, [make_message(claims=(over,))])
    assert telemetry.components("agent-00")["numeric_range_violation"] == 1.0

    telemetry = tier0()
    inside = make_claim(payload=make_payload(field_name="score_pct", value_num=55.0))
    feed(telemetry, [make_message(claims=(inside,))])
    assert telemetry.components("agent-00")["numeric_range_violation"] == 0.0


def test_6_unconfigured_field_names_are_not_range_checked():
    telemetry = tier0()
    claim = make_claim(payload=make_payload(field_name="not_in_config", value_num=1e12))
    feed(telemetry, [make_message(claims=(claim,))])
    assert telemetry.components("agent-00")["numeric_range_violation"] == 0.0


def test_7_downstream_rejection_rate_tracks_recipient_refusals():
    telemetry = tier0()
    claim = make_claim(claim_id="claim-x")
    for _ in range(10):
        telemetry.observe(make_message(claims=(claim,)), frozenset({"claim-x"}))
    assert telemetry.components("agent-00")["downstream_rejection_rate"] == 1.0
    for _ in range(10):
        telemetry.observe(make_message(claims=(claim,)), frozenset())
    assert telemetry.components("agent-00")["downstream_rejection_rate"] == pytest.approx(0.5)


# -- shape and cost ---------------------------------------------------------


@pytest.mark.parametrize("name", CHECK_NAMES)
def test_every_check_stays_in_the_unit_interval(name):
    telemetry = tier0()
    nasty = make_claim(
        payload=make_payload(field_name="score_pct", value_num=-1e9, schema_version=99)
    )
    for i in range(120):
        telemetry.observe(
            make_message(
                claims=(nasty,) * 3,
                malformed=True,
                tool_error=True,
                retry_count=50,
                token_len=1 if i % 3 else 100000,
            ),
            frozenset({"claim-0"}),
        )
    assert 0.0 <= telemetry.components("agent-00")[name] <= 1.0


def test_score_is_the_configured_weighted_sum():
    telemetry = tier0(weights={**dict.fromkeys(CHECK_NAMES, 0.0), "malformed_rate": 1.0})
    feed(telemetry, [make_message(malformed=True, retry_count=3) for _ in range(50)])
    assert telemetry.score("agent-00") == pytest.approx(1.0)

    telemetry = tier0(weights={**dict.fromkeys(CHECK_NAMES, 0.0), "tool_error_rate": 1.0})
    feed(telemetry, [make_message(malformed=True, retry_count=3) for _ in range(50)])
    assert telemetry.score("agent-00") == 0.0


def test_unknown_agents_score_zero_and_reset_clears_history():
    telemetry = tier0()
    assert telemetry.score("nobody") == 0.0
    feed(telemetry, [make_message(malformed=True) for _ in range(20)])
    assert telemetry.score("agent-00") > 0.0
    telemetry.reset_agent("agent-00")
    assert telemetry.score("agent-00") == 0.0


def test_tier0_costs_the_configured_flat_rate_per_message():
    telemetry = tier0(cost_per_message_dollars=0.0000001)
    total = sum(telemetry.observe(make_message(), frozenset()) for _ in range(1_000_000))
    assert total == pytest.approx(0.1)


def test_config_rejects_weights_that_are_not_exactly_the_seven_checks(config):
    broken = {**config, "telemetry": {**config["telemetry"], "weights": {"malformed_rate": 1.0}}}
    with pytest.raises(ValueError, match="seven checks"):
        Tier0Config.from_config(broken, 0.0000001)


# -- no free text anywhere --------------------------------------------------


def test_a_claim_payload_has_no_free_text_field_to_read():
    assert {f.name for f in fields(ClaimPayload)} == TYPED_PAYLOAD_FIELDS


def test_no_check_reads_free_text():
    """Static proof that Tier 0 only touches typed fields and flags.

    Tier 0's whole claim to being near-free is that it never does anything
    semantic. Since a payload has no prose field, the risk is Tier 0 growing
    string handling over the typed strings it does see — so string methods and
    regex are banned outright in this module, alongside any attribute outside
    the typed allowlist.
    """
    source = Path(telemetry_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    allowed_attributes = TYPED_PAYLOAD_FIELDS | {
        # Message-level typed observables.
        "sender_id",
        "claims",
        "payload",
        "malformed",
        "tool_error",
        "retry_count",
        "token_len",
        # Internal state and config.
        "config",
        "window",
        "token_window",
        "token_zscore_cap",
        "retry_normalizer",
        "expected_schema_version",
        "cost_per_message_dollars",
        "weights",
        "numeric_ranges",
        "malformed_rate",
        "tool_error_rate",
        "retries",
        "token_lens",
        "schema_flags",
        "range_flags",
        "rejections",
        "zscore",
        "messages_observed",
        "_agents",
        "_window_for",
        "_token_zscore",
        "components",
        "score",
        "append",
        "get",
        "pop",
        "items",
        "clear",
        "fromkeys",
    }
    banned_string_methods = {
        "lower", "upper", "split", "strip", "find", "startswith", "endswith",
        "replace", "join", "format", "encode", "decode", "search", "match",
        "findall", "sub", "compile",
    }

    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            seen.add(node.attr)
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "re", "Tier 0 must not import a regex engine"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re", "Tier 0 must not import a regex engine"

    strings = seen & banned_string_methods
    assert not strings, f"string handling in Tier 0: {strings}"
    unexpected = {name for name in seen if not name.startswith("__")} - allowed_attributes
    assert not unexpected, f"Tier 0 touched something outside the typed allowlist: {unexpected}"
