"""Phase 2C — Cross-chain price-gap census (inventory arbitrage).

Multi-chain venue definitions, adapters, pool discovery, and cross-chain quoting.
"""

import csv
import io
import json
import sys
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mev_scout.dex import TokenConfig
from mev_scout.report import evaluate_verdict
from mev_scout.rpc import ContractCallError, RpcClient, RpcError

# Selectors
UNISWAP_GET_POOL_SELECTOR = "0x1698ee82"
UNISWAP_QUOTE_SELECTOR = "0xc6a5026a"
SLIPSTREAM_GET_POOL_SELECTOR = "0x28af8d0b"
SLIPSTREAM_QUOTE_SELECTOR = "0x9e7defe6"
CLASSIC_GET_POOL_SELECTOR = "0x79bc57d5"
CLASSIC_GET_AMOUNT_OUT_SELECTOR = "0xf140a35a"
SLOT0_SELECTOR = "0x3850c7bd"
TOKEN0_SELECTOR = "0x0dfe1681"
TICK_SPACING_SELECTOR = "0xd0c93a7c"
FEE_SELECTOR = "0xddca3f43"

# Gas oracle on OP-stack chains (Base & Optimism)
OP_GAS_ORACLE = "0x420000000000000000000000000000000000000F"
GET_L1_FEE_SELECTOR = "0x49948e0e"

# Tokens per chain (Arbitrum 42161, Base 8453, Optimism 10)
XCHAIN_TOKENS: dict[int, dict[str, TokenConfig]] = {
    42161: {
        "WETH": TokenConfig(
            symbol="WETH",
            address="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
            decimals=18,
        ),
        "USDC": TokenConfig(
            symbol="USDC",
            address="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            decimals=6,
        ),
    },
    8453: {
        "WETH": TokenConfig(
            symbol="WETH",
            address="0x4200000000000000000000000000000000000006",
            decimals=18,
        ),
        "USDC": TokenConfig(
            symbol="USDC",
            address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            decimals=6,
        ),
    },
    10: {
        "WETH": TokenConfig(
            symbol="WETH",
            address="0x4200000000000000000000000000000000000006",
            decimals=18,
        ),
        "USDC": TokenConfig(
            symbol="USDC",
            address="0x0b2c639c533813f4aa9d7837caf62653d097ff85",
            decimals=6,
        ),
    },
}


@dataclass(frozen=True)
class VenueConfig:
    chain_id: int
    name: str
    factory: str
    quoter: str | None
    adapter_type: str  # "uniswap_v3", "slipstream", "aerodrome_classic"
    pool_keys: tuple[Any, ...]


# Verified venues from SPEC-phase2c-crosschain.md
XCHAIN_VENUES: list[VenueConfig] = [
    # Arbitrum 42161
    VenueConfig(
        chain_id=42161,
        name="uniswap_v3",
        factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        quoter="0x61ffe014ba17989e743c5f6cb21bf9697530b21e",
        adapter_type="uniswap_v3",
        pool_keys=(100, 500, 3000, 10000),
    ),
    VenueConfig(
        chain_id=42161,
        name="sushiswap_v3",
        factory="0x1af415a1eba07a4986a52b6f2e7de7003d82231e",
        quoter="0x0524e833ccd057e4d7a296e3aaab9f7675964ce1",
        adapter_type="uniswap_v3",
        pool_keys=(100, 500, 3000, 10000),
    ),
    VenueConfig(
        chain_id=42161,
        name="pancakeswap_v3",
        factory="0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865",
        quoter="0xb048bbc1ee6b733fffcfb9e9cef7375518e25997",
        adapter_type="uniswap_v3",
        pool_keys=(100, 500, 2500, 10000),
    ),
    # Base 8453
    VenueConfig(
        chain_id=8453,
        name="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
        quoter="0x3d4e44eb1374240ce5f1b871ab261cd16335b76a",
        adapter_type="uniswap_v3",
        pool_keys=(100, 500, 3000, 10000),
    ),
    VenueConfig(
        chain_id=8453,
        name="slipstream_1",
        factory="0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a",
        quoter="0x254cf9e1e6e233aa1ac962cb9b05b2cfeaae15b0",
        adapter_type="slipstream",
        pool_keys=(1, 50, 100, 200),
    ),
    VenueConfig(
        chain_id=8453,
        name="slipstream_gauge_caps",
        factory="0xade65c38cd4849adba595a4323a8c7ddfe89716a",
        quoter="0x3d4c22254f86f64b7ec90ab8f7aec1fbfd271c6c",
        adapter_type="slipstream",
        pool_keys=(1, 50, 100, 200),
    ),
    VenueConfig(
        chain_id=8453,
        name="slipstream_min_unstake",
        factory="0xf8f2eb4940cfe7d13603dddd87f123820fc061ef",
        quoter="0x514c8b5f54112481e28028f1166bd78501089259",
        adapter_type="slipstream",
        pool_keys=(1, 50, 100, 200),
    ),
    VenueConfig(
        chain_id=8453,
        name="aerodrome_classic",
        factory="0x420dd381b31aef6683db6b902084cb0ffece40da",
        quoter=None,
        adapter_type="aerodrome_classic",
        pool_keys=(False, True),
    ),
    # Optimism 10
    VenueConfig(
        chain_id=10,
        name="uniswap_v3",
        factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        quoter="0x61ffe014ba17989e743c5f6cb21bf9697530b21e",
        adapter_type="uniswap_v3",
        pool_keys=(100, 500, 3000, 10000),
    ),
    VenueConfig(
        chain_id=10,
        name="velodrome_slipstream",
        factory="0xcc0bddb707055e04e497ab22a59c2af4391cd12f",
        quoter="0x89d8218ed5ff1e46d8dcd33fb0bbee3be1621466",
        adapter_type="slipstream",
        pool_keys=(1, 50, 100, 200),
    ),
    VenueConfig(
        chain_id=10,
        name="aero_cl_optimism",
        factory="0x548118c7e0b865c2cfa94d15ec86b666468ac758",
        quoter="0xa2decf05c16537c702779083fe067e308463ce45",
        adapter_type="slipstream",
        pool_keys=(1, 50, 100, 200),
    ),
]

VENUE_BY_NAME: dict[tuple[int, str], VenueConfig] = {
    (v.chain_id, v.name): v for v in XCHAIN_VENUES
}

