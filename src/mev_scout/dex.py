"""DEX and token definitions, ABI helpers, and pool discovery for Arbitrum."""

from dataclasses import dataclass
from typing import Any

from mev_scout.rpc import ContractCallError, RpcClient

GET_POOL_SELECTOR = "0x1698ee82"
FACTORY_SELECTOR = "0xc45a0155"
QUOTE_EXACT_INPUT_SINGLE_SELECTOR = "0xc6a5026a"
SLOT0_SELECTOR = "0x3850c7bd"
LIQUIDITY_SELECTOR = "0x1a686502"
TOKEN0_SELECTOR = "0x0dfe1681"
FEE_SELECTOR = "0xddca3f43"


@dataclass(frozen=True)
class DexConfig:
    name: str
    display_name: str
    factory: str
    quoter: str
    fee_tiers: tuple[int, ...]
    swap_topic0: str


@dataclass(frozen=True)
class TokenConfig:
    symbol: str
    address: str
    decimals: int


@dataclass(frozen=True)
class Pool:
    chain_id: int
    dex: str
    address: str
    token0: str
    token1: str
    fee: int


DEXES: dict[str, DexConfig] = {
    "uniswap_v3": DexConfig(
        name="uniswap_v3",
        display_name="Uniswap V3",
        factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
        quoter="0x61ffe014ba17989e743c5f6cb21bf9697530b21e",
        fee_tiers=(100, 500, 3000, 10000),
        swap_topic0="0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",
    ),
    "sushiswap_v3": DexConfig(
        name="sushiswap_v3",
        display_name="SushiSwap V3",
        factory="0x1af415a1eba07a4986a52b6f2e7de7003d82231e",
        quoter="0x0524e833ccd057e4d7a296e3aaab9f7675964ce1",
        fee_tiers=(100, 500, 3000, 10000),
        swap_topic0="0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67",
    ),
    "pancakeswap_v3": DexConfig(
        name="pancakeswap_v3",
        display_name="PancakeSwap V3",
        factory="0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865",
        quoter="0xb048bbc1ee6b733fffcfb9e9cef7375518e25997",
        fee_tiers=(100, 500, 2500, 10000),
        swap_topic0="0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83",
    ),
}

ARBITRUM_TOKENS: dict[str, TokenConfig] = {
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
    "USDT": TokenConfig(
        symbol="USDT",
        address="0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        decimals=6,
    ),
    "WBTC": TokenConfig(
        symbol="WBTC",
        address="0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
        decimals=8,
    ),
    "ARB": TokenConfig(
        symbol="ARB",
        address="0x912ce59144191c1204e64559fe8253a0e49e6548",
        decimals=18,
    ),
}

ARBITRUM_PAIRS: tuple[tuple[str, str], ...] = (
    ("WETH", "USDC"),
    ("WETH", "USDT"),
    ("WBTC", "WETH"),
    ("ARB", "WETH"),
    ("ARB", "USDC"),
)


def encode_get_pool(token_a: str, token_b: str, fee: int) -> str:
    """Encode factory.getPool(address,address,uint24)."""
    clean_a = token_a.removeprefix("0x").removeprefix("0X").lower().rjust(64, "0")
    clean_b = token_b.removeprefix("0x").removeprefix("0X").lower().rjust(64, "0")
    clean_fee = hex(fee).removeprefix("0x").removeprefix("0X").rjust(64, "0")
    return f"{GET_POOL_SELECTOR}{clean_a}{clean_b}{clean_fee}"


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


def discover_pools(chain_id: int, rpc: RpcClient) -> list[Pool]:
    """Discover pools via factory.getPool for every DEX, pair, and fee tier.

    Zero address means no pool. Verifies token0() and fee() on each discovered pool.
    """
    if chain_id != 42161:
        raise ValueError(f"Pool discovery currently supported for Arbitrum (42161), got {chain_id}")

    candidates: list[tuple[str, str, str, str, int]] = []
    get_pool_calls: list[tuple[str, str, str | int]] = []

    for dex_key, dex in DEXES.items():
        for sym_a, sym_b in ARBITRUM_PAIRS:
            tok_a = ARBITRUM_TOKENS[sym_a].address.lower()
            tok_b = ARBITRUM_TOKENS[sym_b].address.lower()
            for fee in dex.fee_tiers:
                calldata = encode_get_pool(tok_a, tok_b, fee)
                candidates.append((dex_key, tok_a, tok_b, dex.factory, fee))
                get_pool_calls.append((dex.factory, calldata, "latest"))

    get_pool_results = _execute_batch_or_single(rpc, get_pool_calls)

    discovered_candidates: list[tuple[str, str, str, str, int]] = []
    verification_calls: list[tuple[str, str, str | int]] = []

    for (dex_key, tok_a, tok_b, factory, fee), res in zip(candidates, get_pool_results):
        # A revert means "no such pool"; any other error (outage, rate limit) must not
        # silently remove a venue from the analysis.
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

        discovered_candidates.append((dex_key, tok_a, tok_b, addr, fee))
        verification_calls.append((addr, TOKEN0_SELECTOR, "latest"))
        verification_calls.append((addr, FEE_SELECTOR, "latest"))

    if not discovered_candidates:
        return []

    verify_results = _execute_batch_or_single(rpc, verification_calls)

    pools: list[Pool] = []
    for idx, (dex_key, tok_a, tok_b, pool_addr, fee) in enumerate(discovered_candidates):
        tok0_res = verify_results[idx * 2]
        fee_res = verify_results[idx * 2 + 1]

        if isinstance(tok0_res, Exception):
            raise ValueError(f"Failed to query token0 for pool {pool_addr}: {tok0_res}") from tok0_res
        if isinstance(fee_res, Exception):
            raise ValueError(f"Failed to query fee for pool {pool_addr}: {fee_res}") from fee_res

        clean_tok0 = tok0_res.removeprefix("0x").removeprefix("0X")
        clean_fee = fee_res.removeprefix("0x").removeprefix("0X")

        actual_token0 = "0x" + clean_tok0[-40:].lower()
        actual_fee = int(clean_fee, 16)

        expected_token0 = min(tok_a, tok_b)
        expected_token1 = max(tok_a, tok_b)

        if actual_token0 != expected_token0:
            raise ValueError(
                f"Pool verification failed for {pool_addr}: "
                f"token0 mismatch (expected {expected_token0}, got {actual_token0})"
            )
        if actual_fee != fee:
            raise ValueError(
                f"Pool verification failed for {pool_addr}: "
                f"fee mismatch (expected {fee}, got {actual_fee})"
            )

        pools.append(
            Pool(
                chain_id=chain_id,
                dex=dex_key,
                address=pool_addr,
                token0=expected_token0,
                token1=expected_token1,
                fee=fee,
            )
        )

    return pools
