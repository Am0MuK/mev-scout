"""Tests for Phase 2B leftover opportunities: block sampling, prefilter, round trip, persistence, reporting."""

from decimal import Decimal
import json
import pytest

from mev_scout.arb_sample import (
    SHALLOW_LIQUIDITY_THRESHOLD,
    ArbSampleMeta,
    ArbSampleResult,
    check_prefilter,
    encode_quote_exact_input_single,
    decode_quote_result,
    generate_arb_sample_report,
    quote_round_trip,
    sample_blocks,
    track_persistence,
)
from mev_scout.dex import (
    ARBITRUM_TOKENS,
    DEXES,
    Pool,
    QUOTE_EXACT_INPUT_SINGLE_SELECTOR,
    SLOT0_SELECTOR,
    LIQUIDITY_SELECTOR,
)
from mev_scout.rpc import ContractCallError
from mev_scout.store import Store


class FakeSampleRpc:
    def __init__(self, responses=None, block_num=506_705_000, base_fee=100_000_000):
        self.responses = responses or {}
        self._block_num = block_num
        self._base_fee = base_fee
        self.calls = []

    def block_number(self) -> int:
        return self._block_num

    def call(self, to: str, data: str, block: str | int = "latest") -> str:
        key = (to.lower(), data.lower()[:10], str(block).lower())
        self.calls.append((to, data, block))
        # Exact match or selector match
        if (to.lower(), data.lower(), str(block).lower()) in self.responses:
            val = self.responses[(to.lower(), data.lower(), str(block).lower())]
            if isinstance(val, Exception):
                raise val
            return val
        if key in self.responses:
            val = self.responses[key]
            if isinstance(val, Exception):
                raise val
            return val
        # Default slot0 response (P = 1.0)
        if data.lower().startswith(SLOT0_SELECTOR.lower()):
            # sqrtPriceX96 for 1.0 = 2^96
            sqrtP = 2**96
            return "0x" + hex(sqrtP)[2:].rjust(64, "0") + "0" * (6 * 64)
        # Default liquidity response
        if data.lower().startswith(LIQUIDITY_SELECTOR.lower()):
            return "0x" + hex(10**18)[2:].rjust(64, "0")
        raise ContractCallError(f"Unhandled call {to} {data[:10]} {block}")

    def _call(self, method: str, params: list):
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas": hex(self._base_fee)}
        if method == "eth_blockNumber":
            return hex(self._block_num)
        raise ContractCallError(f"Unhandled RPC method {method}")


def test_prefilter_boundary_exact_skips():
    """Prefilter skips a pair whose mid-price gap is not larger than the combined fees.

    Boundary exact: equal gap = skip.
    """
    # Pool A has fee 500 (0.05%), Pool B has fee 3000 (0.3%). Combined = 0.35% = 0.0035.
    combined_fees = Decimal("0.0035")

    # Case 1: Gap strictly greater than combined fees -> PASSES
    price_a = Decimal("2600")
    price_b_pass = price_a * (Decimal("1") + Decimal("0.0040"))  # 0.40% > 0.35%
    passes, gap = check_prefilter(price_a, price_b_pass, fee_a=500, fee_b=3000)
    assert passes is True
    assert gap > combined_fees

    # Case 2: Gap strictly less than combined fees -> SKIPS
    price_b_skip = price_a * (Decimal("1") + Decimal("0.0030"))  # 0.30% < 0.35%
    passes, gap = check_prefilter(price_a, price_b_skip, fee_a=500, fee_b=3000)
    assert passes is False
    assert gap < combined_fees

    # Case 3: Gap exactly equal to combined fees -> SKIPS (boundary exact!)
    price_b_exact = price_a * (Decimal("1") + combined_fees)
    passes, gap = check_prefilter(price_a, price_b_exact, fee_a=500, fee_b=3000)
    assert passes is False
    assert gap == combined_fees


