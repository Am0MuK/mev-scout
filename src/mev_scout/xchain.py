"""Phase 2C — Cross-chain price-gap census (inventory arbitrage).

Multi-chain venue definitions, adapters, pool discovery, and cross-chain quoting.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mev_scout.dex import TokenConfig

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
