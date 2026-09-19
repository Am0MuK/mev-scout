from decimal import Decimal
import json
import pytest

from mev_scout.chains import CHAINS
from mev_scout.decode import Liquidation
from mev_scout.report import (
    CoverageError,
    bucket_for_debt,
    calculate_concentration,
    evaluate_verdict,
    generate_csv,
    generate_report,
)
from mev_scout.store import Store
from mev_scout.validate import ValidationResult
from mev_scout.value import ValuedLiquidation


def _make_valued(
    liquidator="0xliq1",
    debt_usd=Decimal("500"),
    net_usd=Decimal("50"),
    timestamp=1700000000,
    tx_hash="0x1",
    log_index=0,
):
    ev = Liquidation(
        chain_id=146,
        block=50000000,
        timestamp=timestamp,
        tx_hash=tx_hash,
        log_index=log_index,
        collateral="0xc",
        debt="0xd",
        user="0xu",
        debt_to_cover=int(debt_usd * 10**6),
        collateral_amount=10**18,
        liquidator=liquidator,
        receive_atoken=False,
        gas_used=100000,
        gas_price=10**9,
    )
    return ValuedLiquidation(
        event=ev,
        unpriced=False,
        collateral_usd=debt_usd + net_usd + Decimal("10"),
        debt_usd=debt_usd,
        gross_usd=net_usd + Decimal("10"),
        gas_usd=Decimal("5"),
        swap_cost=Decimal("3"),
        flash_fee=Decimal("2"),
        net_usd=net_usd,
    )


def test_bucket_boundaries():
    assert bucket_for_debt(Decimal("99.99")) == "<100"
    assert bucket_for_debt(Decimal("100.00")) == "100–1k"
    assert bucket_for_debt(Decimal("999.99")) == "100–1k"
    assert bucket_for_debt(Decimal("1000.00")) == "1k–10k"
    assert bucket_for_debt(Decimal("10000.00")) == "≥10k"


def test_concentration_hand_computed():
    items = [
        _make_valued(liquidator="0xA", net_usd=Decimal("600"), tx_hash="0x1"),
        _make_valued(liquidator="0xB", net_usd=Decimal("300"), tx_hash="0x2"),
        _make_valued(liquidator="0xC", net_usd=Decimal("100"), tx_hash="0x3"),
    ]
    conc = calculate_concentration(items)
    assert conc["top1_share"] == Decimal("0.6")
    assert conc["top3_share"] == Decimal("1.0")
    # 60^2 + 30^2 + 10^2 = 3600 + 900 + 100 = 4600
    assert conc["hhi"] == Decimal("4600")


def test_evaluate_verdict_pass():
    verdict, reason = evaluate_verdict(avg_monthly_eur=Decimal("350"), top1_share=Decimal("0.40"), threshold_eur=Decimal("300"))
    assert verdict == "PASS"
    assert reason == ""


def test_evaluate_verdict_fail_profit():
    verdict, reason = evaluate_verdict(avg_monthly_eur=Decimal("250"), top1_share=Decimal("0.40"), threshold_eur=Decimal("300"))
    assert verdict == "FAIL"
    assert "below threshold" in reason


def test_evaluate_verdict_fail_concentration():
    verdict, reason = evaluate_verdict(avg_monthly_eur=Decimal("350"), top1_share=Decimal("0.60"), threshold_eur=Decimal("300"))
    assert verdict == "FAIL"
    assert "too concentrated" in reason


def test_evaluate_verdict_fail_both():
    verdict, reason = evaluate_verdict(avg_monthly_eur=Decimal("250"), top1_share=Decimal("0.60"), threshold_eur=Decimal("300"))
    assert verdict == "FAIL"
    assert "below threshold" in reason
    assert "too concentrated" in reason


def test_report_coverage_gap_raises_coverage_error():
    store = Store(":memory:")
    # Requested 100 to 200, but store only has 100 to 150
    store.insert_range(146, 100, 150)
    with pytest.raises(CoverageError, match="Coverage gap"):
        generate_report(
            chain_ids=[146],
            from_blocks={146: 100},
            to_blocks={146: 200},
            store=store,
            valued_events={146: []},
            validation_results={146: ValidationResult(146)},
            eurusd=Decimal("1.17"),
            days=90,
        )


