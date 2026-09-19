"""Tests for DEX configurations, pool discovery and pool storage."""

import pytest

from mev_scout.dex import (
    ARBITRUM_TOKENS,
    ARBITRUM_PAIRS,
    DEXES,
    DexConfig,
    GET_POOL_SELECTOR,
    FEE_SELECTOR,
    TOKEN0_SELECTOR,
    discover_pools,
    encode_get_pool,
    Pool,
)
from mev_scout.rpc import ContractCallError
from mev_scout.store import Store


class FakeRpcClient:
    def __init__(self, responses=None):
        # Key: (to.lower(), data.lower(), str(block).lower()) -> result hex
        self.responses = responses or {}
        self.calls = []

    def call(self, to: str, data: str, block: str | int = "latest") -> str:
        key = (to.lower(), data.lower(), str(block).lower())
        self.calls.append((to, data, block))
        if key in self.responses:
            val = self.responses[key]
            if isinstance(val, Exception):
                raise val
            return val
        # Default fallback for getPool: zero address
        if data.lower().startswith(GET_POOL_SELECTOR.lower()):
            return "0x" + "0" * 64
        raise ContractCallError(f"No fake response for {to} {data} {block}")

    def batch_call(self, calls: list[tuple[str, str, str | int]]) -> list[str]:
        results = []
        for to, data, block in calls:
            try:
                results.append(self.call(to, data, block))
            except Exception as exc:
                results.append(exc)
        return results


def test_dex_table_spec():
    """Verify DEX table matches SPEC-phase2-arbitrage exactly."""
    assert "uniswap_v3" in DEXES
    assert "sushiswap_v3" in DEXES
    assert "pancakeswap_v3" in DEXES

    uni = DEXES["uniswap_v3"]
    assert uni.factory.lower() == "0x1f98431c8ad98523631ae4a59f267346ea31f984"
    assert uni.quoter.lower() == "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
    assert uni.fee_tiers == (100, 500, 3000, 10000)
    assert uni.swap_topic0.lower() == "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

    sushi = DEXES["sushiswap_v3"]
    assert sushi.factory.lower() == "0x1af415a1eba07a4986a52b6f2e7de7003d82231e"
    assert sushi.quoter.lower() == "0x0524e833ccd057e4d7a296e3aaab9f7675964ce1"
    assert sushi.fee_tiers == (100, 500, 3000, 10000)
    assert sushi.swap_topic0.lower() == uni.swap_topic0.lower()

    pancake = DEXES["pancakeswap_v3"]
    assert pancake.factory.lower() == "0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865"
    assert pancake.quoter.lower() == "0xb048bbc1ee6b733fffcfb9e9cef7375518e25997"
    # PancakeSwap has 2500 tier instead of 3000
    assert pancake.fee_tiers == (100, 500, 2500, 10000)
    assert 2500 in pancake.fee_tiers
    assert 3000 not in pancake.fee_tiers
    assert pancake.swap_topic0.lower() == "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"
    assert pancake.swap_topic0.lower() != uni.swap_topic0.lower()


def test_token_table_and_pairs():
    """Verify tokens and pairs on Arbitrum."""
    expected_tokens = {
        "WETH": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        "USDC": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        "USDT": "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        "WBTC": "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
        "ARB": "0x912ce59144191c1204e64559fe8253a0e49e6548",
    }
    for sym, addr in expected_tokens.items():
        assert sym in ARBITRUM_TOKENS
        assert ARBITRUM_TOKENS[sym].address.lower() == addr.lower()

    expected_pairs = {
        ("WETH", "USDC"),
        ("WETH", "USDT"),
        ("WBTC", "WETH"),
        ("ARB", "WETH"),
        ("ARB", "USDC"),
    }
    assert set(ARBITRUM_PAIRS) == expected_pairs


def test_encode_get_pool():
    weth = ARBITRUM_TOKENS["WETH"].address
    usdc = ARBITRUM_TOKENS["USDC"].address
    fee = 500
    calldata = encode_get_pool(weth, usdc, fee)
    assert calldata.startswith("0x1698ee82")
    assert len(calldata) == 10 + 64 * 3
    # Check arguments
    token0_arg = calldata[10 : 10 + 64]
    token1_arg = calldata[10 + 64 : 10 + 128]
    fee_arg = calldata[10 + 128 : 10 + 192]
    assert int(token0_arg, 16) == int(weth, 16)
    assert int(token1_arg, 16) == int(usdc, 16)
    assert int(fee_arg, 16) == 500


