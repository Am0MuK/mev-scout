"""Fetch swap events per pool across a time window."""

import time
from typing import Sequence

from mev_scout.chains import ConfigError
from mev_scout.dex import DEXES, Pool, discover_pools
from mev_scout.explorer import LogSource
from mev_scout.rpc import RpcClient
from mev_scout.store import Store
from mev_scout.swaps import decode_swap_log

MAX_CHUNK_BLOCKS = 500_000


def fetch_swaps(
    chain_id: int,
    days: int,
    explorer: LogSource,
    rpc: RpcClient,
    store: Store,
    pools: Sequence[Pool] | None = None,
    now: int | None = None,
) -> None:
    """Fetch swap logs for discovered pools across the specified time window."""
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage census currently only supports Arbitrum (42161), got {chain_id}")

    if pools is None:
        pools = store.get_pools(chain_id)
        if not pools:
            pools = discover_pools(chain_id, rpc)
            store.insert_pools(pools)

    if not pools:
        return

    now_ts = int(time.time()) if now is None else now
    start_ts = now_ts - (days * 86400)
    from_block = explorer.block_by_time(chain_id, start_ts)
    to_block = rpc.block_number()

    if from_block > to_block:
        from_block = to_block

    for pool in pools:
        dex_config = DEXES.get(pool.dex)
        if not dex_config:
            raise ConfigError(f"Unknown DEX: {pool.dex}")
        topic0 = dex_config.swap_topic0

        gaps = store.pool_covered(chain_id, pool.address, from_block, to_block)
        for gap_start, gap_end in gaps:
            cur_from = gap_start
            while cur_from <= gap_end:
                cur_to = min(gap_end, cur_from + MAX_CHUNK_BLOCKS - 1)
                raw_logs = explorer.get_logs(
                    chain_id=chain_id,
                    address=pool.address,
                    topic0=topic0,
                    from_block=cur_from,
                    to_block=cur_to,
                )
                swaps = [decode_swap_log(chain_id, r, dex=pool.dex) for r in raw_logs]
                store.insert_swaps(swaps)
                store.insert_pool_range(chain_id, pool.address, cur_from, cur_to)
                cur_from = cur_to + 1
