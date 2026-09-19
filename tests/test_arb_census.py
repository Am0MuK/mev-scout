"""Tests for Phase 2A arbitrage census: cycle detection, valuation, reporting, validation."""

from decimal import Decimal
import json
from pathlib import Path
import pytest

from mev_scout.arb_census import (
    ArbCensusReport,
    DetectedArbitrage,
    bucket_for_arb_profit,
    detect_arbitrages,
    generate_arb_census_report,
    validate_arbitrages,
    value_arbitrages,
)
from mev_scout.cli import main
from mev_scout.dex import ARBITRUM_TOKENS, Pool
from mev_scout.store import Store
from mev_scout.swaps import DecodedSwap


class FakeRpcForCensus:
    def __init__(self, receipts=None):
        self.receipts = receipts or {}

    def receipt(self, tx_hash: str) -> dict:
        return self.receipts.get(
            tx_hash.lower(),
            {
                "from": "0x1111111111111111111111111111111111111111",
                "to": "0x2222222222222222222222222222222222222222",
                "gasUsed": hex(100_000),
                "effectiveGasPrice": hex(10_000_000_000),  # 10 gwei -> 0.001 WETH (~$2.63)
                "logs": [],
            },
        )


def _make_pool_weth_usdc(dex: str, addr: str) -> Pool:
    weth = ARBITRUM_TOKENS["WETH"].address
    usdc = ARBITRUM_TOKENS["USDC"].address
    return Pool(
        chain_id=42161,
        dex=dex,
        address=addr.lower(),
        token0=min(weth.lower(), usdc.lower()),
        token1=max(weth.lower(), usdc.lower()),
        fee=500,
    )


def test_detect_two_pool_cycle():
    """Two-pool cycle where net flow >= 0 for all and > 0 for one is detected."""
    pool_uni = _make_pool_weth_usdc("uniswap_v3", "0xc6962004f452be9203591991d15f6b388e09e8d0")
    pool_sushi = _make_pool_weth_usdc("sushiswap_v3", "0xf3eb87c1f6020982173c908e7eb31aa66c1f0296")
    pools = {pool_uni.address.lower(): pool_uni, pool_sushi.address.lower(): pool_sushi}

    # token0 is WETH, token1 is USDC
    # Swap 1 on Uni: paid 1,000 USDC (+1,000*1e6), got 0.5 WETH (-0.5*1e18)
    swap1 = DecodedSwap(
        chain_id=42161,
        dex="uniswap_v3",
        pool=pool_uni.address,
        block=506700100,
        timestamp=1727000100,
        tx_hash="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        log_index=1,
        sender="0xbot",
        recipient="0xbot",
        amount0=-500_000_000_000_000_000,
        amount1=1_000_000_000,
        sqrt_price_x96=4061072399780078164115456,
        liquidity=100000,
        tick=-197583,
    )
    # Swap 2 on Sushi: paid 0.5 WETH (+0.5*1e18), got 1,020 USDC (-1,020*1e6)
    swap2 = DecodedSwap(
        chain_id=42161,
        dex="sushiswap_v3",
        pool=pool_sushi.address,
        block=506700100,
        timestamp=1727000100,
        tx_hash="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        log_index=2,
        sender="0xbot",
        recipient="0xbot",
        amount0=500_000_000_000_000_000,
        amount1=-1_020_000_000,
        sqrt_price_x96=4061072399780078164115456,
        liquidity=100000,
        tick=-197583,
    )

    arbs = detect_arbitrages([swap1, swap2], pools=pools)
    assert len(arbs) == 1
    arb = arbs[0]
    assert arb.tx_hash == swap1.tx_hash
    assert len(arb.swaps) == 2
    usdc_addr = ARBITRUM_TOKENS["USDC"].address.lower()
    weth_addr = ARBITRUM_TOKENS["WETH"].address.lower()
    assert arb.net_token_flows[weth_addr] == 0
    assert arb.net_token_flows[usdc_addr] == 20_000_000  # +20 USDC


