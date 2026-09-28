"""Decoders for Aave V3 LiquidationCall events."""

from dataclasses import dataclass
from typing import Any

from mev_scout.hexint import parse_int

LIQUIDATION_TOPIC0 = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"


@dataclass(frozen=True)
class Liquidation:
    chain_id: int
    block: int
    timestamp: int
    tx_hash: str
    log_index: int
    collateral: str
    debt: str
    user: str
    debt_to_cover: int
    collateral_amount: int
    liquidator: str
    receive_atoken: bool
    gas_used: int
    gas_price: int


def decode_log(chain_id: int, row: dict[str, Any]) -> Liquidation:
    topics = row.get("topics")
    if not isinstance(topics, list) or len(topics) < 4:
        raise ValueError(f"Expected at least 4 topics, got {topics!r}")

    if topics[0].lower() != LIQUIDATION_TOPIC0.lower():
        raise ValueError(f"Invalid topic0: expected {LIQUIDATION_TOPIC0}, got {topics[0]}")

    collateral = "0x" + topics[1][-40:].lower()
    debt = "0x" + topics[2][-40:].lower()
    user = "0x" + topics[3][-40:].lower()

    raw_data = str(row.get("data", ""))
    clean_data = raw_data.removeprefix("0x").removeprefix("0X")
    if len(clean_data) != 256:
        raise ValueError(f"Invalid data length: expected 256 hex characters, got {len(clean_data)}")

    debt_to_cover = int(clean_data[0:64], 16)
    collateral_amount = int(clean_data[64:128], 16)
    liquidator = "0x" + clean_data[128:192][-40:].lower()
    receive_atoken = bool(int(clean_data[192:256], 16))

    block = parse_int(row["blockNumber"])
    timestamp = parse_int(row["timeStamp"])
    tx_hash = str(row["transactionHash"]).lower()
    log_index = parse_int(row["logIndex"])
    gas_used = parse_int(row["gasUsed"])
    gas_price = parse_int(row["gasPrice"])

    return Liquidation(
        chain_id=chain_id,
        block=block,
        timestamp=timestamp,
        tx_hash=tx_hash,
        log_index=log_index,
        collateral=collateral,
        debt=debt,
        user=user,
        debt_to_cover=debt_to_cover,
        collateral_amount=collateral_amount,
        liquidator=liquidator,
        receive_atoken=receive_atoken,
        gas_used=gas_used,
        gas_price=gas_price,
    )
