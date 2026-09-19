from unittest.mock import MagicMock
import pytest

from mev_scout.chains import CHAINS
from mev_scout.decode import Liquidation
from mev_scout.validate import (
    ERC20_TRANSFER_TOPIC0,
    ValidationResult,
    validate_chain,
)


def _make_event(tx_hash="0x1", block=100, log_index=0, gas_used=100_000, gas_price=10**9):
    return Liquidation(
        chain_id=146,
        block=block,
        timestamp=1700000000,
        tx_hash=tx_hash,
        log_index=log_index,
        collateral="0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38",
        debt="0x29219dd400f2bf60e5a23d13be72b486d4038894",
        user="0xb3bfb32977cfd6200ab9537e3703e501d8381c9b",
        debt_to_cover=1000 * 10**6,
        collateral_amount=21529359566765859279297,
        liquidator="0x95654779c314e3390786b98ebcd83f7cbef664ec",
        receive_atoken=False,
        gas_used=gas_used,
        gas_price=gas_price,
    )


def _make_reserve_data(atoken_addr="0x6c5e14a212c1c3e4baf6f871ac9b1a969918c131"):
    # 15 32-byte words. Word 8 is aToken address.
    words = ["0" * 64] * 15
    words[8] = atoken_addr.lower().removeprefix("0x").zfill(64)
    return "0x" + "".join(words)


def test_validation_agreeing_receipt_collateral_transfer():
    event = _make_event()
    receipt = {
        "transactionHash": event.tx_hash,
        "gasUsed": hex(event.gas_used),
        "effectiveGasPrice": hex(event.gas_price),
        "logs": [
            {
                "address": event.collateral,
                "topics": [
                    ERC20_TRANSFER_TOPIC0,
                    "0x" + "0" * 24 + event.user[2:],
                    "0x" + "0" * 24 + event.liquidator[2:],
                ],
                "data": hex(event.collateral_amount),
            }
        ],
    }

    rpc = MagicMock()
    rpc.receipt.return_value = receipt
    rpc.call.return_value = _make_reserve_data()

    res = validate_chain([event], chain_id=146, rpc=rpc)
    assert res.total_sampled == 1
    assert res.gas_checks_passed == 1
    assert res.gas_checks_failed == 0
    assert res.transfer_checks_passed == 1
    assert res.transfer_checks_failed == 0
    assert not res.has_warnings


def test_validation_agreeing_receipt_atoken_transfer():
    event = _make_event()
    atoken_addr = "0x6c5e14a212c1c3e4baf6f871ac9b1a969918c131"
    receipt = {
        "transactionHash": event.tx_hash,
        "gasUsed": hex(event.gas_used),
        "effectiveGasPrice": hex(event.gas_price),
        "logs": [
            {
                "address": atoken_addr,
                "topics": [
                    ERC20_TRANSFER_TOPIC0,
                    "0x" + "0" * 24 + event.user[2:],
                    "0x" + "0" * 24 + event.liquidator[2:],
                ],
                "data": hex(event.collateral_amount),
            }
        ],
    }

    rpc = MagicMock()
    rpc.receipt.return_value = receipt
    rpc.call.return_value = _make_reserve_data(atoken_addr=atoken_addr)

    res = validate_chain([event], chain_id=146, rpc=rpc)
    assert res.transfer_checks_passed == 1
    assert res.transfer_checks_failed == 0


def test_validation_gas_mismatch():
    event = _make_event()
    receipt = {
        "transactionHash": event.tx_hash,
        "gasUsed": hex(event.gas_used),
        "effectiveGasPrice": hex(event.gas_price + 100),  # mismatch
        "logs": [
            {
                "address": event.collateral,
                "topics": [
                    ERC20_TRANSFER_TOPIC0,
                    "0x" + "0" * 24 + event.user[2:],
                    "0x" + "0" * 24 + event.liquidator[2:],
                ],
                "data": hex(event.collateral_amount),
            }
        ],
    }

    rpc = MagicMock()
    rpc.receipt.return_value = receipt
    rpc.call.return_value = _make_reserve_data()

    res = validate_chain([event], chain_id=146, rpc=rpc)
    assert res.gas_checks_passed == 0
    assert res.gas_checks_failed == 1
    assert event.tx_hash in res.gas_disagreements[0]
    assert res.has_warnings


def test_validation_missing_transfer():
    event = _make_event()
    receipt = {
        "transactionHash": event.tx_hash,
        "gasUsed": hex(event.gas_used),
        "effectiveGasPrice": hex(event.gas_price),
        "logs": [],  # no transfers
    }

    rpc = MagicMock()
    rpc.receipt.return_value = receipt
    rpc.call.return_value = _make_reserve_data()

    res = validate_chain([event], chain_id=146, rpc=rpc)
    assert res.transfer_checks_passed == 0
    assert res.transfer_checks_failed == 1
    assert event.tx_hash in res.transfer_disagreements[0]
    assert res.has_warnings


def test_validation_reserve_data_tuple_length_mismatch():
    event = _make_event()
    receipt = {
        "transactionHash": event.tx_hash,
        "gasUsed": hex(event.gas_used),
        "effectiveGasPrice": hex(event.gas_price),
        "logs": [],
    }

    rpc = MagicMock()
    rpc.receipt.return_value = receipt
    # Only 10 words instead of 15
    rpc.call.return_value = "0x" + "0" * (10 * 64)

    res = validate_chain([event], chain_id=146, rpc=rpc)
    assert res.transfer_checks_not_run == 1
    assert res.transfer_checks_failed == 0
    assert "expected 15" in res.not_run_reasons[0]


def test_validation_sample_cap_at_20():
    events = [_make_event(tx_hash=f"0x{i}", log_index=i) for i in range(50)]
    rpc = MagicMock()
    rpc.receipt.return_value = {
        "transactionHash": "0x0",
        "gasUsed": hex(100_000),
        "effectiveGasPrice": hex(10**9),
        "logs": [],
    }
    rpc.call.return_value = _make_reserve_data()

    res = validate_chain(events, chain_id=146, rpc=rpc)
    assert res.total_sampled == 20
    assert rpc.receipt.call_count == 20
