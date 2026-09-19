"""Phase 2C — Cross-chain price-gap census (inventory arbitrage).

Multi-chain venue definitions, adapters, pool discovery, and cross-chain quoting.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mev_scout.dex import TokenConfig
from mev_scout.rpc import ContractCallError, RpcClient

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
    quoter: str | None


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
