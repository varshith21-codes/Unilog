"""Tests for the price table and the cost-per-SKU meter.

Two properties matter here, and they pull in opposite directions.

**Never invent a price.** A cost-per-SKU figure is what a buyer uses to decide whether the
system is affordable at half a million SKUs. A wrong-but-plausible number ends that
conversation on false information, whereas a blank prompts a question. So an unpriced model
yields no cost at all, not a zero and not an estimate.

**Never understate one either.** Tokens are attributed to the tier that actually burned them.
Apportioning a single total by each tier's share of the *call count* looks equivalent and
systematically flatters the cascade, because escalation re-sends a whole prompt to the
expensive model. The test below pins that with numbers where the two approaches differ by 50x.
"""

from __future__ import annotations

import pytest
from axiom.extract import ModelCascade, PriceTable, StubModelClient, UsageLedger, load_prices
from axiom.extract.pricing import STALE_AFTER_DAYS, ModelPrice

VOLUME = (0.07, 0.40)
"""Real us-east-2 rate for zai.glm-4.7-flash, per million tokens."""

FRONTIER = (1.00, 3.20)
"""Real us-east-2 rate for zai.glm-5."""


def ledger_with(*calls: tuple[str, int, int]) -> UsageLedger:
    """Build a ledger directly, so token counts per tier are exact and readable."""
    ledger = UsageLedger()
    for tier, tokens_in, tokens_out in calls:
        ledger.calls += 1
        ledger.input_tokens += tokens_in
        ledger.output_tokens += tokens_out
        ledger.by_tier[tier] = ledger.by_tier.get(tier, 0) + 1
        ledger.input_by_tier[tier] = ledger.input_by_tier.get(tier, 0) + tokens_in
        ledger.output_by_tier[tier] = ledger.output_by_tier.get(tier, 0) + tokens_out
    return ledger


# ===================================================================== per-tier attribution


def test_tokens_are_attributed_to_the_tier_that_burned_them():
    """The regression that keeps the cascade honest.

    One cheap call with a tiny prompt, then one frontier escalation with a large one. Splitting
    the 10,100 input tokens evenly by call count would bill 5,050 of them at the cheap rate and
    5,050 at the expensive rate. The truth is 100 cheap and 10,000 expensive — a materially
    higher number, and the one that tells you escalation is what costs money.
    """
    ledger = ledger_with(("volume", 100, 50), ("frontier", 10_000, 2_000))
    prices = {"volume": VOLUME, "frontier": FRONTIER}

    expected = (
        (100 / 1e6) * 0.07
        + (50 / 1e6) * 0.40
        + (10_000 / 1e6) * 1.00
        + (2_000 / 1e6) * 3.20
    )
    assert ledger.cost_usd(prices) == pytest.approx(expected, rel=1e-9)

    # What the old call-share approach would have produced, for contrast.
    half_in, half_out = 10_100 / 2, 2_050 / 2
    call_share = (
        (half_in / 1e6) * 0.07
        + (half_out / 1e6) * 0.40
        + (half_in / 1e6) * 1.00
        + (half_out / 1e6) * 3.20
    )
    assert ledger.cost_usd(prices) > call_share, "call-share apportioning understates escalation"


def test_cost_by_tier_shows_where_the_money_went():
    ledger = ledger_with(("volume", 1_000, 500), ("frontier", 1_000, 500))
    breakdown = ledger.cost_by_tier({"volume": VOLUME, "frontier": FRONTIER})

    assert breakdown is not None
    assert breakdown["frontier"] > breakdown["volume"], "same tokens, dearer model"
    assert sum(breakdown.values()) == pytest.approx(
        ledger.cost_usd({"volume": VOLUME, "frontier": FRONTIER}), rel=1e-9
    )


def test_merge_preserves_per_tier_tokens():
    """A batch total is the sum of its parts, per tier, or the breakdown is meaningless."""
    a = ledger_with(("volume", 100, 10))
    b = ledger_with(("volume", 200, 20), ("frontier", 5_000, 500))
    a.merge(b)

    assert a.input_by_tier == {"volume": 300, "frontier": 5_000}
    assert a.output_by_tier == {"volume": 30, "frontier": 500}
    assert a.by_tier == {"volume": 2, "frontier": 1}
    assert a.input_tokens == 5_300


def test_recording_a_response_tracks_its_tokens_by_tier():
    """Guards the wiring between the client and the ledger, not just the arithmetic."""
    ledger = UsageLedger()
    client = StubModelClient(["x" * 40, "y" * 80])
    ledger.record(client.converse(model_id="m", tier="volume", system="s", user="u" * 100))
    ledger.record(client.converse(model_id="m", tier="frontier", system="s", user="u" * 400))

    assert set(ledger.input_by_tier) == {"volume", "frontier"}
    assert sum(ledger.input_by_tier.values()) == ledger.input_tokens
    assert sum(ledger.output_by_tier.values()) == ledger.output_tokens
    assert ledger.input_by_tier["frontier"] > ledger.input_by_tier["volume"]


# ===================================================================== refusing to guess


def test_no_price_table_means_no_cost():
    assert ledger_with(("volume", 100, 10)).cost_usd(None) is None
    assert ledger_with(("volume", 100, 10)).cost_usd({}) is None


def test_a_used_tier_without_a_price_blanks_the_whole_cost():
    """A partial total would silently undercount, which is worse than reporting nothing."""
    ledger = ledger_with(("volume", 100, 10), ("frontier", 100, 10))
    assert ledger.cost_usd({"volume": VOLUME}) is None
    assert ledger.cost_by_tier({"volume": VOLUME}) is None


