import pytest

from mev_scout.decode import Liquidation
from mev_scout.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_empty_store_has_full_gap(store):
    gaps = store.covered(chain_id=42161, from_block=100, to_block=200)
    assert gaps == [(100, 200)]


def test_adjacent_ranges_merge_cleanly(store):
    store.insert_range(chain_id=42161, from_block=100, to_block=150)
    store.insert_range(chain_id=42161, from_block=151, to_block=200)

    gaps = store.covered(chain_id=42161, from_block=100, to_block=200)
    assert gaps == []


def test_overlapping_ranges_merge_cleanly(store):
    store.insert_range(chain_id=42161, from_block=100, to_block=160)
    store.insert_range(chain_id=42161, from_block=140, to_block=200)

    gaps = store.covered(chain_id=42161, from_block=100, to_block=200)
    assert gaps == []


def test_one_block_gap_is_reported(store):
    store.insert_range(chain_id=42161, from_block=100, to_block=150)
    store.insert_range(chain_id=42161, from_block=152, to_block=200)

    gaps = store.covered(chain_id=42161, from_block=100, to_block=200)
    assert gaps == [(151, 151)]


def test_prefix_and_suffix_gaps(store):
    store.insert_range(chain_id=42161, from_block=100, to_block=150)

    gaps = store.covered(chain_id=42161, from_block=50, to_block=200)
    assert gaps == [(50, 99), (151, 200)]


def test_liquidations_round_trip(store):
    item = Liquidation(
        chain_id=146,
        block=50060028,
        timestamp=1760131250,
        tx_hash="0x8340c866d4312edb70e4dff0da711b7dbd9b0df08924e4742f706d474c579143",
        log_index=119,
        collateral="0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38",
        debt="0x29219dd400f2bf60e5a23d13be72b486d4038894",
        user="0xb3bfb32977cfd6200ab9537e3703e501d8381c9b",
        debt_to_cover=1711159398,
        collateral_amount=21529359566765859279297,  # > 64 bit integer
        liquidator="0x95654779c314e3390786b98ebcd83f7cbef664ec",
        receive_atoken=False,
        gas_used=640361,
        gas_price=2668742532097,
    )
    store.insert_liquidations([item])

    res = store.get_liquidations(146)
    assert len(res) == 1
    assert res[0] == item


def test_call_cache_round_trip(store):
    assert store.get_call_cache(146, "0xto", "0xdata", 100) is None
    store.set_call_cache(146, "0xto", "0xdata", 100, "0xresult")
    assert store.get_call_cache(146, "0xto", "0xdata", 100) == "0xresult"


def test_decimals_cache_round_trip(store):
    token = "0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38"
    assert store.get_decimals(146, token) is None
    store.set_decimals(146, token.upper(), 18)
    assert store.get_decimals(146, token.lower()) == 18


def _sw(pool, block, tx, li=0, ts=1000):
    from mev_scout.swaps import DecodedSwap
    return DecodedSwap(42161, "uniswap_v3", pool, block, ts, tx, li, "0xs", "0xr", 1, -1, 2**96, 1, 0)


def test_multi_pool_swaps_only_returns_transactions_touching_two_pools():
    # 9.5M real swaps do not fit in memory; only multi-pool transactions can be arbitrage.
    st = Store(":memory:")
    st.insert_swaps([_sw("0xa", 1, "0x1"), _sw("0xb", 1, "0x1", 1), _sw("0xa", 2, "0x2"), _sw("0xa", 3, "0x3"), _sw("0xa", 3, "0x3", 1)])
    got = st.get_multi_pool_swaps(42161)
    assert sorted({s.tx_hash for s in got}) == ["0x1"]
    assert len(got) == 2


def test_last_swap_at_or_before_block():
    st = Store(":memory:")
    st.insert_swaps([_sw("0xa", 5, "0x1"), _sw("0xa", 9, "0x2"), _sw("0xa", 9, "0x3", 7), _sw("0xa", 12, "0x4")])
    last = st.get_last_swap(42161, "0xa", 10)
    assert (last.block, last.log_index) == (9, 7)
    assert st.get_last_swap(42161, "0xa", 4) is None