def test_discover_pools_zero_address_and_valid():
    """Zero address means no pool; valid pool is verified for token0 and fee."""
    weth = ARBITRUM_TOKENS["WETH"].address
    usdc = ARBITRUM_TOKENS["USDC"].address
    token0 = min(weth.lower(), usdc.lower())

    uni_factory = DEXES["uniswap_v3"].factory
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"

    # Mock RPC responses
    get_pool_weth_usdc_500 = encode_get_pool(weth, usdc, 500)
    responses = {
        (uni_factory.lower(), get_pool_weth_usdc_500.lower(), "latest"): "0x" + "0" * 24 + pool_addr[2:],
        (pool_addr.lower(), TOKEN0_SELECTOR.lower(), "latest"): "0x" + "0" * 24 + token0[2:],
        (pool_addr.lower(), FEE_SELECTOR.lower(), "latest"): "0x" + hex(500)[2:].rjust(64, "0"),
    }
    rpc = FakeRpcClient(responses)
    pools = discover_pools(chain_id=42161, rpc=rpc)

    # Should find exactly the uni pool
    found = [p for p in pools if p.dex == "uniswap_v3" and p.fee == 500 and (p.token0 == token0 or p.token1 == token0)]
    assert len(found) == 1
    p = found[0]
    assert p.address.lower() == pool_addr.lower()
    assert p.fee == 500
    assert p.token0.lower() == token0


def test_discover_pools_pancake_tier_2500():
    """PancakeSwap 2500 tier is probed and discovered."""
    weth = ARBITRUM_TOKENS["WETH"].address
    usdc = ARBITRUM_TOKENS["USDC"].address
    token0 = min(weth.lower(), usdc.lower())

    cake_factory = DEXES["pancakeswap_v3"].factory
    pool_addr = "0x1234567890123456789012345678901234567890"

    get_pool_call = encode_get_pool(weth, usdc, 2500)
    responses = {
        (cake_factory.lower(), get_pool_call.lower(), "latest"): "0x" + "0" * 24 + pool_addr[2:],
        (pool_addr.lower(), TOKEN0_SELECTOR.lower(), "latest"): "0x" + "0" * 24 + token0[2:],
        (pool_addr.lower(), FEE_SELECTOR.lower(), "latest"): "0x" + hex(2500)[2:].rjust(64, "0"),
    }
    rpc = FakeRpcClient(responses)
    pools = discover_pools(chain_id=42161, rpc=rpc)

    pancake_2500 = [p for p in pools if p.dex == "pancakeswap_v3" and p.fee == 2500]
    assert len(pancake_2500) == 1
    assert pancake_2500[0].address.lower() == pool_addr.lower()


def test_discover_pools_verification_failure_raises():
    """If token0() or fee() does not match, discover_pools fails loudly."""
    weth = ARBITRUM_TOKENS["WETH"].address
    usdc = ARBITRUM_TOKENS["USDC"].address
    uni_factory = DEXES["uniswap_v3"].factory
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"

    get_pool_call = encode_get_pool(weth, usdc, 500)
    # Return wrong token0
    responses = {
        (uni_factory.lower(), get_pool_call.lower(), "latest"): "0x" + "0" * 24 + pool_addr[2:],
        (pool_addr.lower(), TOKEN0_SELECTOR.lower(), "latest"): "0x" + "0" * 24 + "11" * 20,
        (pool_addr.lower(), FEE_SELECTOR.lower(), "latest"): "0x" + hex(500)[2:].rjust(64, "0"),
    }
    rpc = FakeRpcClient(responses)
    with pytest.raises(ValueError, match="token0 mismatch"):
        discover_pools(chain_id=42161, rpc=rpc)


def test_store_pools_persistence():
    store = Store(":memory:")
    pool1 = Pool(
        chain_id=42161,
        dex="uniswap_v3",
        address="0xc6962004f452be9203591991d15f6b388e09e8d0",
        token0="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        token1="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        fee=500,
    )
    pool2 = Pool(
        chain_id=42161,
        dex="pancakeswap_v3",
        address="0xd9e2a1a61b6e61b275cec326465d417e52c1b95c",
        token0="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        token1="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        fee=500,
    )
    store.insert_pools([pool1, pool2])

    pools = store.get_pools(chain_id=42161)
    assert len(pools) == 2
    by_addr = {p.address.lower(): p for p in pools}
    assert pool1.address.lower() in by_addr
    assert by_addr[pool1.address.lower()].fee == 500
    assert by_addr[pool1.address.lower()].dex == "uniswap_v3"

    uni_pools = store.get_pools(chain_id=42161, dex="uniswap_v3")
    assert len(uni_pools) == 1
    assert uni_pools[0].dex == "uniswap_v3"

    p = store.get_pool(chain_id=42161, address=pool1.address)
    assert p is not None
    assert p.dex == "uniswap_v3"

    missing = store.get_pool(chain_id=42161, address="0x0000000000000000000000000000000000000001")
    assert missing is None