def test_an_unused_tier_without_a_price_is_harmless():
    """An unpriced vision model must not blank the cost of a run that never called it."""
    ledger = ledger_with(("volume", 100, 10))
    assert ledger.cost_usd({"volume": VOLUME}) is not None


def test_an_empty_ledger_costs_nothing_rather_than_failing():
    assert UsageLedger().cost_usd({"volume": VOLUME}) == 0.0


# ===================================================================== the table


def test_missing_table_loads_as_none_rather_than_raising(tmp_path):
    """Cost reporting is optional; a pipeline must run on an account that never fetched it."""
    assert PriceTable.load(tmp_path / "absent.yaml") is None
    assert load_prices(tmp_path / "absent.yaml") is None


def test_generated_table_is_present_and_parses():
    """The checked-in table is generated by scripts/fetch_bedrock_prices.py from the AWS API."""
    table = load_prices()
    assert table is not None, "run: python scripts/fetch_bedrock_prices.py --write"
    assert table.region
    assert table.models
    assert "AWS" in table.source


def test_every_pinned_tier_in_the_cascade_can_be_priced():
    """If a tier the pipeline actually uses is unpriced, cost reporting silently disappears."""
    cascade = ModelCascade.load()
    table = load_prices()
    assert table is not None

    prices = table.tier_prices(cascade)
    unpriced = sorted(set(cascade.tiers) - set(prices))
    assert not unpriced, (
        f"tiers {unpriced} have no published price; re-run the fetch script or expect "
        f"no cost on any run that escalates to them"
    )


def test_tier_prices_translates_model_ids_into_tiers():
    cascade = ModelCascade(
        region="us-east-2",
        tiers={"volume": "zai.glm-4.7-flash", "frontier": "unpriced.model"},
    )
    table = PriceTable(
        region="us-east-2",
        fetched_at="2026-08-01T00:00:00+00:00",
        source="test",
        models={"zai.glm-4.7-flash": ModelPrice(0.07, 0.40)},
    )
    prices = table.tier_prices(cascade)

    assert prices == {"volume": (0.07, 0.40)}
    assert "frontier" not in prices, "an unpriced model is omitted, not defaulted to zero"


def test_flash_is_dramatically_cheaper_than_frontier():
    """The premise of the whole cascade, checked against real fetched prices.

    If this ever fails, starting cheap and escalating is not saving anything and the tier
    ordering needs revisiting.
    """
    table = load_prices()
    assert table is not None
    flash = table.models.get("zai.glm-4.7-flash")
    frontier = table.models.get("zai.glm-5")
    if flash is None or frontier is None:
        pytest.skip("both models must be priced in this region to compare them")

    assert flash.input_per_million_usd < frontier.input_per_million_usd / 5
    assert flash.output_per_million_usd < frontier.output_per_million_usd / 5


def test_embedding_model_is_priced_on_input_only():
    """Embeddings emit vectors, not tokens, so an output rate would be meaningless."""
    table = load_prices()
    assert table is not None
    embedding = next(
        (p for model_id, p in table.models.items() if "titan-embed" in model_id), None
    )
    if embedding is None:
        pytest.skip("no embedding model priced in this region")
    assert embedding.input_per_million_usd > 0
    assert embedding.output_per_million_usd == 0.0


# ===================================================================== staleness


def test_a_fresh_table_is_not_stale():
    from datetime import UTC, datetime

    table = PriceTable(
        region="us-east-2",
        fetched_at=datetime.now(UTC).isoformat(),
        source="test",
        models={},
    )
    assert table.is_stale is False
    assert table.age_days() < 1


def test_an_old_table_is_reported_as_stale():
    """AWS changes prices. A year-old table misrepresents unit economics silently."""
    from datetime import UTC, datetime, timedelta

    old = datetime.now(UTC) - timedelta(days=STALE_AFTER_DAYS + 5)
    table = PriceTable(
        region="us-east-2", fetched_at=old.isoformat(), source="test", models={}
    )
    assert table.is_stale is True


def test_an_unparseable_timestamp_does_not_crash_staleness():
    table = PriceTable(region="x", fetched_at="not a date", source="test", models={})
    assert table.age_days() is None
    assert table.is_stale is False


def test_summary_is_reportable():
    table = load_prices()
    assert table is not None
    summary = table.summary()
    for key in ("region", "fetched_at", "source", "priced_models", "stale"):
        assert key in summary


# ===================================================================== projection


def test_serialise_cost_carries_provenance():
    from axiom.console import serialise_cost

    ledger = ledger_with(("volume", 1_000, 200))
    table = load_prices()
    prices = {"volume": VOLUME}

    payload = serialise_cost(
        ledger,
        cost_usd=ledger.cost_usd(prices),
        cost_by_tier=ledger.cost_by_tier(prices),
        prices=table,
    )

    assert payload["priced"] is True
    assert payload["cost_usd"] > 0
    assert payload["input_by_tier"] == {"volume": 1_000}
    assert payload["price_source"]["region"]


def test_serialise_cost_marks_an_unpriced_run_rather_than_showing_zero():
    """Zero would read as a result. It is a missing input."""
    from axiom.console import serialise_cost

    ledger = ledger_with(("frontier", 1_000, 200))
    payload = serialise_cost(ledger, cost_usd=None, cost_by_tier=None, prices=None)

    assert payload["priced"] is False
    assert payload["cost_usd"] is None
    assert payload["cost_by_tier"] is None
    assert payload["input_tokens"] == 1_000, "usage is still reported without a price"