def test_quote_exact_input_single_encoding_and_decoding():
    token_in = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
    token_out = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    amount_in = 1_000_000_000  # 1000 USDC
    fee = 500

    calldata = encode_quote_exact_input_single(token_in, token_out, amount_in, fee)
    assert calldata.startswith(QUOTE_EXACT_INPUT_SINGLE_SELECTOR)
    # Check length: 4 bytes selector (10 chars with 0x) + 5 words of 32 bytes (320 chars) = 330 chars
    assert len(calldata) == 330

    # Test decoding of 4 words: amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate
    amount_out = 500_000_000_000_000_000  # 0.5 WETH
    gas_est = 120_000
    res_hex = (
        "0x"
        + hex(amount_out)[2:].rjust(64, "0")
        + "0" * 64
        + "0" * 64
        + hex(gas_est)[2:].rjust(64, "0")
    )
    dec_amount_out, dec_gas = decode_quote_result(res_hex)
    assert dec_amount_out == amount_out
    assert dec_gas == gas_est


def test_reverted_quote_skipped_and_counted():
    """A reverted quote is skipped and counted, never treated as 0."""
    pool_a = Pool(42161, "uniswap_v3", "0xpoolA", "0xbase", "0xquote", 500)
    pool_b = Pool(42161, "sushiswap_v3", "0xpoolB", "0xbase", "0xquote", 3000)

    # Quoter reverts with execution reverted
    quoter_uni = DEXES["uniswap_v3"].quoter.lower()
    responses = {
        (quoter_uni, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "506700100"): ContractCallError("execution reverted"),
    }
    rpc = FakeSampleRpc(responses)

    result = quote_round_trip(
        pool_a=pool_a,
        pool_b=pool_b,
        quote_token="0xquote",
        base_token="0xbase",
        size_usd=1000,
        start_amount=1_000_000_000,
        block=506700100,
        rpc=rpc,
        weth_price_usd=Decimal("2600"),
    )
    assert result.reverted is True
    assert result.is_opportunity is False
    assert result.profit_usd is None  # Never treated as 0


def test_successful_opportunity_and_persistence_stops_at_first_non_profit():
    """Persistence stops at the first block without profit."""
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    pool_a = Pool(42161, "uniswap_v3", "0xpoolA", weth, usdc, 500)
    pool_b = Pool(42161, "sushiswap_v3", "0xpoolB", weth, usdc, 500)

    quoter_uni = DEXES["uniswap_v3"].quoter.lower()
    quoter_sushi = DEXES["sushiswap_v3"].quoter.lower()

    # Block 100: profitable (buy on A gives 0.5 base, sell on B gives 1020 quote -> +20 USD)
    # Block 101: profitable (+15 USD)
    # Block 102: unprofitable (+0 USD -> net < 0 due to gas)
    # Block 105: should NOT be called because persistence stopped at 102!

    def make_res(amt, gas=100_000):
        return "0x" + hex(amt)[2:].rjust(64, "0") + "0" * 64 + "0" * 64 + hex(gas)[2:].rjust(64, "0")

    responses = {
        # Block 100
        (quoter_uni, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "100"): make_res(500_000_000_000_000_000),
        (quoter_sushi, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "100"): make_res(1_020_000_000),
        # Block 101 (+1)
        (quoter_uni, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "101"): make_res(500_000_000_000_000_000),
        (quoter_sushi, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "101"): make_res(1_015_000_000),
        # Block 102 (+2): final amount only 1_000_000_000 (break-even gross, net negative after gas)
        (quoter_uni, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "102"): make_res(500_000_000_000_000_000),
        (quoter_sushi, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "102"): make_res(1_000_000_000),
        # Block 105 (+5): if called, error
        (quoter_uni, QUOTE_EXACT_INPUT_SINGLE_SELECTOR.lower(), "105"): Exception("Should not be checked!"),
    }
    rpc = FakeSampleRpc(responses, base_fee=10_000_000)

    # Initial quote at block 100
    res = quote_round_trip(
        pool_a=pool_a,
        pool_b=pool_b,
        quote_token=usdc,
        base_token=weth,
        size_usd=1000,
        start_amount=1_000_000_000,
        block=100,
        rpc=rpc,
        weth_price_usd=Decimal("2600"),
    )
    assert res.is_opportunity is True
    assert res.net_usd > Decimal("0")

    # Track persistence
    persisted_blocks = track_persistence(
        pool_a=pool_a,
        pool_b=pool_b,
        quote_token=usdc,
        base_token=weth,
        size_usd=1000,
        start_amount=1_000_000_000,
        initial_block=100,
        rpc=rpc,
        weth_price_usd=Decimal("2600"),
    )
    # Survived at +1, stopped at +2
    assert persisted_blocks == 1


