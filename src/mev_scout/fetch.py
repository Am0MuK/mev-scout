"""Fetch liquidation events for a chain across a time window."""

import time
from mev_scout.chains import CHAINS, ConfigError
from mev_scout.decode import LIQUIDATION_TOPIC0, decode_log
from mev_scout.explorer import EtherscanClient
from mev_scout.rpc import RpcClient
from mev_scout.store import Store

MAX_CHUNK_BLOCKS = 500_000
GET_RESERVES_LIST_SELECTOR = "0xd1946dbc"


def decode_address_array(data_hex: str) -> list[str]:
    clean = data_hex.removeprefix("0x").removeprefix("0X")
    if len(clean) < 128:
        raise ValueError(f"Invalid ABI encoding for address array: too short ({len(clean)} chars)")

    offset = int(clean[0:64], 16)
    offset_hex = offset * 2
    if len(clean) < offset_hex + 64:
        raise ValueError("Invalid ABI encoding: offset points beyond payload")

    length = int(clean[offset_hex : offset_hex + 64], 16)
    expected_len = offset_hex + 64 + (length * 64)
    if len(clean) < expected_len:
        raise ValueError(f"Invalid ABI encoding: expected {expected_len} hex chars, got {len(clean)}")

    addresses = []
    start = offset_hex + 64
    for i in range(length):
        word = clean[start + i * 64 : start + (i + 1) * 64]
        addr = "0x" + word[-40:].lower()
        addresses.append(addr)
    return addresses


def fetch(
    chain_id: int,
    days: int,
    explorer: EtherscanClient,
    rpc: RpcClient,
    store: Store,
    now: int | None = None,
) -> None:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain id: {chain_id}")

    chain = CHAINS[chain_id]

    # Pre-flight check: wrapped-native must be in Pool.getReservesList()
    raw_reserves = rpc.call(to=chain.pool, data=GET_RESERVES_LIST_SELECTOR, block="latest")
    try:
        reserves = decode_address_array(raw_reserves)
    except Exception as exc:
        raise ConfigError(f"Failed to decode Pool.getReservesList() on chain {chain_id}: {exc}") from exc

    reserves_lower = {a.lower() for a in reserves}
    if chain.wrapped_native.lower() not in reserves_lower:
        raise ConfigError(
            f"Wrapped native {chain.wrapped_native} ({chain.native_symbol}) not found in "
            f"Pool.getReservesList() for chain {chain_id}"
        )

    # Resolve window
    now_ts = int(time.time()) if now is None else now
    start_ts = now_ts - (days * 86400)
    from_block = explorer.block_by_time(chain_id, start_ts)
    to_block = rpc.block_number()

    if from_block > to_block:
        from_block = to_block

    gaps = store.covered(chain_id, from_block, to_block)
    for gap_start, gap_end in gaps:
        cur_from = gap_start
        while cur_from <= gap_end:
            cur_to = min(gap_end, cur_from + MAX_CHUNK_BLOCKS - 1)
            raw_logs = explorer.get_logs(
                chain_id=chain_id,
                address=chain.pool,
                topic0=LIQUIDATION_TOPIC0,
                from_block=cur_from,
                to_block=cur_to,
            )
            events = [decode_log(chain_id, r) for r in raw_logs]
            store.insert_liquidations(events)
            store.insert_range(chain_id, cur_from, cur_to)
            cur_from = cur_to + 1
