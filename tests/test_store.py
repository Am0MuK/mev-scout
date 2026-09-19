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