GAS_PRICE_ORACLE_ADDRESS = OP_GAS_ORACLE
DEFAULT_REBALANCE_PCT = Decimal("0.0005")  # 0.05%
DEFAULT_REBALANCE_FIXED_USD = Decimal("1.00")
OVERHEAD_GAS = 100_000
XCHAIN_SIZES_USD = (Decimal("1000"), Decimal("10000"), Decimal("50000"))

CANONICAL_DEEPEST_POOLS = {
    42161: "0xc6962004f452be9203591991d15f6b388e09e8d0",  # Uniswap V3 fee 500
    8453: "0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce",   # Uniswap V3 fee 100
    10: "0xc1738d90c0f3056157f44d8525b642674e2d2740",     # Uniswap V3 fee 3000
}

DEEPEST_POOL_FEES = {
    42161: 500,
    8453: 100,
    10: 3000,
}


@dataclass(frozen=True)

class XChainPool:
    chain_id: int
    venue_name: str
    address: str
    token0: str
    token1: str
    pool_key: Any  # fee (int), tickSpacing (int), or stable (bool)
    adapter_type: str
    factory: str
    quoter: str | None = None


def _clean_addr(addr: str) -> str:
    return addr.removeprefix("0x").removeprefix("0X").lower().rjust(64, "0")


def encode_get_pool(adapter_type: str, token_a: str, token_b: str, pool_key: Any) -> str:
    """Encode factory pool discovery call for the specified adapter type."""
    a = _clean_addr(token_a)
    b = _clean_addr(token_b)
    if adapter_type == "uniswap_v3":
        fee_hex = hex(int(pool_key)).removeprefix("0x").rjust(64, "0")
        return f"{UNISWAP_GET_POOL_SELECTOR}{a}{b}{fee_hex}"
    elif adapter_type == "slipstream":
        ts = int(pool_key)
        # int24 two's complement if negative, else positive
        if ts < 0:
            ts_hex = hex((1 << 256) + ts).removeprefix("0x")
        else:
            ts_hex = hex(ts).removeprefix("0x").rjust(64, "0")
        return f"{SLIPSTREAM_GET_POOL_SELECTOR}{a}{b}{ts_hex}"
    elif adapter_type == "aerodrome_classic":
        stb_hex = hex(1 if bool(pool_key) else 0).removeprefix("0x").rjust(64, "0")
        return f"{CLASSIC_GET_POOL_SELECTOR}{a}{b}{stb_hex}"
    else:
        raise ValueError(f"Unknown adapter_type: {adapter_type}")


def encode_quote(
    adapter_type: str,
    token_in: str,
    token_out: str,
    amount_in: int,
    pool_key: Any,
    sqrt_price_limit_x96: int = 0,
) -> str:
    """Encode quote call for the given adapter type."""
    clean_in = _clean_addr(token_in)
    clean_out = _clean_addr(token_out)
    clean_amt = hex(int(amount_in)).removeprefix("0x").rjust(64, "0")
    clean_lim = hex(int(sqrt_price_limit_x96)).removeprefix("0x").rjust(64, "0")

    if adapter_type == "uniswap_v3":
        clean_fee = hex(int(pool_key)).removeprefix("0x").rjust(64, "0")
        return f"{UNISWAP_QUOTE_SELECTOR}{clean_in}{clean_out}{clean_amt}{clean_fee}{clean_lim}"
    elif adapter_type == "slipstream":
        ts = int(pool_key)
        if ts < 0:
            clean_ts = hex((1 << 256) + ts).removeprefix("0x")
        else:
            clean_ts = hex(ts).removeprefix("0x").rjust(64, "0")
        return f"{SLIPSTREAM_QUOTE_SELECTOR}{clean_in}{clean_out}{clean_amt}{clean_ts}{clean_lim}"
    elif adapter_type == "aerodrome_classic":
        # Pool.getAmountOut(uint256 amountIn, address tokenIn)
        return f"{CLASSIC_GET_AMOUNT_OUT_SELECTOR}{clean_amt}{clean_in}"
    else:
        raise ValueError(f"Unknown adapter_type: {adapter_type}")


def decode_quote(adapter_type: str, data_hex: str) -> tuple[int, int]:
    """Decode quote result to (amount_out, gas_estimate)."""
    clean = data_hex.removeprefix("0x").removeprefix("0X")
    if adapter_type in ("uniswap_v3", "slipstream"):
        if len(clean) < 256:
            raise ValueError(f"Invalid quote result length: {len(clean)} chars")
        amount_out = int(clean[0:64], 16)
        gas_estimate = int(clean[192:256], 16)
        return amount_out, gas_estimate
    elif adapter_type == "aerodrome_classic":
        if len(clean) < 64:
            raise ValueError(f"Invalid classic quote result length: {len(clean)} chars")
        amount_out = int(clean[0:64], 16)
        return amount_out, 0
    else:
        raise ValueError(f"Unknown adapter_type: {adapter_type}")


def is_shallow_quote(amount_in_1x: int, quote_1x: int, quote_10x: int) -> bool:
    """Check whether a pool is shallow.

    A chosen pool must quote 10x the size at no worse than 2% below the 1x price.
    Returns True if shallow (should be skipped), False if deep enough.
    """
    if amount_in_1x <= 0 or quote_1x <= 0 or quote_10x <= 0:
        return True

    price_1x = Decimal(quote_1x) / Decimal(amount_in_1x)
    price_10x = Decimal(quote_10x) / Decimal(10 * amount_in_1x)

    # "at no worse than 2% below the 1x price"
    min_allowed_price = price_1x * Decimal("0.98")
    if price_10x < min_allowed_price:
        return True
    return False


def _execute_batch_or_single(
    rpc: RpcClient, calls: list[tuple[str, str, str | int]]
) -> list[Any]:
    if not calls:
        return []
    batch_fn = getattr(rpc, "batch_call", None) if hasattr(type(rpc), "batch_call") else None
    if callable(batch_fn):
        return batch_fn(calls)
    results = []
    for to, data, block in calls:
        try:
            results.append(rpc.call(to, data, block))
        except ContractCallError as exc:
            results.append(exc)
    return results