def test_generate_csv():
    items = [_make_valued(liquidator="0xA", net_usd=Decimal("600"), tx_hash="0x1")]
    csv_str = generate_csv(items, eurusd=Decimal("1.20"))
    assert "tx_hash" in csv_str
    assert "net_eur" in csv_str
    assert "0x1" in csv_str
    assert "500" in csv_str  # 600 / 1.20 = 500 EUR


def test_report_text_and_json():
    store = Store(":memory:")
    store.insert_range(146, 100, 200)

    items = [
        _make_valued(liquidator="0xA", net_usd=Decimal("600"), debt_usd=Decimal("500"), tx_hash="0x1", log_index=0),
        _make_valued(liquidator="0xB", net_usd=Decimal("400"), debt_usd=Decimal("500"), tx_hash="0x2", log_index=1),
    ]
    val_res = ValidationResult(146, total_sampled=1, gas_checks_passed=1, transfer_checks_passed=1)

    report_data = generate_report(
        chain_ids=[146],
        from_blocks={146: 100},
        to_blocks={146: 200},
        store=store,
        valued_events={146: items},
        validation_results={146: val_res},
        eurusd=Decimal("1.20"),
        threshold_eur=Decimal("300"),
        days=90,
    )

    text = report_data.to_text()
    assert "Sonic" in text
    assert "contestable" in text
    assert "BNB Chain" in text  # Uncovered chains listed

    json_str = report_data.to_json()
    data = json.loads(json_str)
    assert 146 in [c["chain_id"] for c in data["chains"]]
    assert len(data["uncovered_chains"]) == 1
    assert "contestable" in data["market_note"]


def test_report_with_l1_warning():
    store = Store(":memory:")
    store.insert_range(8453, 100, 200)

    val_res = ValidationResult(
        8453,
        total_sampled=5,
        gas_checks_passed=5,
        transfer_checks_passed=5,
        l1_fee_share=Decimal("0.185"),
        l1_warnings=["average L1 fee is 18.5% of execution fee (>10%); gas is underestimated on this chain"],
    )

    report_data = generate_report(
        chain_ids=[8453],
        from_blocks={8453: 100},
        to_blocks={8453: 200},
        store=store,
        valued_events={8453: []},
        validation_results={8453: val_res},
        eurusd=Decimal("1.20"),
        threshold_eur=Decimal("300"),
        days=90,
    )

    text = report_data.to_text()
    assert "VALIDATION WARNINGS:" in text
    assert "underestimated" in text
    assert "18.5%" in text

    json_str = report_data.to_json()
    data = json.loads(json_str)
    assert data["chains"][0]["validation"]["l1_fee_share"] == "0.185"
    assert len(data["chains"][0]["validation"]["l1_warnings"]) == 1


def test_event_on_month_boundary_is_counted_once():
    store = Store(":memory:")
    store.insert_range(146, 100, 200)
    end = 1_700_000_000
    boundary = end - 30 * 86400
    items = [_make_valued(net_usd=Decimal("100"), timestamp=boundary, tx_hash="0xb")]
    rep = generate_report(
        chain_ids=[146], from_blocks={146: 100}, to_blocks={146: 200}, store=store,
        valued_events={146: items}, validation_results={146: ValidationResult(146)},
        eurusd=Decimal("1"), days=90, end_ts=end,
    )
    months = rep.chains[0].months
    assert sum(m.event_count for m in months) == 1


def test_months_are_anchored_at_window_end_not_last_event():
    store = Store(":memory:")
    store.insert_range(146, 100, 200)
    end = 1_700_000_000
    # Only event is 45 days before the window end: it belongs to the second month.
    items = [_make_valued(net_usd=Decimal("100"), timestamp=end - 45 * 86400, tx_hash="0xm")]
    rep = generate_report(
        chain_ids=[146], from_blocks={146: 100}, to_blocks={146: 200}, store=store,
        valued_events={146: items}, validation_results={146: ValidationResult(146)},
        eurusd=Decimal("1"), days=90, end_ts=end,
    )
    counts = [m.event_count for m in sorted(rep.chains[0].months, key=lambda m: m.month_index)]
    assert counts == [0, 1, 0]


def test_last_fetched_block():
    store = Store(":memory:")
    assert store.last_fetched_block(146) is None
    store.insert_range(146, 100, 200)
    store.insert_range(146, 201, 350)
    store.insert_range(42161, 1, 999)
    assert store.last_fetched_block(146) == 350