def test_detect_three_pool_cycle():
    """Three-pool triangular arbitrage cycle."""
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    arb_tok = ARBITRUM_TOKENS["ARB"].address.lower()

    pool1 = Pool(42161, "uniswap_v3", "0x1111", min(weth, usdc), max(weth, usdc), 500)
    pool2 = Pool(42161, "sushiswap_v3", "0x2222", min(arb_tok, weth), max(arb_tok, weth), 500)
    pool3 = Pool(42161, "pancakeswap_v3", "0x3333", min(arb_tok, usdc), max(arb_tok, usdc), 500)
    pools = {p.address: p for p in [pool1, pool2, pool3]}

    tx = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

    # Pool 1 (WETH/USDC): token0 is WETH, token1 is USDC. Paid 1000 USDC, got 0.5 WETH.
    s1 = DecodedSwap(42161, "uniswap_v3", "0x1111", 100, 1000, tx, 1, "0xbot", "0xbot", -500_000_000_000_000_000, 1_000_000_000, 4061072399780078164115456, 100, -197583)
    # Pool 2 (ARB/WETH): arb_tok (0x912ce...) > weth (0x82af...), so token0 is WETH, token1 is ARB.
    # Paid 0.5 WETH, got 1000 ARB.
    s2 = DecodedSwap(42161, "sushiswap_v3", "0x2222", 100, 1000, tx, 2, "0xbot", "0xbot", 500_000_000_000_000_000, -1_000_000_000_000_000_000, 4061072399780078164115456, 100, -197583)
    # Pool 3 (ARB/USDC): arb_tok (0x912c...) < usdc (0xaf88...), so token0 is ARB, token1 is USDC.
    # Paid 1000 ARB, got 1030 USDC.
    s3 = DecodedSwap(42161, "pancakeswap_v3", "0x3333", 100, 1000, tx, 3, "0xbot", "0xbot", 1_000_000_000_000_000_000, -1_030_000_000, 4061072399780078164115456, 100, -197583)

    arbs = detect_arbitrages([s1, s2, s3], pools=pools)
    assert len(arbs) == 1
    assert arbs[0].net_token_flows[weth] == 0
    assert arbs[0].net_token_flows[arb_tok] == 0
    assert arbs[0].net_token_flows[usdc] == 30_000_000  # +30 USDC


def test_non_cycle_multihop_rejected():
    """Multi-hop swap that ends in a different token is not an arbitrage."""
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    arb_tok = ARBITRUM_TOKENS["ARB"].address.lower()

    pool1 = Pool(42161, "uniswap_v3", "0x1111", min(weth, usdc), max(weth, usdc), 500)
    pool2 = Pool(42161, "sushiswap_v3", "0x2222", min(arb_tok, weth), max(arb_tok, weth), 500)
    pools = {p.address: p for p in [pool1, pool2]}

    tx = "0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
    # Swap 1: USDC -> WETH (paid 1000 USDC, got 0.5 WETH)
    s1 = DecodedSwap(42161, "uniswap_v3", "0x1111", 100, 1000, tx, 1, "0xbot", "0xbot", -500_000_000_000_000_000, 1_000_000_000, 4061072399780078164115456, 100, -197583)
    # Swap 2: WETH -> ARB (paid 0.5 WETH, got 1000 ARB)
    s2 = DecodedSwap(42161, "sushiswap_v3", "0x2222", 100, 1000, tx, 2, "0xbot", "0xbot", 500_000_000_000_000_000, -1_000_000_000_000_000_000, 4061072399780078164115456, 100, -197583)

    arbs = detect_arbitrages([s1, s2], pools=pools)
    # Fails because USDC net flow is -1000 (< 0)
    assert len(arbs) == 0


def test_negative_net_arbitrage_kept():
    """Negative-net arbitrage (gross profit < gas) is kept and counted."""
    pool_uni = _make_pool_weth_usdc("uniswap_v3", "0xc6962004f452be9203591991d15f6b388e09e8d0")
    pool_sushi = _make_pool_weth_usdc("sushiswap_v3", "0xf3eb87c1f6020982173c908e7eb31aa66c1f0296")
    pools = {pool_uni.address.lower(): pool_uni, pool_sushi.address.lower(): pool_sushi}

    tx = "0xdddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    # Gross profit is only $1 USDC
    s1 = DecodedSwap(42161, "uniswap_v3", pool_uni.address, 100, 1000, tx, 1, "0xbot", "0xbot", -500_000_000_000_000_000, 1_000_000_000, 4061072399780078164115456, 100, -197583)
    s2 = DecodedSwap(42161, "sushiswap_v3", pool_sushi.address, 100, 1000, tx, 2, "0xbot", "0xbot", 500_000_000_000_000_000, -1_001_000_000, 4061072399780078164115456, 100, -197583)

    raw_arbs = detect_arbitrages([s1, s2], pools=pools)
    assert len(raw_arbs) == 1

    # Receipt with gas = 300,000 gas @ 20 gwei = 0.006 WETH (~$15.76)
    rpc = FakeRpcForCensus({
        tx: {
            "from": "0xbot_eoa",
            "to": "0xbot_contract",
            "gasUsed": hex(300_000),
            "effectiveGasPrice": hex(20_000_000_000),
            "logs": [],
        }
    })
    store = Store(":memory:")
    valued_arbs = value_arbitrages(raw_arbs, rpc=rpc, pools=pools, store=store)
    assert len(valued_arbs) == 1
    v = valued_arbs[0]
    assert v.gross_usd == Decimal("1")
    assert v.gas_usd > Decimal("10")
    assert v.net_usd < Decimal("0")  # Negative net profit!
    assert v.bot_from.lower() == "0xbot_eoa".lower()
    assert v.contract_to.lower() == "0xbot_contract".lower()