def discover_xchain_pools(
    chain_id: int,
    rpc: RpcClient,
    venues: list[VenueConfig] | None = None,
) -> list[XChainPool]:
    """Discover pools on `chain_id` across all venues for WETH/USDC.

    Verifies token0() and fee/tickSpacing on each pool.
    """
    if chain_id not in XCHAIN_TOKENS:
        raise ValueError(f"Chain {chain_id} not supported for cross-chain")

    if venues is None:
        venues = [v for v in XCHAIN_VENUES if v.chain_id == chain_id]

    tok_weth = XCHAIN_TOKENS[chain_id]["WETH"].address.lower()
    tok_usdc = XCHAIN_TOKENS[chain_id]["USDC"].address.lower()
    expected_token0 = min(tok_weth, tok_usdc)
    expected_token1 = max(tok_weth, tok_usdc)

    candidates = []
    calls = []
    for v in venues:
        for key in v.pool_keys:
            calldata = encode_get_pool(v.adapter_type, tok_weth, tok_usdc, key)
            candidates.append((v, key))
            calls.append((v.factory, calldata, "latest"))

    results = _execute_batch_or_single(rpc, calls)

    discovered_candidates = []
    verification_calls = []

    for (v, key), res in zip(candidates, results):
        if isinstance(res, ContractCallError):
            continue
        if isinstance(res, Exception):
            raise res
        if not isinstance(res, str):
            continue
        clean_res = res.removeprefix("0x").removeprefix("0X")
        if len(clean_res) < 40:
            continue
        addr = "0x" + clean_res[-40:].lower()
        if int(clean_res, 16) == 0:
            continue

        discovered_candidates.append((v, key, addr))
        verification_calls.append((addr, TOKEN0_SELECTOR, "latest"))
        if v.adapter_type == "uniswap_v3":
            verification_calls.append((addr, FEE_SELECTOR, "latest"))
        elif v.adapter_type == "slipstream":
            verification_calls.append((addr, TICK_SPACING_SELECTOR, "latest"))

    if not discovered_candidates:
        return []

    verify_results = _execute_batch_or_single(rpc, verification_calls)

    pools = []
    res_idx = 0
    for v, key, addr in discovered_candidates:
        tok0_res = verify_results[res_idx]
        res_idx += 1
        if isinstance(tok0_res, Exception):
            raise ValueError(f"Failed to query token0 for pool {addr}: {tok0_res}") from tok0_res
        clean_tok0 = tok0_res.removeprefix("0x").removeprefix("0X")
        actual_token0 = "0x" + clean_tok0[-40:].lower()
        if actual_token0 != expected_token0:
            raise ValueError(
                f"Pool verification failed for {addr}: "
                f"token0 mismatch (expected {expected_token0}, got {actual_token0})"
            )

        if v.adapter_type == "uniswap_v3":
            fee_res = verify_results[res_idx]
            res_idx += 1
            if isinstance(fee_res, Exception):
                raise ValueError(f"Failed to query fee for pool {addr}: {fee_res}") from fee_res
            actual_fee = int(fee_res.removeprefix("0x").removeprefix("0X"), 16)
            if actual_fee != int(key):
                raise ValueError(
                    f"Pool verification failed for {addr}: "
                    f"fee mismatch (expected {key}, got {actual_fee})"
                )
        elif v.adapter_type == "slipstream":
            ts_res = verify_results[res_idx]
            res_idx += 1
            if isinstance(ts_res, Exception):
                raise ValueError(f"Failed to query tickSpacing for pool {addr}: {ts_res}") from ts_res
            clean_ts = ts_res.removeprefix("0x").removeprefix("0X")
            raw_ts = int(clean_ts, 16)
            actual_ts = raw_ts - (1 << 256) if raw_ts >= (1 << 255) else raw_ts
            if actual_ts != int(key):
                raise ValueError(
                    f"Pool verification failed for {addr}: "
                    f"tickSpacing mismatch (expected {key}, got {actual_ts})"
                )

        pools.append(
            XChainPool(
                chain_id=chain_id,
                venue_name=v.name,
                address=addr,
                token0=expected_token0,
                token1=expected_token1,
                pool_key=key,
                adapter_type=v.adapter_type,
                factory=v.factory,
                quoter=v.quoter,
            )
        )

    return pools


def generate_time_grid(
    start_ts: int,
    end_ts: int,
    step_s: int = 300,
    dense_ranges: list[str] | None = None,
    dense_step_s: int = 60,
) -> list[int]:
    """Generate time grid: every `step_s` (default 5 min = 300 s), plus every `dense_step_s` (60 s) in dense ranges."""
    moments = set(range(start_ts, end_ts + 1, max(1, step_s)))
    if dense_ranges:
        for dr in dense_ranges:
            if ":" in dr:
                parts = dr.split(":")
                df, dt = int(parts[0]), int(parts[1])
                moments.update(range(df, dt + 1, max(1, dense_step_s)))
    return sorted(moments)


def find_block_by_timestamp(
    chain_id: int,
    target_ts: int,
    rpc: Any,
    store: Any = None,
    low: int | None = None,
    high: int | None = None,
) -> tuple[int, int]:
    """Find the last block with timestamp <= target_ts via binary search on eth_getBlockByNumber.

    Returns (block_number, block_timestamp).
    Uses caching (store and in-memory).
    """
    def _get_block_ts(b: int) -> int:
        if store is not None:
            cached = store.get_call_cache(chain_id, "block_timestamp", str(b), "0")
            if cached is not None:
                return int(cached)

        block_hex = hex(b)
        res = rpc._call("eth_getBlockByNumber", [block_hex, False])
        if not isinstance(res, dict) or res.get("timestamp") is None:
            raise RpcError(f"eth_getBlockByNumber failed or missing timestamp for block {b}")
        ts = int(str(res["timestamp"]), 0)
        if store is not None:
            store.set_call_cache(chain_id, "block_timestamp", str(b), "0", str(ts))
        return ts

    if high is None:
        high = rpc.block_number()
    if low is None:
        low = 1

    high_ts = _get_block_ts(high)
    if high_ts <= target_ts:
        return high, high_ts

    low_ts = _get_block_ts(low)
    if low_ts > target_ts:
        raise RpcError(f"No block with timestamp <= {target_ts} found (low block {low} has timestamp {low_ts})")

    best_block = low
    best_ts = low_ts
    l = low
    r = high

    while l <= r:
        mid = (l + r) // 2
        mid_ts = _get_block_ts(mid)
        if mid_ts <= target_ts:
            best_block = mid
            best_ts = mid_ts
            l = mid + 1
        else:
            r = mid - 1

    return best_block, best_ts


