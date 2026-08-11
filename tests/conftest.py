from __future__ import annotations

import random
from pathlib import Path

import pytest

from harpy.cli import load_config
from harpy.ledger import ModelPrice, Pricing, load_pricing
from harpy.types import Claim, ClaimPayload, Message

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config() -> dict:
    return load_config(REPO_ROOT / "configs" / "default.yaml")


@pytest.fixture
def placeholder_pricing() -> Pricing:
    return load_pricing(REPO_ROOT / "configs" / "pricing.yaml")


@pytest.fixture
def test_pricing() -> Pricing:
    """Non-zero prices so budget arithmetic has something to bite on.

    Deliberately not read from any provider page — tests assert arithmetic, not
    economics, and a test that needed real prices would be testing the wrong
    thing.
    """
    return Pricing(
        worker_model=ModelPrice(3.0, 15.0, "test fixture", "n/a"),
        sentinel_model=ModelPrice(5.0, 25.0, "test fixture", "n/a"),
        tier0_cost_per_message_dollars=0.0000001,
        verified=True,
        path="<test fixture>",
    )


@pytest.fixture
def rng() -> random.Random:
    return random.Random(1234)


def make_payload(
    subject_id: str = "subject-00",
    field_name: str = "price_usd",
    value_num: float | None = 100.0,
    value_date: str | None = None,
    value_enum: str | None = None,
    schema_version: int = 3,
) -> ClaimPayload:
    return ClaimPayload(
        subject_id=subject_id,
        field_name=field_name,
        value_num=value_num,
        value_date=value_date,
        value_enum=value_enum,
        schema_version=schema_version,
    )


def make_claim(
    claim_id: str = "claim-0",
    origin_agent_id: str = "agent-00",
    tick: int = 0,
    payload: ClaimPayload | None = None,
    is_corrupt: bool = False,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        origin_agent_id=origin_agent_id,
        tick=tick,
        payload=payload or make_payload(),
        is_corrupt=is_corrupt,
    )


def make_message(
    sender_id: str = "agent-00",
    recipient_id: str = "agent-01",
    tick: int = 0,
    claims: tuple[Claim, ...] | None = None,
    malformed: bool = False,
    tool_error: bool = False,
    retry_count: int = 0,
    token_len: int = 400,
) -> Message:
    return Message(
        message_id=f"msg-{sender_id}-{tick}",
        sender_id=sender_id,
        recipient_id=recipient_id,
        tick=tick,
        claims=claims if claims is not None else (make_claim(),),
        malformed=malformed,
        tool_error=tool_error,
        retry_count=retry_count,
        token_len=token_len,
    )
