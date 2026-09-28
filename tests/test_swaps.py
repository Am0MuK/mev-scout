"""Tests for swap log decoding across Uniswap, SushiSwap, and PancakeSwap."""

import json
from pathlib import Path
import pytest

from mev_scout.dex import DEXES
from mev_scout.swaps import (
    PANCAKESWAP_SWAP_TOPIC0,
    UNISWAP_SWAP_TOPIC0,
    DecodedSwap,
    decode_swap_log,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arb"


def test_decode_uniswap_fixture():
    with open(FIXTURES_DIR / "uniswap_v3_weth_usdc_500_swaps.json") as f:
        rows = json.load(f)["result"]

    assert len(rows) == 20
    swaps = [decode_swap_log(chain_id=42161, row=r, dex="uniswap_v3") for r in rows]
    assert len(swaps) == 20

    first = swaps[0]
    assert first.chain_id == 42161
    assert first.dex == "uniswap_v3"
    assert first.pool.lower() == "0xc6962004f452be9203591991d15f6b388e09e8d0"
    assert first.amount0 == -679714168064271602
    assert first.amount1 == 1786734916
    assert first.sqrt_price_x96 == 4061072399780078164115456
    assert first.liquidity == 2656185379376736927
    assert first.tick == -197583
    assert first.protocol_fees_token0 == 0
    assert first.protocol_fees_token1 == 0
    assert first.sender.startswith("0x") and len(first.sender) == 42
    assert first.recipient.startswith("0x") and len(first.recipient) == 42
    # One token paid in, one token taken out
    assert (first.amount0 > 0 and first.amount1 < 0) or (first.amount0 < 0 and first.amount1 > 0)


def test_decode_sushi_fixture():
    with open(FIXTURES_DIR / "sushi_v3_weth_usdc_500_swaps.json") as f:
        rows = json.load(f)["result"]

    assert len(rows) == 19
    swaps = [decode_swap_log(chain_id=42161, row=r, dex="sushiswap_v3") for r in rows]
    assert len(swaps) == 19

    first = swaps[0]
    assert first.chain_id == 42161
    assert first.dex == "sushiswap_v3"
    assert first.pool.lower() == "0xf3eb87c1f6020982173c908e7eb31aa66c1f0296"
    assert (first.amount0 > 0 and first.amount1 < 0) or (first.amount0 < 0 and first.amount1 > 0)
    assert -(1 << 23) <= first.tick < (1 << 23)


def test_decode_pancake_fixture():
    with open(FIXTURES_DIR / "pancake_v3_weth_usdc_500_swaps.json") as f:
        rows = json.load(f)["result"]

    assert len(rows) == 20
    swaps = [decode_swap_log(chain_id=42161, row=r, dex="pancakeswap_v3") for r in rows]
    assert len(swaps) == 20

    first = swaps[0]
    assert first.chain_id == 42161
    assert first.dex == "pancakeswap_v3"
    assert first.pool.lower() == "0xd9e2a1a61b6e61b275cec326465d417e52c1b95c"
    assert first.amount0 == -99094734270087108
    assert first.amount1 == 260497601
    assert first.sqrt_price_x96 == 4061338557276608723383287
    assert first.liquidity == 50979364761886380
    assert first.tick == -197582
    assert -(1 << 23) <= first.tick < (1 << 23)
    assert first.protocol_fees_token0 >= 0
    assert first.protocol_fees_token1 >= 0


def test_uniswap_topic_filter_on_pancake_fixture_returns_nothing():
    """Filtering on Uniswap topic silently drops every PancakeSwap swap."""
    with open(FIXTURES_DIR / "pancake_v3_weth_usdc_500_swaps.json") as f:
        rows = json.load(f)["result"]

    # If an explorer query or filter searched for UNISWAP_SWAP_TOPIC0:
    filtered_rows = [
        r for r in rows
        if r.get("topics", []) and r["topics"][0].lower() == UNISWAP_SWAP_TOPIC0.lower()
    ]
    # Proves that 0 rows match
    assert len(filtered_rows) == 0

    # All 20 match the PANCAKESWAP_SWAP_TOPIC0
    pancake_filtered = [
        r for r in rows
        if r.get("topics", []) and r["topics"][0].lower() == PANCAKESWAP_SWAP_TOPIC0.lower()
    ]
    assert len(pancake_filtered) == len(rows)


def test_decode_invalid_topics_or_length():
    row_few_topics = {
        "address": "0xc6962004f452be9203591991d15f6b388e09e8d0",
        "blockNumber": "0x1",
        "timeStamp": "0x1",
        "transactionHash": "0x123",
        "logIndex": "0x0",
        "topics": ["0x0000000000000000000000000000000000000000000000000000000000000000"],
        "data": "0x" + "0" * 320,
    }
    with pytest.raises(ValueError, match="Expected at least 3 topics"):
        decode_swap_log(42161, row_few_topics)

    row_bad_topic = {
        "address": "0xc6962004f452be9203591991d15f6b388e09e8d0",
        "blockNumber": "0x1",
        "timeStamp": "0x1",
        "transactionHash": "0x123",
        "logIndex": "0x0",
        "topics": [
            "0x0000000000000000000000000000000000000000000000000000000000000000",
            "0x" + "0" * 64,
            "0x" + "0" * 64,
        ],
        "data": "0x" + "0" * 320,
    }
    with pytest.raises(ValueError, match="Invalid topic0"):
        decode_swap_log(42161, row_bad_topic)

    row_bad_len = {
        "address": "0xc6962004f452be9203591991d15f6b388e09e8d0",
        "blockNumber": "0x1",
        "timeStamp": "0x1",
        "transactionHash": "0x123",
        "logIndex": "0x0",
        "topics": [
            UNISWAP_SWAP_TOPIC0,
            "0x" + "0" * 64,
            "0x" + "0" * 64,
        ],
        "data": "0x1234",
    }
    with pytest.raises(ValueError, match="Invalid data length"):
        decode_swap_log(42161, row_bad_len)


def test_decode_swap_accepts_0x_for_log_index_zero():
    # Etherscan encodes logIndex 0 as a bare "0x".
    with open(FIXTURES_DIR / "uniswap_v3_weth_usdc_500_swaps.json") as f:
        row = dict(json.load(f)["result"][0])
    row["logIndex"] = "0x"

    assert decode_swap_log(chain_id=42161, row=row, dex="uniswap_v3").log_index == 0