def check_moment_skew(
    moment: int,
    chain_blocks: dict[int, tuple[int, int]],
    max_age_s: int = 5,
) -> tuple[bool, int]:
    """Check skew guard: drop moment if any chain's block is more than 5 s older than moment.

    Returns (passes, skew_s) where skew_s is max(timestamps) - min(timestamps).
    """
    timestamps = [ts for _, ts in chain_blocks.values()]
    if not timestamps:
        return False, 0

    for _, ts in chain_blocks.values():
        if ts > moment:
            return False, 0
        if (moment - ts) > max_age_s:
            return False, max(timestamps) - min(timestamps)

    skew = max(timestamps) - min(timestamps)
    return True, skew


@dataclass(frozen=True)
class BestQuoteResult:
    amount_out: int
    gas_estimate: int
    pool: XChainPool


@dataclass
class XChainOpportunity:
    moment: int
    chain_buy: int
    chain_sell: int
    block_buy: int
    block_sell: int
    pool_buy: str
    pool_sell: str
    size_usd: Decimal
    gross_usd: Decimal
    gas_usd: Decimal
    rebalance_usd: Decimal
    net_usd: Decimal
    gap_pct: Decimal
    is_opportunity: bool
    persisted_next_block: bool | None = None
    persisted_1m: bool | None = None
    persisted_5m: bool | None = None


def encode_get_l1_fee(data_len: int = 400) -> str:
    """Encode GasPriceOracle.getL1Fee(bytes) call with dynamic byte payload."""
    offset = hex(32).removeprefix("0x").rjust(64, "0")
    length = hex(data_len).removeprefix("0x").rjust(64, "0")
    pad_len = ((data_len + 31) // 32) * 32
    payload = "00" * pad_len
    return f"{GET_L1_FEE_SELECTOR}{offset}{length}{payload}"


def get_l1_fee(rpc: RpcClient, chain_id: int, block: int | str) -> int:
    """Get L1 data fee in wei via GasPriceOracle on Base (8453) and Optimism (10).

    Arbitrum (42161) incorporates L1 data fee into L2 baseFee; returns 0.
    """
    if chain_id not in (8453, 10):
        return 0
    calldata = encode_get_l1_fee(400)
    res = rpc.call(GAS_PRICE_ORACLE_ADDRESS, calldata, block)
    clean = res.removeprefix("0x").removeprefix("0X")
    if not clean:
        raise ValueError(f"Empty L1 fee response on chain {chain_id} block {block}")
    return int(clean, 16)


def get_base_fee_per_gas(rpc: RpcClient, block: int | str) -> int:
    """Read baseFeePerGas in wei from block header.

    Never falls back to a default value.
    """
    block_tag = hex(block) if isinstance(block, int) else str(block)
    block_obj = rpc._call("eth_getBlockByNumber", [block_tag, False])
    if not isinstance(block_obj, dict) or block_obj.get("baseFeePerGas") is None:
        raise ValueError(f"Block {block} has no baseFeePerGas")
    base_fee_raw = block_obj["baseFeePerGas"]
    base_fee = int(str(base_fee_raw), 0)
    if base_fee <= 0:
        raise ValueError(f"Block {block} has non-positive baseFeePerGas: {base_fee}")
    return base_fee


def decode_slot0_weth_usdc_price(chain_id: int, slot0_hex: str) -> Decimal:
    """Decode slot0 sqrtPriceX96 to WETH price in USDC (USD).

    Accounts for token ordering per chain:
    - Arbitrum (42161) & Base (8453): WETH (18 dec) is token0, USDC (6 dec) is token1.
    - Optimism (10): USDC (6 dec) is token0, WETH (18 dec) is token1.
    """
    clean = slot0_hex.removeprefix("0x").removeprefix("0X")
    if len(clean) < 64:
        raise ValueError(f"Invalid slot0 result length: {len(clean)}")
    sqrt_p = int(clean[0:64], 16)
    if sqrt_p <= 0:
        raise ValueError(f"Invalid sqrtPriceX96: {sqrt_p}")
    ratio = Decimal(sqrt_p) / Decimal(2**96)
    ratio_sq = ratio * ratio

    if chain_id in (42161, 8453):
        return ratio_sq * Decimal(10**12)
    elif chain_id == 10:
        return (Decimal(1) / ratio_sq) * Decimal(10**12)
    else:
        raise ValueError(f"Unknown chain_id for mid price: {chain_id}")


def check_prefilter(
    price_a: Decimal,
    fee_a: int,
    price_b: Decimal,
    fee_b: int,
    rebalance_pct: Decimal = DEFAULT_REBALANCE_PCT,
) -> tuple[bool, Decimal, Decimal]:
    """Check slot0 mid-price gap against fee sum + rebalancing cost.

    Returns (passes, gap, threshold).
    Boundary exact: equal gap = skip (passes=False).
    """
    if price_a <= 0 or price_b <= 0:
        return False, Decimal("0"), Decimal("0")

    if price_a <= price_b:
        p_cheaper, p_dearer = price_a, price_b
    else:
        p_cheaper, p_dearer = price_b, price_a

    gap = (p_dearer - p_cheaper) / p_cheaper
    threshold = (Decimal(fee_a + fee_b) / Decimal("1000000")) + rebalance_pct
    return (gap > threshold), gap, threshold


def _get_quote_target(pool: XChainPool) -> str:
    if pool.adapter_type == "aerodrome_classic":
        return pool.address
    if pool.quoter is not None:
        return pool.quoter
    venue = VENUE_BY_NAME.get((pool.chain_id, pool.venue_name))
    if venue is not None and venue.quoter is not None:
        return venue.quoter
    raise ValueError(f"No quoter found for venue {pool.venue_name} on chain {pool.chain_id}")


def get_best_buy_quote(
    rpc: RpcClient,
    chain_id: int,
    block: int,
    pools: list[XChainPool],
    size_usd: Decimal,
    counters: dict[str, int] | None = None,
) -> BestQuoteResult | None:
    """Find best executable buy quote (USDC -> WETH) across discovered pools.

    Requires 10x shallow check. Reverts (ContractCallError) are skipped and counted.
    """
    if size_usd <= Decimal("0") or not pools:
        return None

    amount_in = int(size_usd * Decimal("1000000"))
    token_in = XCHAIN_TOKENS[chain_id]["USDC"].address
    token_out = XCHAIN_TOKENS[chain_id]["WETH"].address

    candidates: list[BestQuoteResult] = []
    for pool in pools:
        target = _get_quote_target(pool)
        calldata_1x = encode_quote(pool.adapter_type, token_in, token_out, amount_in, pool.pool_key)
        calldata_10x = encode_quote(pool.adapter_type, token_in, token_out, 10 * amount_in, pool.pool_key)

        try:
            res_1x = rpc.call(target, calldata_1x, block)
        except ContractCallError:
            if counters is not None:
                counters["reverts"] = counters.get("reverts", 0) + 1
            continue

        try:
            res_10x = rpc.call(target, calldata_10x, block)
        except ContractCallError:
            if counters is not None:
                counters["reverts"] = counters.get("reverts", 0) + 1
            continue

        try:
            out_1x, gas_1x = decode_quote(pool.adapter_type, res_1x)
            out_10x, _ = decode_quote(pool.adapter_type, res_10x)
        except (ValueError, IndexError):
            if counters is not None:
                counters["unpriced"] = counters.get("unpriced", 0) + 1
            continue

        if out_1x <= 0 or out_10x <= 0:
            continue

        if is_shallow_quote(amount_in, out_1x, out_10x):
            if counters is not None:
                counters["shallow"] = counters.get("shallow", 0) + 1
            continue

        candidates.append(BestQuoteResult(amount_out=out_1x, gas_estimate=gas_1x, pool=pool))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c.amount_out)