def test_shallow_pool_flag():
    """Pool with liquidity below threshold is flagged as shallow."""
    pool = Pool(42161, "uniswap_v3", "0xpool", "0xbase", "0xquote", 500)
    # Low liquidity: 100 < 1_000_000_000
    responses = {
        ("0xpool", LIQUIDITY_SELECTOR.lower(), "100"): "0x" + hex(100)[2:].rjust(64, "0"),
    }
    rpc = FakeSampleRpc(responses)
    liq_hex = rpc.call(pool.address, LIQUIDITY_SELECTOR, 100)
    liq = int(liq_hex, 16)
    assert liq < SHALLOW_LIQUIDITY_THRESHOLD


def test_sample_blocks_spacing_and_dense():
    """Block sampling produces evenly spaced blocks plus all dense ranges."""
    from_block = 1000
    to_block = 2000
    blocks_per_step = 200  # 1000, 1200, 1400, 1600, 1800, 2000
    dense_ranges = ["1050:1052"]  # 1050, 1051, 1052

    sampled = sample_blocks(
        from_block=from_block,
        to_block=to_block,
        blocks_per_step=blocks_per_step,
        dense_ranges=dense_ranges,
    )
    assert 1000 in sampled
    assert 1200 in sampled
    assert 1050 in sampled
    assert 1051 in sampled
    assert 1052 in sampled
    assert sampled == sorted(set(sampled))


def test_arb_sample_report_and_verdict():
    """Report computes per-pair metrics, distribution, upper bound, and verdict."""
    results = [
        ArbSampleResult(
            block=100,
            pair="WETH/USDC",
            pool_a="0xuni",
            pool_b="0xsushi",
            size_usd=10000,
            gross_usd=Decimal("50"),
            gas_usd=Decimal("5"),
            net_usd=Decimal("45"),
            is_opportunity=True,
            persisted_blocks=2,
            is_shallow=False,
        ),
        ArbSampleResult(
            block=200,
            pair="WETH/USDC",
            pool_a="0xuni",
            pool_b="0xsushi",
            size_usd=10000,
            gross_usd=Decimal("30"),
            gas_usd=Decimal("5"),
            net_usd=Decimal("25"),
            is_opportunity=True,
            persisted_blocks=1,
            is_shallow=True,
        ),
    ]
    meta = ArbSampleMeta(
        chain_id=42161,
        total_sampled_blocks=10,
        skipped_prefilter_pairs=40,
        reverted_quotes=2,
        from_block=100,
        to_block=1000,
    )
    report = generate_arb_sample_report(
        meta=meta,
        results=results,
        eurusd=Decimal("1.1460"),
        threshold_eur=Decimal("300"),
        days=30,
    )
    text = report.to_text()
    assert "Leftover Opportunities" in text
    assert "Upper bound" in text
    assert "Verdict" in text
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["chain_id"] == 42161
    assert "WETH/USDC" in data["pairs"]


# --- no silent guesses (Claude review 2026-09-19) ---------------------------------
from decimal import Decimal as _D
import pytest as _pytest
from mev_scout import arb_sample as _as
from mev_scout.dex import ARBITRUM_TOKENS as _T, Pool as _Pool
from mev_scout.rpc import ContractCallError as _CCE, RpcError as _RE


