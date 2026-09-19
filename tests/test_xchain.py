"""Tests for Phase 2C cross-chain venue table and adapters."""

import pytest
from mev_scout.xchain import (
    XCHAIN_TOKENS,
    XCHAIN_VENUES,
    VenueConfig,
    encode_get_pool,
    encode_quote,
    decode_quote,
    UNISWAP_GET_POOL_SELECTOR,
    UNISWAP_QUOTE_SELECTOR,
    SLIPSTREAM_GET_POOL_SELECTOR,
    SLIPSTREAM_QUOTE_SELECTOR,
    CLASSIC_GET_POOL_SELECTOR,
    CLASSIC_GET_AMOUNT_OUT_SELECTOR,
)


def test_xchain_tokens():
    """Verify cross-chain tokens for Arbitrum, Base, and Optimism match SPEC-phase2c."""
    # Arbitrum 42161
    assert 42161 in XCHAIN_TOKENS
    arb_weth = XCHAIN_TOKENS[42161]["WETH"]
    arb_usdc = XCHAIN_TOKENS[42161]["USDC"]
    assert arb_weth.address.lower() == "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    assert arb_weth.decimals == 18
    assert arb_usdc.address.lower() == "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
    assert arb_usdc.decimals == 6

    # Base 8453
    assert 8453 in XCHAIN_TOKENS
    base_weth = XCHAIN_TOKENS[8453]["WETH"]
    base_usdc = XCHAIN_TOKENS[8453]["USDC"]
    assert base_weth.address.lower() == "0x4200000000000000000000000000000000000006"
    assert base_weth.decimals == 18
    assert base_usdc.address.lower() == "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    assert base_usdc.decimals == 6

    # Optimism 10
    assert 10 in XCHAIN_TOKENS
    op_weth = XCHAIN_TOKENS[10]["WETH"]
    op_usdc = XCHAIN_TOKENS[10]["USDC"]
    assert op_weth.address.lower() == "0x4200000000000000000000000000000000000006"
    assert op_weth.decimals == 18
    assert op_usdc.address.lower() == "0x0b2c639c533813f4aa9d7837caf62653d097ff85"
    assert op_usdc.decimals == 6


def test_xchain_venues_table():
    """Verify venue table matches verified factories and quoters from SPEC-phase2c."""
    venues_by_chain = {}
    for v in XCHAIN_VENUES:
        venues_by_chain.setdefault(v.chain_id, []).append(v)

    # Arbitrum 42161: 3 venues (Uniswap, Sushi, Pancake)
    assert len(venues_by_chain[42161]) == 3
    arb_names = {v.name: v for v in venues_by_chain[42161]}
    assert "uniswap_v3" in arb_names
    assert "sushiswap_v3" in arb_names
    assert "pancakeswap_v3" in arb_names
    assert arb_names["uniswap_v3"].factory.lower() == "0x1f98431c8ad98523631ae4a59f267346ea31f984"
    assert arb_names["uniswap_v3"].quoter.lower() == "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"
    assert arb_names["pancakeswap_v3"].pool_keys == (100, 500, 2500, 10000)

    # Base 8453: 5 venues (Uni V3, Slipstream 1, Gauge Caps, MinUnstake, Classic)
    assert len(venues_by_chain[8453]) == 5
    base_names = {v.name: v for v in venues_by_chain[8453]}
    assert "uniswap_v3" in base_names
    assert base_names["uniswap_v3"].factory.lower() == "0x33128a8fc17869897dce68ed026d694621f6fdfd"
    assert base_names["uniswap_v3"].quoter.lower() == "0x3d4e44eb1374240ce5f1b871ab261cd16335b76a"

    assert "slipstream_1" in base_names
    assert base_names["slipstream_1"].factory.lower() == "0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a"
    assert base_names["slipstream_1"].quoter.lower() == "0x254cf9e1e6e233aa1ac962cb9b05b2cfeaae15b0"
    assert base_names["slipstream_1"].adapter_type == "slipstream"
    assert base_names["slipstream_1"].pool_keys == (1, 50, 100, 200)

    assert "slipstream_gauge_caps" in base_names
    assert base_names["slipstream_gauge_caps"].factory.lower() == "0xade65c38cd4849adba595a4323a8c7ddfe89716a"
    assert base_names["slipstream_gauge_caps"].quoter.lower() == "0x3d4c22254f86f64b7ec90ab8f7aec1fbfd271c6c"
    assert base_names["slipstream_gauge_caps"].adapter_type == "slipstream"

    assert "slipstream_min_unstake" in base_names
    assert base_names["slipstream_min_unstake"].factory.lower() == "0xf8f2eb4940cfe7d13603dddd87f123820fc061ef"
    assert base_names["slipstream_min_unstake"].quoter.lower() == "0x514c8b5f54112481e28028f1166bd78501089259"
    assert base_names["slipstream_min_unstake"].adapter_type == "slipstream"

    assert "aerodrome_classic" in base_names
    assert base_names["aerodrome_classic"].factory.lower() == "0x420dd381b31aef6683db6b902084cb0ffece40da"
    assert base_names["aerodrome_classic"].quoter is None
    assert base_names["aerodrome_classic"].adapter_type == "aerodrome_classic"
    assert base_names["aerodrome_classic"].pool_keys == (False, True)

    # Optimism 10: 3 venues (Uniswap V3, Velodrome Slipstream, Aero CL)
    assert len(venues_by_chain[10]) == 3
    op_names = {v.name: v for v in venues_by_chain[10]}
    assert "uniswap_v3" in op_names
    assert op_names["uniswap_v3"].factory.lower() == "0x1f98431c8ad98523631ae4a59f267346ea31f984"
    assert op_names["uniswap_v3"].quoter.lower() == "0x61ffe014ba17989e743c5f6cb21bf9697530b21e"

    assert "velodrome_slipstream" in op_names
    assert op_names["velodrome_slipstream"].factory.lower() == "0xcc0bddb707055e04e497ab22a59c2af4391cd12f"
    assert op_names["velodrome_slipstream"].quoter.lower() == "0x89d8218ed5ff1e46d8dcd33fb0bbee3be1621466"
    assert op_names["velodrome_slipstream"].adapter_type == "slipstream"
    assert op_names["velodrome_slipstream"].pool_keys == (1, 50, 100, 200)

    assert "aero_cl_optimism" in op_names
    assert op_names["aero_cl_optimism"].factory.lower() == "0x548118c7e0b865c2cfa94d15ec86b666468ac758"
    assert op_names["aero_cl_optimism"].quoter.lower() == "0xa2decf05c16537c702779083fe067e308463ce45"
    assert op_names["aero_cl_optimism"].adapter_type == "slipstream"