def get_best_sell_quote(
    rpc: RpcClient,
    chain_id: int,
    block: int,
    pools: list[XChainPool],
    amount_in_weth: int,
    counters: dict[str, int] | None = None,
) -> BestQuoteResult | None:
    """Find best executable sell quote (WETH -> USDC) across discovered pools.

    Requires 10x shallow check. Reverts (ContractCallError) are skipped and counted.
    """
    if amount_in_weth <= 0 or not pools:
        return None

    token_in = XCHAIN_TOKENS[chain_id]["WETH"].address
    token_out = XCHAIN_TOKENS[chain_id]["USDC"].address

    candidates: list[BestQuoteResult] = []
    for pool in pools:
        target = _get_quote_target(pool)
        calldata_1x = encode_quote(pool.adapter_type, token_in, token_out, amount_in_weth, pool.pool_key)
        calldata_10x = encode_quote(pool.adapter_type, token_in, token_out, 10 * amount_in_weth, pool.pool_key)

        try:
            res_1x = rpc.call(target, calldata_1x, block)
        except ContractCallError:
            if counters is not None:
                counters["reverts"] = counters.get("reverts", 0) + 1
            continue

        try:
            res_10x = rpc.call(target, calldata_10x, block)
        except ContractCallError:
            if counters is not None:
                counters["reverts"] = counters.get("reverts", 0) + 1
            continue

        try:
            out_1x, gas_1x = decode_quote(pool.adapter_type, res_1x)
            out_10x, _ = decode_quote(pool.adapter_type, res_10x)
        except (ValueError, IndexError):
            if counters is not None:
                counters["unpriced"] = counters.get("unpriced", 0) + 1
            continue

        if out_1x <= 0 or out_10x <= 0:
            continue

        if is_shallow_quote(amount_in_weth, out_1x, out_10x):
            if counters is not None:
                counters["shallow"] = counters.get("shallow", 0) + 1
            continue

        candidates.append(BestQuoteResult(amount_out=out_1x, gas_estimate=gas_1x, pool=pool))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c.amount_out)


def evaluate_cross_chain_gap(
    moment: int,
    chain_buy: int,
    chain_sell: int,
    block_buy: int,
    block_sell: int,
    rpc_buy: RpcClient,
    rpc_sell: RpcClient,
    pools_buy: list[XChainPool],
    pools_sell: list[XChainPool],
    size_usd: Decimal,
    mid_price_buy: Decimal,
    mid_price_sell: Decimal,
    rebalance_pct: Decimal = DEFAULT_REBALANCE_PCT,
    rebalance_fixed_usd: Decimal = DEFAULT_REBALANCE_FIXED_USD,
    counters: dict[str, int] | None = None,
) -> XChainOpportunity | None:
    """Evaluate cross-chain arbitrage gap and profitability for an ordered chain pair.

    1. Buy WETH on chain_buy with size_usd USDC.
    2. Sell WETH received on chain_sell for USDC.
    3. Compute gas costs per leg (quoter gasEstimate + 100k at base fee; L1 fee on Base/Optimism).
    4. Compute rebalance cost (rebalance_pct * size_usd + rebalance_fixed_usd).
    """
    buy_quote = get_best_buy_quote(
        rpc=rpc_buy,
        chain_id=chain_buy,
        block=block_buy,
        pools=pools_buy,
        size_usd=size_usd,
        counters=counters,
    )
    if buy_quote is None:
        if counters is not None:
            counters["unpriced"] = counters.get("unpriced", 0) + 1
        return None

    sell_quote = get_best_sell_quote(
        rpc=rpc_sell,
        chain_id=chain_sell,
        block=block_sell,
        pools=pools_sell,
        amount_in_weth=buy_quote.amount_out,
        counters=counters,
    )
    if sell_quote is None:
        if counters is not None:
            counters["unpriced"] = counters.get("unpriced", 0) + 1
        return None

    amount_in_usdc = int(size_usd * Decimal("1000000"))
    gross_profit_raw = sell_quote.amount_out - amount_in_usdc
    gross_usd = Decimal(gross_profit_raw) / Decimal("1000000")
    gap_pct = Decimal(gross_profit_raw) / Decimal(amount_in_usdc)

    # Leg 1 Gas (Buy)
    base_fee_buy = get_base_fee_per_gas(rpc_buy, block_buy)
    l1_fee_buy = get_l1_fee(rpc_buy, chain_buy, block_buy)
    gas_units_buy = buy_quote.gas_estimate + OVERHEAD_GAS
    gas_wei_buy = (gas_units_buy * base_fee_buy) + l1_fee_buy
    gas_eth_buy = Decimal(gas_wei_buy) / Decimal(10**18)
    gas_usd_buy = gas_eth_buy * mid_price_buy

    # Leg 2 Gas (Sell)
    base_fee_sell = get_base_fee_per_gas(rpc_sell, block_sell)
    l1_fee_sell = get_l1_fee(rpc_sell, chain_sell, block_sell)
    gas_units_sell = sell_quote.gas_estimate + OVERHEAD_GAS
    gas_wei_sell = (gas_units_sell * base_fee_sell) + l1_fee_sell
    gas_eth_sell = Decimal(gas_wei_sell) / Decimal(10**18)
    gas_usd_sell = gas_eth_sell * mid_price_sell

    gas_usd = gas_usd_buy + gas_usd_sell
    rebalance_usd = (size_usd * rebalance_pct) + rebalance_fixed_usd
    net_usd = gross_usd - gas_usd - rebalance_usd
    is_opp = net_usd > Decimal("0")

    return XChainOpportunity(
        moment=moment,
        chain_buy=chain_buy,
        chain_sell=chain_sell,
        block_buy=block_buy,
        block_sell=block_sell,
        pool_buy=buy_quote.pool.address,
        pool_sell=sell_quote.pool.address,
        size_usd=size_usd,
        gross_usd=gross_usd,
        gas_usd=gas_usd,
        rebalance_usd=rebalance_usd,
        net_usd=net_usd,
        gap_pct=gap_pct,
        is_opportunity=is_opp,
    )


