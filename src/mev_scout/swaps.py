"""Decoders for Uniswap V3, SushiSwap V3, and PancakeSwap V3 Swap logs."""

from dataclasses import dataclass
from typing import Any

from mev_scout.hexint import parse_int

UNISWAP_SWAP_TOPIC0 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
PANCAKESWAP_SWAP_TOPIC0 = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"


@dataclass(frozen=True)
class DecodedSwap:
    chain_id: int
    dex: str
    pool: str
    block: int
    timestamp: int
    tx_hash: str
    log_index: int
    sender: str
    recipient: str
    amount0: int
    amount1: int
    sqrt_price_x96: int
    liquidity: int
    tick: int
    protocol_fees_token0: int = 0
    protocol_fees_token1: int = 0


def _to_signed_256(hex_str: str) -> int:
    val = int(hex_str, 16)
    if val >= (1 << 255):
        val -= (1 << 256)
    return val


def decode_swap_log(chain_id: int, row: dict[str, Any], dex: str | None = None) -> DecodedSwap:
    """Decode a Uniswap V3, SushiSwap V3, or PancakeSwap V3 Swap log."""
    topics = row.get("topics")
    if not isinstance(topics, list) or len(topics) < 3:
        raise ValueError(f"Expected at least 3 topics, got {topics!r}")

    topic0 = str(topics[0]).lower()
    is_pancake = topic0 == PANCAKESWAP_SWAP_TOPIC0.lower()
    is_uni_or_sushi = topic0 == UNISWAP_SWAP_TOPIC0.lower()

    if not (is_pancake or is_uni_or_sushi):
        raise ValueError(f"Invalid topic0: {topic0}")

    if is_pancake:
        if dex is not None and dex != "pancakeswap_v3":
            raise ValueError(f"Expected topic0 for {dex}, but got PancakeSwap topic0 {topic0}")
        actual_dex = "pancakeswap_v3"
        expected_len = 448  # 7 * 64
    else:
        if dex == "pancakeswap_v3":
            raise ValueError(f"Expected PancakeSwap topic0, but got Uniswap topic0 {topic0}")
        actual_dex = dex or "uniswap_v3"
        expected_len = 320  # 5 * 64

    clean_data = str(row.get("data", "")).removeprefix("0x").removeprefix("0X")
    if len(clean_data) != expected_len:
        raise ValueError(f"Invalid data length: expected {expected_len} hex characters, got {len(clean_data)}")

    sender = "0x" + str(topics[1])[-40:].lower()
    recipient = "0x" + str(topics[2])[-40:].lower()

    amount0 = _to_signed_256(clean_data[0:64])
    amount1 = _to_signed_256(clean_data[64:128])
    sqrt_price_x96 = int(clean_data[128:192], 16)
    liquidity = int(clean_data[192:256], 16)
    tick = _to_signed_256(clean_data[256:320])

    if not (-(1 << 23) <= tick < (1 << 23)):
        raise ValueError(f"Decoded tick {tick} out of int24 range")

    protocol_fees_token0 = 0
    protocol_fees_token1 = 0
    if is_pancake:
        protocol_fees_token0 = int(clean_data[320:384], 16)
        protocol_fees_token1 = int(clean_data[384:448], 16)

    pool = str(row.get("address", "")).lower()
    block = parse_int(row["blockNumber"])
    timestamp = parse_int(row["timeStamp"])
    tx_hash = str(row["transactionHash"]).lower()
    log_index = parse_int(row["logIndex"])

    return DecodedSwap(
        chain_id=chain_id,
        dex=actual_dex,
        pool=pool,
        block=block,
        timestamp=timestamp,
        tx_hash=tx_hash,
        log_index=log_index,
        sender=sender,
        recipient=recipient,
        amount0=amount0,
        amount1=amount1,
        sqrt_price_x96=sqrt_price_x96,
        liquidity=liquidity,
        tick=tick,
        protocol_fees_token0=protocol_fees_token0,
        protocol_fees_token1=protocol_fees_token1,
    )
