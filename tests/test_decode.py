import json
from pathlib import Path
import pytest

from mev_scout.decode import LIQUIDATION_TOPIC0, Liquidation, decode_log


def test_decode_sonic_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "sonic_liquidation_block_50060028.json"
    with open(fixture_path) as f:
        data = json.load(f)

    rows = data["result"]
    assert len(rows) == 11

    events = [decode_log(146, row) for row in rows]
    assert len(events) == 11

    first = events[0]
    assert first.chain_id == 146
    assert first.block == 50060028
    assert first.tx_hash == "0x8340c866d4312edb70e4dff0da711b7dbd9b0df08924e4742f706d474c579143"
    assert first.log_index == int("0x77", 16)
    assert first.collateral == "0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38"
    assert first.debt == "0x29219dd400f2bf60e5a23d13be72b486d4038894"
    assert first.user == "0xb3bfb32977cfd6200ab9537e3703e501d8381c9b"
    assert first.liquidator == "0x95654779c314e3390786b98ebcd83f7cbef664ec"
    assert first.debt_to_cover == 1711159398
    assert first.collateral_amount == 21529359566765859279297
    assert first.receive_atoken is False
    assert first.gas_used == int("0x9c569", 16)
    assert first.gas_price == int("0x26d5d735c01", 16)
    assert first.timestamp == int("0x68e978b2", 16)


def test_decode_wrong_topic0_raises_value_error():
    row = {
        "topics": ["0xdeadbeef", "0x" + "0" * 64, "0x" + "0" * 64, "0x" + "0" * 64],
        "data": "0x" + "00" * 128,
        "blockNumber": "0x1",
        "timeStamp": "0x100",
        "gasPrice": "0x1",
        "gasUsed": "0x1",
        "logIndex": "0x0",
        "transactionHash": "0xabc",
    }
    with pytest.raises(ValueError, match="topic0"):
        decode_log(1, row)


def test_decode_too_few_topics_raises_value_error():
    row = {
        "topics": [LIQUIDATION_TOPIC0],
        "data": "0x" + "00" * 128,
        "blockNumber": "0x1",
        "timeStamp": "0x100",
        "gasPrice": "0x1",
        "gasUsed": "0x1",
        "logIndex": "0x0",
        "transactionHash": "0xabc",
    }
    with pytest.raises(ValueError, match="topics"):
        decode_log(1, row)


def test_decode_malformed_data_length_raises_value_error():
    row = {
        "topics": [
            LIQUIDATION_TOPIC0,
            "0x" + "0" * 24 + "1" * 40,
            "0x" + "0" * 24 + "2" * 40,
            "0x" + "0" * 24 + "3" * 40,
        ],
        "data": "0x1234",  # Not 256 hex chars (4 32-byte words)
        "blockNumber": "0x1",
        "timeStamp": "0x100",
        "gasPrice": "0x1",
        "gasUsed": "0x1",
        "logIndex": "0x0",
        "transactionHash": "0xabc",
    }
    with pytest.raises(ValueError, match="data length"):
        decode_log(1, row)


def test_decode_accepts_0x_for_zero_fields():
    # Etherscan encodes zero as a bare "0x": logIndex 0, gasPrice 0.
    fixture_path = Path(__file__).parent / "fixtures" / "sonic_liquidation_block_50060028.json"
    with open(fixture_path) as f:
        row = dict(json.load(f)["result"][0])
    row["logIndex"] = "0x"
    row["gasPrice"] = "0x"

    event = decode_log(146, row)
    assert event.log_index == 0
    assert event.gas_price == 0
