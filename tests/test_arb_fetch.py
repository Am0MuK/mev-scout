"""Tests for pool swap fetching, coverage guard, and arb CLI commands."""

import json
from pathlib import Path
import pytest

from mev_scout.chains import ConfigError
from mev_scout.cli import main
from mev_scout.dex import DEXES, Pool, TokenConfig
from mev_scout.explorer import LogSource
from mev_scout.arb_fetch import fetch_swaps
from mev_scout.rpc import RpcClient
from mev_scout.store import Store
from mev_scout.swaps import DecodedSwap, PANCAKESWAP_SWAP_TOPIC0, UNISWAP_SWAP_TOPIC0

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arb"


class FakeExplorer(LogSource):
    def __init__(self, logs_by_addr=None, block_time_map=None):
        self.logs_by_addr = logs_by_addr or {}
        self.block_time_map = block_time_map or {}
        self.log_calls = []

    def block_by_time(self, chain_id: int, timestamp: int) -> int:
        return self.block_time_map.get(timestamp, 506_700_000)

    def get_logs(
        self,
        chain_id: int,
        address: str,
        topic0: str,
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        self.log_calls.append((chain_id, address.lower(), topic0.lower(), from_block, to_block))
        return self.logs_by_addr.get((address.lower(), topic0.lower()), [])


class FakeRpc:
    def __init__(self, block_num: int = 506_705_000):
        self._block_num = block_num

    def block_number(self) -> int:
        return self._block_num


def test_store_swaps_and_pool_coverage():
    store = Store(":memory:")
    pool_addr = "0xc6962004f452be9203591991d15f6b388e09e8d0"
    swap1 = DecodedSwap(
        chain_id=42161,
        dex="uniswap_v3",
        pool=pool_addr,
        block=506700100,
        timestamp=1727000100,
        tx_hash="0x1111111111111111111111111111111111111111111111111111111111111111",
        log_index=1,
        sender="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        recipient="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        amount0=-1000000000,
        amount1=2000000,
        sqrt_price_x96=4061072399780078164115456,
        liquidity=2656185379376736927,
        tick=-197583,
    )
    store.insert_swaps([swap1])

    # Query swaps
    swaps = store.get_swaps(chain_id=42161)
    assert len(swaps) == 1
    assert swaps[0].tx_hash == swap1.tx_hash
    assert swaps[0].amount0 == -1000000000
    assert swaps[0].amount1 == 2000000

    # Query with block bounds
    assert len(store.get_swaps(chain_id=42161, from_block=506700200)) == 0
    assert len(store.get_swaps(chain_id=42161, to_block=506700100)) == 1

    # Pool coverage ranges
    assert store.last_fetched_pool_block(42161, pool_addr) is None
    gaps = store.pool_covered(42161, pool_addr, 100, 200)
    assert gaps == [(100, 200)]

    store.insert_pool_range(42161, pool_addr, 100, 150)
    assert store.last_fetched_pool_block(42161, pool_addr) == 150
    gaps = store.pool_covered(42161, pool_addr, 100, 200)
    assert gaps == [(151, 200)]

    store.insert_pool_range(42161, pool_addr, 151, 200)
    gaps = store.pool_covered(42161, pool_addr, 100, 200)
    assert gaps == []


def test_fetch_swaps_per_pool_uses_correct_topic0():
    """Verify that fetching swaps uses PancakeSwap topic0 for PancakeSwap and Uniswap topic0 for Uniswap."""
    store = Store(":memory:")
    uni_pool = Pool(
        chain_id=42161,
        dex="uniswap_v3",
        address="0xc6962004f452be9203591991d15f6b388e09e8d0",
        token0="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        token1="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        fee=500,
    )
    pancake_pool = Pool(
        chain_id=42161,
        dex="pancakeswap_v3",
        address="0xd9e2a1a61b6e61b275cec326465d417e52c1b95c",
        token0="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        token1="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        fee=500,
    )
    store.insert_pools([uni_pool, pancake_pool])

    with open(FIXTURES_DIR / "uniswap_v3_weth_usdc_500_swaps.json") as f:
        uni_rows = json.load(f)["result"]
    with open(FIXTURES_DIR / "pancake_v3_weth_usdc_500_swaps.json") as f:
        pancake_rows = json.load(f)["result"]

    explorer = FakeExplorer(
        logs_by_addr={
            (uni_pool.address.lower(), UNISWAP_SWAP_TOPIC0.lower()): uni_rows,
            (pancake_pool.address.lower(), PANCAKESWAP_SWAP_TOPIC0.lower()): pancake_rows,
        }
    )
    rpc = FakeRpc(block_num=506_705_000)

    fetch_swaps(
        chain_id=42161,
        days=90,
        explorer=explorer,
        rpc=rpc,
        store=store,
        now=1727000000,
    )

    # Verify explorer was called with correct topic for each pool
    calls = explorer.log_calls
    assert len(calls) == 2
    uni_call = [c for c in calls if c[1] == uni_pool.address.lower()][0]
    pancake_call = [c for c in calls if c[1] == pancake_pool.address.lower()][0]

    assert uni_call[2] == UNISWAP_SWAP_TOPIC0.lower()
    assert pancake_call[2] == PANCAKESWAP_SWAP_TOPIC0.lower()

    # Verify swaps were saved
    uni_swaps = store.get_swaps(chain_id=42161, pool=uni_pool.address)
    pancake_swaps = store.get_swaps(chain_id=42161, pool=pancake_pool.address)
    assert len(uni_swaps) == len(uni_rows)
    assert len(pancake_swaps) == len(pancake_rows)

    # Verify coverage ranges stored
    assert store.pool_covered(42161, uni_pool.address, 506_700_000, 506_705_000) == []
    assert store.pool_covered(42161, pancake_pool.address, 506_700_000, 506_705_000) == []


def test_cli_arb_pools_and_arb_fetch_unsupported_chain(monkeypatch):
    """Unsupported chains should exit with code 2."""
    with pytest.raises(SystemExit) as exc:
        main(["arb-pools", "--chain", "999999"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        main(["arb-fetch", "--chain", "999999"])
    assert exc.value.code == 2