def test_bucket_for_arb_profit():
    assert bucket_for_arb_profit(Decimal("5")) == "<10"
    assert bucket_for_arb_profit(Decimal("10")) == "10–100"
    assert bucket_for_arb_profit(Decimal("50")) == "10–100"
    assert bucket_for_arb_profit(Decimal("100")) == "100–1k"
    assert bucket_for_arb_profit(Decimal("500")) == "100–1k"
    assert bucket_for_arb_profit(Decimal("1000")) == "≥1k"
    assert bucket_for_arb_profit(Decimal("5000")) == "≥1k"


def test_validate_arbitrages_transfer_logs():
    """Fixed-seed sample recomputes net token flow from receipt Transfer logs."""
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    tx = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    bot_contract = "0x2222222222222222222222222222222222222222"
    pool_uni = "0xc6962004f452be9203591991d15f6b388e09e8d0"

    # Transfer event topic0
    transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    # Receipt with Transfer logs matching net flow: +20 USDC
    receipt = {
        "from": "0x1111111111111111111111111111111111111111",
        "to": bot_contract,
        "gasUsed": hex(100_000),
        "effectiveGasPrice": hex(10_000_000_000),
        "logs": [
            # Bot paid 1,000 USDC out to pool
            {
                "address": usdc,
                "topics": [transfer_topic, "0x" + "0"*24 + bot_contract[2:], "0x" + "0"*24 + pool_uni[2:]],
                "data": hex(1_000_000_000),
            },
            # Bot received 1,020 USDC from other pool
            {
                "address": usdc,
                "topics": [transfer_topic, "0x" + "0"*24 + pool_uni[2:], "0x" + "0"*24 + bot_contract[2:]],
                "data": hex(1_020_000_000),
            },
        ],
    }

    arb = DetectedArbitrage(
        tx_hash=tx,
        block=506700100,
        timestamp=1727000100,
        swaps=[],
        net_token_flows={usdc: 20_000_000},
        gross_usd=Decimal("20"),
        gas_usd=Decimal("2.63"),
        net_usd=Decimal("17.37"),
        bot_from="0x1111111111111111111111111111111111111111",
        contract_to=bot_contract,
    )

    rpc = FakeRpcForCensus({tx: receipt})
    res = validate_arbitrages([arb], rpc=rpc, seed=42, max_sample=20)
    assert res.total_sampled == 1
    assert res.checks_passed == 1
    assert res.checks_failed == 0


def test_arb_census_report_generation():
    """Report computes count, gross, gas, net, concentration, buckets, and verdict."""
    tx1 = "0x1111"
    tx2 = "0x2222"
    now_ts = 1727000000

    arb1 = DetectedArbitrage(
        tx_hash=tx1,
        block=506700100,
        timestamp=now_ts - 5 * 86400,
        swaps=[],
        net_token_flows={},
        gross_usd=Decimal("500"),
        gas_usd=Decimal("50"),
        net_usd=Decimal("450"),
        bot_from="0xbot1",
        contract_to="0xcontract1",
    )
    arb2 = DetectedArbitrage(
        tx_hash=tx2,
        block=506700200,
        timestamp=now_ts - 35 * 86400,
        swaps=[],
        net_token_flows={},
        gross_usd=Decimal("400"),
        gas_usd=Decimal("40"),
        net_usd=Decimal("360"),
        bot_from="0xbot2",
        contract_to="0xcontract2",
    )

    report = generate_arb_census_report(
        chain_id=42161,
        arbitrages=[arb1, arb2],
        eurusd=Decimal("1.1460"),
        threshold_eur=Decimal("300"),
        days=90,
        end_ts=now_ts,
    )
    assert report.chain_id == 42161
    assert report.total_arbitrages == 2
    assert report.distinct_bots == 2
    text = report.to_text()
    assert "DEX Arbitrage Census" in text
    assert "Verdict" in text
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["chain_id"] == 42161
    assert data["total_arbitrages"] == 2