def test_uniswap_adapter_encoding_and_decoding():
    token_a = "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"
    token_b = "0xaf88d065e77c8cc2239327c5edb3a432268e5831"
    fee = 500

    # getPool
    cd_gp = encode_get_pool("uniswap_v3", token_a, token_b, fee)
    assert cd_gp.startswith(UNISWAP_GET_POOL_SELECTOR)
    assert len(cd_gp) == 10 + 64 * 3

    # quote
    amt_in = 10**18
    cd_q = encode_quote("uniswap_v3", token_a, token_b, amt_in, fee)
    assert cd_q.startswith(UNISWAP_QUOTE_SELECTOR)
    assert len(cd_q) == 10 + 64 * 5

    # decode
    amt_out = 2650 * 10**6
    gas_est = 135_000
    res_hex = (
        "0x"
        + hex(amt_out)[2:].rjust(64, "0")
        + "0" * 64
        + "0" * 64
        + hex(gas_est)[2:].rjust(64, "0")
    )
    dec_out, dec_gas = decode_quote("uniswap_v3", res_hex)
    assert dec_out == amt_out
    assert dec_gas == gas_est


def test_slipstream_adapter_encoding_and_decoding():
    token_a = "0x4200000000000000000000000000000000000006"
    token_b = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    tick_spacing = 100

    # getPool
    cd_gp = encode_get_pool("slipstream", token_a, token_b, tick_spacing)
    assert cd_gp.startswith(SLIPSTREAM_GET_POOL_SELECTOR)
    assert SLIPSTREAM_GET_POOL_SELECTOR == "0x28af8d0b"
    assert len(cd_gp) == 10 + 64 * 3

    # quote
    amt_in = 10**18
    cd_q = encode_quote("slipstream", token_a, token_b, amt_in, tick_spacing)
    assert cd_q.startswith(SLIPSTREAM_QUOTE_SELECTOR)
    assert SLIPSTREAM_QUOTE_SELECTOR == "0x9e7defe6"
    assert len(cd_q) == 10 + 64 * 5

    # decode
    amt_out = 2640 * 10**6
    gas_est = 142_000
    res_hex = (
        "0x"
        + hex(amt_out)[2:].rjust(64, "0")
        + "0" * 64
        + "0" * 64
        + hex(gas_est)[2:].rjust(64, "0")
    )
    dec_out, dec_gas = decode_quote("slipstream", res_hex)
    assert dec_out == amt_out
    assert dec_gas == gas_est


def test_classic_aerodrome_adapter_encoding_and_decoding():
    token_a = "0x4200000000000000000000000000000000000006"
    token_b = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

    # getPool (volatile: stable=False)
    cd_gp_vol = encode_get_pool("aerodrome_classic", token_a, token_b, False)
    assert cd_gp_vol.startswith(CLASSIC_GET_POOL_SELECTOR)
    assert CLASSIC_GET_POOL_SELECTOR == "0x79bc57d5"
    assert len(cd_gp_vol) == 10 + 64 * 3
    assert int(cd_gp_vol[10 + 128 : 10 + 192], 16) == 0

    # getPool (stable: stable=True)
    cd_gp_stb = encode_get_pool("aerodrome_classic", token_a, token_b, True)
    assert int(cd_gp_stb[10 + 128 : 10 + 192], 16) == 1

    # quote on pool: Pool.getAmountOut(uint256,address)
    amt_in = 10**18
    cd_q = encode_quote("aerodrome_classic", token_a, token_b, amt_in, False)
    assert cd_q.startswith(CLASSIC_GET_AMOUNT_OUT_SELECTOR)
    assert CLASSIC_GET_AMOUNT_OUT_SELECTOR == "0xf140a35a"
    assert len(cd_q) == 10 + 64 * 2

    # decode
    amt_out = 2635 * 10**6
    res_hex = "0x" + hex(amt_out)[2:].rjust(64, "0")
    dec_out, dec_gas = decode_quote("aerodrome_classic", res_hex)
    assert dec_out == amt_out
    assert dec_gas == 0