@dataclass
class XChainPairSizeReport:
    chain_pair: str
    size_usd: Decimal
    total_moments: int
    executable_moments: int
    executable_share: Decimal
    median_net_usd: Decimal
    p90_net_usd: Decimal
    max_net_usd: Decimal
    persistence_dist: dict[str, int]
    monthly_net_usd: Decimal
    monthly_net_eur: Decimal
    capital_usd: Decimal
    capital_eur: Decimal
    return_on_capital_pct: Decimal


@dataclass
class XChainReport:
    from_timestamp: int
    to_timestamp: int
    days: int
    eurusd: Decimal
    threshold_eur: Decimal
    total_sampled_moments: int
    pair_size_reports: dict[tuple[str, Decimal], XChainPairSizeReport]
    overall_verdict: str
    overall_verdict_reason: str
    total_capital_eur: Decimal
    opportunities: list[XChainOpportunity]

    def to_text(self) -> str:
        lines = [
            "================================================================================",
            "mev-scout Phase 2C — Cross-Chain Inventory Arbitrage Census",
            "================================================================================",
            f"Window: {self.days} days | EUR/USD: {self.eurusd} | Threshold: {self.threshold_eur:.0f} EUR/mo",
            f"Sampled moments: {self.total_sampled_moments} (from {self.from_timestamp} to {self.to_timestamp})",
            f"Capital required: {self.total_capital_eur:,.2f} EUR",
            f"Verdict: {self.overall_verdict}"
            + (f" ({self.overall_verdict_reason})" if self.overall_verdict_reason else "")
            + f" | Capital needed: {self.total_capital_eur:,.2f} EUR",
            "",
            "Note: Upper bound: real execution adds latency, partial fills and price risk between legs.",
            "",
            "Results Per Chain Pair and Size:",
            "--------------------------------------------------------------------------------",
        ]
        for (pair, size), rep in sorted(
            self.pair_size_reports.items(), key=lambda item: (item[0][0], item[0][1])
        ):
            lines.append(f"Pair: {pair} | Size: ${size:,.0f}")
            lines.append(
                f"  Executable moments: {rep.executable_moments}/{rep.total_moments} ({rep.executable_share * 100:.1f}%)"
            )
            lines.append(
                f"  Net per trade: median=${rep.median_net_usd:,.2f}, p90=${rep.p90_net_usd:,.2f}, max=${rep.max_net_usd:,.2f}"
            )
            lines.append(
                f"  Persistence distribution: next_block={rep.persistence_dist.get('next_block', 0)}, "
                f"1m={rep.persistence_dist.get('1m', 0)}, 5m={rep.persistence_dist.get('5m', 0)}"
            )
            lines.append(
                f"  Monthly net: ${rep.monthly_net_usd:,.2f} ({rep.monthly_net_eur:,.2f} EUR)"
            )
            lines.append(
                f"  Capital required: ${rep.capital_usd:,.2f} ({rep.capital_eur:,.2f} EUR)"
            )
            lines.append(f"  Return on capital: {rep.return_on_capital_pct:.2f}%")
            lines.append("--------------------------------------------------------------------------------")

        lines.append("================================================================================")
        return "\n".join(lines)

    def to_json(self) -> str:
        d = {
            "from_timestamp": self.from_timestamp,
            "to_timestamp": self.to_timestamp,
            "days": self.days,
            "eurusd": str(self.eurusd),
            "threshold_eur": str(self.threshold_eur),
            "total_sampled_moments": self.total_sampled_moments,
            "overall_verdict": self.overall_verdict,
            "overall_verdict_reason": self.overall_verdict_reason,
            "total_capital_eur": str(self.total_capital_eur),
            "pair_size_reports": {
                f"{k[0]}_{k[1]}": {
                    "chain_pair": v.chain_pair,
                    "size_usd": str(v.size_usd),
                    "total_moments": v.total_moments,
                    "executable_moments": v.executable_moments,
                    "executable_share": str(v.executable_share),
                    "median_net_usd": str(v.median_net_usd),
                    "p90_net_usd": str(v.p90_net_usd),
                    "max_net_usd": str(v.max_net_usd),
                    "persistence_dist": v.persistence_dist,
                    "monthly_net_usd": str(v.monthly_net_usd),
                    "monthly_net_eur": str(v.monthly_net_eur),
                    "capital_usd": str(v.capital_usd),
                    "capital_eur": str(v.capital_eur),
                    "return_on_capital_pct": str(v.return_on_capital_pct),
                }
                for k, v in self.pair_size_reports.items()
            },
        }
        return json.dumps(d, indent=2)

    def to_csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "moment",
            "chain_buy",
            "chain_sell",
            "block_buy",
            "block_sell",
            "pool_buy",
            "pool_sell",
            "size_usd",
            "gross_usd",
            "gas_usd",
            "rebalance_usd",
            "net_usd",
            "net_eur",
            "gap_pct",
            "is_opportunity",
            "persisted_next_block",
            "persisted_1m",
            "persisted_5m",
        ])
        for o in self.opportunities:
            net_eur = (o.net_usd / self.eurusd) if self.eurusd > 0 else Decimal("0")
            writer.writerow([
                o.moment,
                o.chain_buy,
                o.chain_sell,
                o.block_buy,
                o.block_sell,
                o.pool_buy,
                o.pool_sell,
                str(o.size_usd),
                str(o.gross_usd),
                str(o.gas_usd),
                str(o.rebalance_usd),
                str(o.net_usd),
                str(net_eur),
                str(o.gap_pct),
                1 if o.is_opportunity else 0,
                "" if o.persisted_next_block is None else (1 if o.persisted_next_block else 0),
                "" if o.persisted_1m is None else (1 if o.persisted_1m else 0),
                "" if o.persisted_5m is None else (1 if o.persisted_5m else 0),
            ])
        return out.getvalue()


