from unittest.mock import MagicMock
import pytest

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.decode import LIQUIDATION_TOPIC0
from mev_scout.fetch import fetch
from mev_scout.store import Store


def _encode_address_array(addrs: list[str]) -> str:
    # ABI encode address[]: offset (0x20), length, elements
    res = "0x" + "0" * 62 + "20"
    res += hex(len(addrs))[2:].zfill(64)
    for a in addrs:
        res += a.lower().removeprefix("0x").zfill(64)
    return res


def test_fetch_unsupported_chain_raises_config_error():
    explorer = MagicMock()
    rpc = MagicMock()
    store = Store(":memory:")
    with pytest.raises(ConfigError, match="Unsupported chain"):
        fetch(999999, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)


def test_fetch_wrapped_native_not_in_reserves_raises_config_error():
    chain = CHAINS[146]
    other_token = "0x1111111111111111111111111111111111111111"

    rpc = MagicMock()
    rpc.call.return_value = _encode_address_array([other_token])

    explorer = MagicMock()
    store = Store(":memory:")

    with pytest.raises(ConfigError, match="Wrapped native"):
        fetch(146, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)


def test_fetch_normal_window_stores_liquidations_and_range():
    chain = CHAINS[146]
    rpc = MagicMock()
    rpc.call.return_value = _encode_address_array([chain.wrapped_native])
    rpc.block_number.return_value = 50000100

    explorer = MagicMock()
    explorer.block_by_time.return_value = 50000000

    raw_log = {
        "address": chain.pool,
        "topics": [
            LIQUIDATION_TOPIC0,
            "0x" + "0" * 24 + chain.wrapped_native[2:],
            "0x" + "0" * 24 + "29219dd400f2bf60e5a23d13be72b486d4038894",
            "0x" + "0" * 24 + "b3bfb32977cfd6200ab9537e3703e501d8381c9b",
        ],
        "data": "0x" + "0" * 64 + "0" * 64 + "0" * 24 + "95654779c314e3390786b98ebcd83f7cbef664ec" + "0" * 64,
        "blockNumber": hex(50000050),
        "timeStamp": hex(1700000000),
        "gasPrice": "0x10",
        "gasUsed": "0x20",
        "logIndex": "0x1",
        "transactionHash": "0xabc",
    }
    explorer.get_logs.return_value = [raw_log]

    store = Store(":memory:")
    fetch(146, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)

    # Liquidations stored
    liqs = store.get_liquidations(146)
    assert len(liqs) == 1
    assert liqs[0].tx_hash == "0xabc"

    # Gaps now empty
    assert store.covered(146, 50000000, 50000100) == []


def test_fetch_chunks_large_block_ranges():
    chain = CHAINS[42161]
    rpc = MagicMock()
    rpc.call.return_value = _encode_address_array([chain.wrapped_native])
    rpc.block_number.return_value = 1_200_000

    explorer = MagicMock()
    explorer.block_by_time.return_value = 100_000
    explorer.get_logs.return_value = []

    store = Store(":memory:")
    fetch(42161, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)

    # 100,000 to 1,200,000 is 1,100,001 blocks -> 3 chunks of at most 500,000:
    # [100,000 - 599,999], [600,000 - 1,099,999], [1,100,000 - 1,200,000]
    assert explorer.get_logs.call_count == 3
    calls = explorer.get_logs.call_args_list
    assert calls[0].kwargs["from_block"] == 100_000
    assert calls[0].kwargs["to_block"] == 599_999
    assert calls[1].kwargs["from_block"] == 600_000
    assert calls[1].kwargs["to_block"] == 1_099_999
    assert calls[2].kwargs["from_block"] == 1_100_000
    assert calls[2].kwargs["to_block"] == 1_200_000


def test_fetch_skips_already_covered_ranges():
    chain = CHAINS[146]
    rpc = MagicMock()
    rpc.call.return_value = _encode_address_array([chain.wrapped_native])
    rpc.block_number.return_value = 50000100

    explorer = MagicMock()
    explorer.block_by_time.return_value = 50000000

    store = Store(":memory:")
    # Mark whole range already covered
    store.insert_range(146, 50000000, 50000100)

    fetch(146, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)
    assert explorer.get_logs.call_count == 0


def test_fetch_chunk_failure_does_not_record_range():
    chain = CHAINS[146]
    rpc = MagicMock()
    rpc.call.return_value = _encode_address_array([chain.wrapped_native])
    rpc.block_number.return_value = 50000100

    explorer = MagicMock()
    explorer.block_by_time.return_value = 50000000
    explorer.get_logs.side_effect = RuntimeError("Network error")

    store = Store(":memory:")
    with pytest.raises(RuntimeError, match="Network error"):
        fetch(146, days=90, explorer=explorer, rpc=rpc, store=store, now=1700000000)

    assert store.covered(146, 50000000, 50000100) == [(50000000, 50000100)]