# --- no silent guesses (Claude review 2026-09-19) ---------------------------------

WETH = ARBITRUM_TOKENS["WETH"].address
WBTC = ARBITRUM_TOKENS["WBTC"].address
USDC = ARBITRUM_TOKENS["USDC"].address


def _wbtc_weth_cycle(ts=1000):
    p1 = Pool(42161, "uniswap_v3", "0x" + "a1" * 20, WBTC, WETH, 500)
    p2 = Pool(42161, "sushiswap_v3", "0x" + "a2" * 20, WBTC, WETH, 500)
    tx = "0x" + "ee" * 32
    # WETH in on p1 (bot pays 1 WETH, gets WBTC), WBTC back into p2 for 1.01 WETH.
    s1 = DecodedSwap(42161, "uniswap_v3", p1.address, 100, ts, tx, 1, "0xb", "0xb", -4_000_000, 10**18, 2**96, 1, 0)
    s2 = DecodedSwap(42161, "sushiswap_v3", p2.address, 100, ts, tx, 2, "0xb", "0xb", 4_000_000, -(101 * 10**16), 2**96, 1, 0)
    pools = {p1.address: p1, p2.address: p2}
    return detect_arbitrages([s1, s2], pools=pools), pools


def test_no_weth_price_available_marks_arbitrage_unpriced_not_2600():
    arbs, pools = _wbtc_weth_cycle()
    assert len(arbs) == 1
    v = value_arbitrages(arbs, rpc=FakeRpcForCensus(), pools=pools, store=Store(":memory:"))[0]
    assert v.unpriced is True
    assert "WETH" in v.unpriced_reason


def test_stale_stable_swap_is_not_used_as_price():
    arbs, pools = _wbtc_weth_cycle(ts=10_000)
    stable = Pool(42161, "uniswap_v3", "0x" + "a3" * 20, WETH, USDC, 500)
    pools[stable.address] = stable
    store = Store(":memory:")
    # Last stable-pool swap is two hours before the arbitrage.
    store.insert_swaps([DecodedSwap(42161, "uniswap_v3", stable.address, 90, 10_000 - 7200, "0x" + "cc" * 32, 0,
                                    "0xu", "0xu", 10**18, -2600 * 10**6, 4061072399780078164115456, 1, 0)])
    v = value_arbitrages(arbs, rpc=FakeRpcForCensus(), pools=pools, store=store)[0]
    assert v.unpriced is True


def test_receipt_without_gas_fields_marks_arbitrage_unpriced():
    arbs, pools = _wbtc_weth_cycle()
    stable = Pool(42161, "uniswap_v3", "0x" + "a3" * 20, WETH, USDC, 500)
    pools[stable.address] = stable
    store = Store(":memory:")
    store.insert_swaps([DecodedSwap(42161, "uniswap_v3", stable.address, 99, 999, "0x" + "cd" * 32, 0,
                                    "0xu", "0xu", 10**18, -2600 * 10**6, 4061072399780078164115456, 1, 0)])
    rpc = FakeRpcForCensus({arbs[0].tx_hash: {"from": "0xa", "to": "0xb", "logs": []}})
    v = value_arbitrages(arbs, rpc=rpc, pools=pools, store=store)[0]
    assert v.unpriced is True
    assert "gas" in v.unpriced_reason


def test_report_totals_exclude_unpriced_and_count_them():
    priced = DetectedArbitrage("0x1", 1, 100, [], {}, gross_usd=Decimal("10"), gas_usd=Decimal("1"),
                               net_usd=Decimal("9"), bot_from="0xa")
    unpriced = DetectedArbitrage("0x2", 2, 200, [], {}, bot_from="0xb", unpriced=True, unpriced_reason="no WETH price")
    rep = generate_arb_census_report(42161, [priced, unpriced], eurusd=Decimal("1"), end_ts=1000)
    assert rep.net_usd == Decimal("9")
    assert rep.unpriced_count == 1
    assert "unpriced" in rep.to_text().lower()