def generate_xchain_report(
    opportunities: list[XChainOpportunity],
    total_moments: int,
    days: int,
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    from_ts: int = 0,
    to_ts: int = 0,
) -> XChainReport:
    """Aggregate opportunities per chain pair and size, compute RoC, and determine verdict."""
    by_pair_size: dict[tuple[str, Decimal], list[XChainOpportunity]] = {}
    for o in opportunities:
        pair_key = f"{o.chain_buy}-{o.chain_sell}"
        key = (pair_key, o.size_usd)
        by_pair_size.setdefault(key, []).append(o)

    pair_reports: dict[tuple[str, Decimal], XChainPairSizeReport] = {}
    total_capital_usd = Decimal("0")
    max_size_per_pair: dict[str, Decimal] = {}

    for (pair_name, size), opps in by_pair_size.items():
        exec_opps = [o for o in opps if o.is_opportunity and o.persisted_next_block is True]
        exec_count = len(exec_opps)
        share = Decimal(exec_count) / Decimal(total_moments) if total_moments > 0 else Decimal("0")

        if exec_opps:
            sorted_nets = sorted(o.net_usd for o in exec_opps)
            n = len(sorted_nets)
            median_net = sorted_nets[n // 2]
            p90_net = sorted_nets[min(n - 1, int(0.90 * n))]
            max_net = sorted_nets[-1]
            sum_net = sum(sorted_nets, Decimal("0"))
        else:
            median_net = Decimal("0")
            p90_net = Decimal("0")
            max_net = Decimal("0")
            sum_net = Decimal("0")

        p_dist = {
            "next_block": sum(1 for o in exec_opps if o.persisted_next_block),
            "1m": sum(1 for o in exec_opps if o.persisted_1m),
            "5m": sum(1 for o in exec_opps if o.persisted_5m),
        }

        monthly_usd = (sum_net / Decimal(days)) * Decimal("30") if days > 0 else Decimal("0")
        monthly_eur = (monthly_usd / eurusd) if eurusd > 0 else Decimal("0")

        # Required inventory = largest size traded held as USDC and WETH on every chain in the pair (4x)
        cap_usd = Decimal("4") * size
        cap_eur = (cap_usd / eurusd) if eurusd > 0 else Decimal("0")
        roc = (monthly_eur / cap_eur * Decimal("100")) if cap_eur > 0 else Decimal("0")

        pair_reports[(pair_name, size)] = XChainPairSizeReport(
            chain_pair=pair_name,
            size_usd=size,
            total_moments=total_moments,
            executable_moments=exec_count,
            executable_share=share,
            median_net_usd=median_net,
            p90_net_usd=p90_net,
            max_net_usd=max_net,
            persistence_dist=p_dist,
            monthly_net_usd=monthly_usd,
            monthly_net_eur=monthly_eur,
            capital_usd=cap_usd,
            capital_eur=cap_eur,
            return_on_capital_pct=roc,
        )

        if size > max_size_per_pair.get(pair_name, Decimal("0")):
            max_size_per_pair[pair_name] = size

    # Total capital required across unique pairs
    for pair_name, max_size in max_size_per_pair.items():
        total_capital_usd += Decimal("4") * max_size

    total_capital_eur = (total_capital_usd / eurusd) if eurusd > 0 else Decimal("0")

    # Overall monthly net EUR across best sizes
    pair_best_monthly_eur: dict[str, Decimal] = {}
    for (pair_name, _), rep in pair_reports.items():
        if rep.monthly_net_eur > pair_best_monthly_eur.get(pair_name, Decimal("0")):
            pair_best_monthly_eur[pair_name] = rep.monthly_net_eur

    overall_monthly_eur = sum(pair_best_monthly_eur.values(), Decimal("0"))

    # Real 30-day months ending at the window end: per month and chain pair, the best
    # size's executable net, summed over pairs. One crash month must not carry the
    # whole window (same rule as the liquidation census).
    anchor = to_ts or max((o.moment for o in opportunities), default=0)
    m_count = max(1, int(days // 30))
    monthly_eur: list[Decimal] = []
    for m_idx in range(m_count):
        hi = anchor - m_idx * 30 * 86400
        lo = hi - 30 * 86400
        per_pair: dict[str, dict[Decimal, Decimal]] = {}
        for o in opportunities:
            if not (o.is_opportunity and o.persisted_next_block is True and lo < o.moment <= hi):
                continue
            pair_key = f"{o.chain_buy}-{o.chain_sell}"
            sizes = per_pair.setdefault(pair_key, {})
            sizes[o.size_usd] = sizes.get(o.size_usd, Decimal("0")) + o.net_usd
        month_usd = sum((max(v.values()) for v in per_pair.values()), Decimal("0"))
        monthly_eur.append(month_usd / eurusd if eurusd > 0 else Decimal("0"))

    verdict, reason = evaluate_verdict(
        monthly_eur,
        top1_share=Decimal("0"),
        threshold_eur=threshold_eur,
    )

    return XChainReport(
        from_timestamp=from_ts,
        to_timestamp=to_ts,
        days=days,
        eurusd=eurusd,
        threshold_eur=threshold_eur,
        total_sampled_moments=total_moments,
        pair_size_reports=pair_reports,
        overall_verdict=verdict,
        overall_verdict_reason=reason,
        total_capital_eur=total_capital_eur,
        opportunities=opportunities,
    )


def parse_dense_ranges(dense_ranges: list[str] | None) -> list[tuple[int, int]]:
    """Parse list of 'FROM:TO' timestamp strings into (from_ts, to_ts) tuples."""
    if not dense_ranges:
        return []
    res = []
    for r in dense_ranges:
        parts = r.split(":")
        if len(parts) == 2:
            try:
                res.append((int(parts[0]), int(parts[1])))
            except ValueError:
                continue
    return res


def run_xchain_sample(
    rpcs: dict[int, RpcClient],
    store: Any,
    days: int = 30,
    every_min: int = 5,
    dense_ranges: list[str] | None = None,
    rebalance_pct: Decimal = DEFAULT_REBALANCE_PCT,
    rebalance_fixed_usd: Decimal = DEFAULT_REBALANCE_FIXED_USD,
    now_ts: int | None = None,
    start_ts: int | None = None,
    progress_stream: Any = None,
) -> None:
    """Run sampling across time grid for cross-chain arbitrage.

    Saves results after every sampled moment and supports resume.
    Prints progress to stderr every 10 moments.
    """
    pools: dict[int, list[XChainPool]] = {}
    for cid in (42161, 8453, 10):
        stored = store.get_xchain_pools(cid)
        if stored:
            pools[cid] = stored
        else:
            discovered = discover_xchain_pools(rpcs[cid], cid)
            store.insert_xchain_pools(discovered)
            pools[cid] = discovered

    end_ts = now_ts if now_ts is not None else int(time.time())
    if start_ts is None:
        start_ts = end_ts - days * 86400
    dense = parse_dense_ranges(dense_ranges)
    grid = generate_time_grid(start_ts, end_ts, step_s=every_min * 60, dense_ranges=dense)

    completed = store.get_completed_xchain_moments()
    chain_pairs = [(42161, 8453), (42161, 10), (8453, 10)]

    for idx, moment in enumerate(grid):
        if moment in completed:
            continue

        chain_blocks: dict[int, tuple[int, int]] = {}
        for cid in (42161, 8453, 10):
            b_num, b_ts = find_block_by_timestamp(
                chain_id=cid,
                target_ts=moment,
                rpc=rpcs[cid],
                store=store,
            )
            chain_blocks[cid] = (b_num, b_ts)

        passes_skew, skew_s = check_moment_skew(moment, chain_blocks, max_age_s=5)
        if not passes_skew:
            store.record_xchain_moment(moment, status="skewed", skew_s=skew_s)
            continue

        moment_opps: list[XChainOpportunity] = []
        for chain_a, chain_b in chain_pairs:
            block_a = chain_blocks[chain_a][0]
            block_b = chain_blocks[chain_b][0]

            try:
                slot0_a = rpcs[chain_a].call(CANONICAL_DEEPEST_POOLS[chain_a], SLOT0_SELECTOR, block_a)
                price_a = decode_slot0_weth_usdc_price(chain_a, slot0_a)
            except ContractCallError:
                continue

            try:
                slot0_b = rpcs[chain_b].call(CANONICAL_DEEPEST_POOLS[chain_b], SLOT0_SELECTOR, block_b)
                price_b = decode_slot0_weth_usdc_price(chain_b, slot0_b)
            except ContractCallError:
                continue

            passes_pref, _, _ = check_prefilter(
                price_a,
                DEEPEST_POOL_FEES[chain_a],
                price_b,
                DEEPEST_POOL_FEES[chain_b],
                rebalance_pct=rebalance_pct,
            )
            if not passes_pref:
                continue

            if price_a <= price_b:
                c_buy, c_sell = chain_a, chain_b
                p_buy, p_sell = price_a, price_b
            else:
                c_buy, c_sell = chain_b, chain_a
                p_buy, p_sell = price_b, price_a

            b_buy = chain_blocks[c_buy][0]
            b_sell = chain_blocks[c_sell][0]

            for size in XCHAIN_SIZES_USD:
                opp = evaluate_cross_chain_gap(
                    moment=moment,
                    chain_buy=c_buy,
                    chain_sell=c_sell,
                    block_buy=b_buy,
                    block_sell=b_sell,
                    rpc_buy=rpcs[c_buy],
                    rpc_sell=rpcs[c_sell],
                    pools_buy=pools[c_buy],
                    pools_sell=pools[c_sell],
                    size_usd=size,
                    mid_price_buy=p_buy,
                    mid_price_sell=p_sell,
                    rebalance_pct=rebalance_pct,
                    rebalance_fixed_usd=rebalance_fixed_usd,
                )
                if opp is not None:
                    if opp.is_opportunity:
                        # 1. next blocks on both chains
                        opp_next = evaluate_cross_chain_gap(
                            moment=moment,
                            chain_buy=c_buy,
                            chain_sell=c_sell,
                            block_buy=b_buy + 1,
                            block_sell=b_sell + 1,
                            rpc_buy=rpcs[c_buy],
                            rpc_sell=rpcs[c_sell],
                            pools_buy=pools[c_buy],
                            pools_sell=pools[c_sell],
                            size_usd=size,
                            mid_price_buy=p_buy,
                            mid_price_sell=p_sell,
                            rebalance_pct=rebalance_pct,
                            rebalance_fixed_usd=rebalance_fixed_usd,
                        )
                        opp.persisted_next_block = (
                            opp_next is not None and opp_next.is_opportunity
                        )

                        # 2. +1 min
                        b_buy_1m, _ = find_block_by_timestamp(c_buy, moment + 60, rpcs[c_buy], store=store)
                        b_sell_1m, _ = find_block_by_timestamp(c_sell, moment + 60, rpcs[c_sell], store=store)
                        opp_1m = evaluate_cross_chain_gap(
                            moment=moment,
                            chain_buy=c_buy,
                            chain_sell=c_sell,
                            block_buy=b_buy_1m,
                            block_sell=b_sell_1m,
                            rpc_buy=rpcs[c_buy],
                            rpc_sell=rpcs[c_sell],
                            pools_buy=pools[c_buy],
                            pools_sell=pools[c_sell],
                            size_usd=size,
                            mid_price_buy=p_buy,
                            mid_price_sell=p_sell,
                            rebalance_pct=rebalance_pct,
                            rebalance_fixed_usd=rebalance_fixed_usd,
                        )
                        opp.persisted_1m = opp_1m is not None and opp_1m.is_opportunity

                        # 3. +5 min
                        b_buy_5m, _ = find_block_by_timestamp(c_buy, moment + 300, rpcs[c_buy], store=store)
                        b_sell_5m, _ = find_block_by_timestamp(c_sell, moment + 300, rpcs[c_sell], store=store)
                        opp_5m = evaluate_cross_chain_gap(
                            moment=moment,
                            chain_buy=c_buy,
                            chain_sell=c_sell,
                            block_buy=b_buy_5m,
                            block_sell=b_sell_5m,
                            rpc_buy=rpcs[c_buy],
                            rpc_sell=rpcs[c_sell],
                            pools_buy=pools[c_buy],
                            pools_sell=pools[c_sell],
                            size_usd=size,
                            mid_price_buy=p_buy,
                            mid_price_sell=p_sell,
                            rebalance_pct=rebalance_pct,
                            rebalance_fixed_usd=rebalance_fixed_usd,
                        )
                        opp.persisted_5m = opp_5m is not None and opp_5m.is_opportunity

                    moment_opps.append(opp)

        if moment_opps:
            store.insert_xchain_opportunities(moment_opps)
        store.record_xchain_moment(moment, status="completed", skew_s=skew_s)

        if progress_stream is not None and (idx + 1) % 10 == 0:
            progress_stream.write(f"Sampled {idx + 1}/{len(grid)} moments\n")
            progress_stream.flush()