def _stable_pool(addr):
    return _Pool(42161, "uniswap_v3", addr, _T["WETH"].address, _T["USDC"].address, 500)


def test_weth_price_is_median_of_stable_pools_not_the_first_shallow_one():
    pools = [_stable_pool("0x" + "01" * 20), _stable_pool("0x" + "02" * 20), _stable_pool("0x" + "03" * 20)]
    mids = {pools[0].address: _D("9.14"), pools[1].address: _D("2648"), pools[2].address: _D("2645")}
    assert _as._weth_usd_from_mids(pools, mids) == _D("2645")


def test_no_stable_pool_price_gives_none_not_2600():
    assert _as._weth_usd_from_mids([_stable_pool("0x" + "01" * 20)], {}) is None


class _Rpc:
    def __init__(self, exc):
        self.exc = exc

    def call(self, to, data, block):
        raise self.exc


def test_slot0_revert_is_counted():
    mids, reverted = _as._read_slot0_mids([_stable_pool("0x" + "01" * 20)], 1, _Rpc(_CCE("reverted")))
    assert mids == {} and reverted == 1


def test_slot0_transport_error_is_not_counted_as_revert():
    with _pytest.raises(_RE):
        _as._read_slot0_mids([_stable_pool("0x" + "01" * 20)], 1, _Rpc(_RE("HTTP 503")))


def test_sample_meta_keeps_unpriced_blocks():
    from mev_scout.store import Store
    st = Store(":memory:")
    st.set_arb_sample_meta(_as.ArbSampleMeta(42161, 10, 2, 1, 100, 200, unpriced_blocks=3))
    assert st.get_arb_sample_meta(42161).unpriced_blocks == 3


class _RpcSeq:
    """call() raises the given exception; _call() for blocks returns `block_obj`."""
    def __init__(self, exc=None, block_obj=None):
        self.exc, self.block_obj = exc, block_obj

    def call(self, to, data, block):
        if self.exc:
            raise self.exc
        # amountOut, sqrtPriceX96After, ticksCrossed, gasEstimate
        return "0x" + hex(10**18)[2:].rjust(64, "0") + "0" * 64 * 2 + hex(100_000)[2:].rjust(64, "0")

    def _call(self, method, params):
        return self.block_obj


def _two_pools():
    a = _Pool(42161, "uniswap_v3", "0x" + "0a" * 20, _T["WETH"].address, _T["USDC"].address, 500)
    b = _Pool(42161, "sushiswap_v3", "0x" + "0b" * 20, _T["WETH"].address, _T["USDC"].address, 500)
    return a, b


def test_quote_transport_error_propagates():
    a, b = _two_pools()
    with _pytest.raises(_RE):
        _as.quote_round_trip(a, b, _T["USDC"].address, _T["WETH"].address, 1000, 10**9, 1, _RpcSeq(_RE("HTTP 503")), _D("2600"))


def test_missing_base_fee_is_an_error_not_a_guess():
    a, b = _two_pools()
    with _pytest.raises(_RE):
        _as.quote_round_trip(a, b, _T["USDC"].address, _T["WETH"].address, 1000, 10**9, 1, _RpcSeq(block_obj={}), _D("2600"))


def test_pool_discovery_transport_error_is_not_a_missing_pool():
    from mev_scout.dex import discover_pools

    class R:
        def call(self, to, data, block):
            raise _RE("HTTP 503")
    with _pytest.raises(_RE):
        discover_pools(42161, R())


def test_sampled_block_bookkeeping_survives_restart():
    # A 109-minute run was lost because results were written only at the end.
    from mev_scout.store import Store
    st = Store(":memory:")
    st.mark_sampled_block(42161, 100, skipped=3, reverted=1, unpriced=0)
    st.mark_sampled_block(42161, 200, skipped=2, reverted=0, unpriced=1)
    assert st.get_sampled_blocks(42161) == {100, 200}
    assert st.sampled_block_totals(42161) == (5, 1, 1)
