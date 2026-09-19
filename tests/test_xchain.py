"""Tests for Phase 2C cross-chain venue table and adapters."""

import pytest
from decimal import Decimal
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


def test_shallow_check_logic():
    from mev_scout.xchain import is_shallow_quote

    # 1 WETH = 10**18, 10 WETH = 10 * 10**18
    # 1x price = 2600 USDC
    amt_in_1x = 10**18
    quote_1x = 2600 * 10**6

    # 10x quote = 25900 USDC (price 2590, drop = 0.38% <= 2%) -> NOT shallow
    assert is_shallow_quote(amt_in_1x, quote_1x, 25900 * 10**6) is False

    # 10x quote = 25480 USDC (price 2548, drop = exactly 2.0%) -> NOT shallow
    assert is_shallow_quote(amt_in_1x, quote_1x, 25480 * 10**6) is False

    # 10x quote = 25479 USDC (drop > 2%) -> IS shallow
    assert is_shallow_quote(amt_in_1x, quote_1x, 25479 * 10**6) is True

    # Aerodrome tickSpacing 200 real example: 1 WETH -> 546, 10 WETH -> 546
    assert is_shallow_quote(10**18, 546 * 10**6, 546 * 10**6) is True

    # Reverted / zero quotes are treated as shallow
    assert is_shallow_quote(10**18, 0, 10**6) is True
    assert is_shallow_quote(10**18, 10**6, 0) is True


def test_discover_xchain_pools_base():
    from mev_scout.xchain import XChainPool, discover_xchain_pools
    from mev_scout.rpc import ContractCallError

    base_weth = XCHAIN_TOKENS[8453]["WETH"].address.lower()
    base_usdc = XCHAIN_TOKENS[8453]["USDC"].address.lower()
    token0 = min(base_weth, base_usdc)

    pool_uni = "0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce"
    pool_slip = "0xb2cc224c1c9fe33e329736a701460002d2c1130e"
    pool_classic = "0x42000000000000000000000000000000000000aa"

    # Fake RPC responses
    uni_factory = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
    slip_factory = "0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a"
    classic_factory = "0x420dd381b31aef6683db6b902084cb0ffece40da"

    # Call mappings
    call_responses = {
        # Uni V3 fee 100 on Base
        (uni_factory.lower(), encode_get_pool("uniswap_v3", base_weth, base_usdc, 100).lower(), "latest"): (
            "0x" + "0" * 24 + pool_uni[2:]
        ),
        (pool_uni.lower(), "0x0dfe1681", "latest"): "0x" + "0" * 24 + token0[2:],
        (pool_uni.lower(), "0xddca3f43", "latest"): "0x" + hex(100)[2:].rjust(64, "0"),

        # Slipstream 1 ts 100 on Base
        (slip_factory.lower(), encode_get_pool("slipstream", base_weth, base_usdc, 100).lower(), "latest"): (
            "0x" + "0" * 24 + pool_slip[2:]
        ),
        (pool_slip.lower(), "0x0dfe1681", "latest"): "0x" + "0" * 24 + token0[2:],
        (pool_slip.lower(), "0xd0c93a7c", "latest"): "0x" + hex(100)[2:].rjust(64, "0"),

        # Classic Aerodrome volatile on Base
        (classic_factory.lower(), encode_get_pool("aerodrome_classic", base_weth, base_usdc, False).lower(), "latest"): (
            "0x" + "0" * 24 + pool_classic[2:]
        ),
        (pool_classic.lower(), "0x0dfe1681", "latest"): "0x" + "0" * 24 + token0[2:],
    }

    class FakeRpc:
        def call(self, to: str, data: str, block: str | int = "latest") -> str:
            key = (to.lower(), data.lower(), str(block).lower())
            if key in call_responses:
                return call_responses[key]
            # Other getPool calls return zero address (no pool)
            if data.startswith("0x1698ee82") or data.startswith("0x28af8d0b") or data.startswith("0x79bc57d5"):
                return "0x" + "0" * 64
            raise ContractCallError(f"No fake response for {to} {data}")

        def batch_call(self, calls: list[tuple[str, str, str | int]]) -> list[str]:
            res = []
            for c in calls:
                try:
                    res.append(self.call(c[0], c[1], c[2]))
                except ContractCallError as exc:
                    res.append(exc)
            return res

    rpc = FakeRpc()
    discovered = discover_xchain_pools(8453, rpc)

    assert len(discovered) == 3
    by_addr = {p.address.lower(): p for p in discovered}
    assert pool_uni.lower() in by_addr
    assert by_addr[pool_uni.lower()].adapter_type == "uniswap_v3"
    assert by_addr[pool_uni.lower()].pool_key == 100

    assert pool_slip.lower() in by_addr
    assert by_addr[pool_slip.lower()].adapter_type == "slipstream"
    assert by_addr[pool_slip.lower()].pool_key == 100

    assert pool_classic.lower() in by_addr
    assert by_addr[pool_classic.lower()].adapter_type == "aerodrome_classic"
    assert by_addr[pool_classic.lower()].pool_key is False


def test_discover_xchain_pools_verification_mismatch_raises():
    from mev_scout.xchain import discover_xchain_pools
    from mev_scout.rpc import ContractCallError

    base_weth = XCHAIN_TOKENS[8453]["WETH"].address.lower()
    base_usdc = XCHAIN_TOKENS[8453]["USDC"].address.lower()
    uni_factory = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
    pool_addr = "0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce"

    call_responses = {
        (uni_factory.lower(), encode_get_pool("uniswap_v3", base_weth, base_usdc, 100).lower(), "latest"): (
            "0x" + "0" * 24 + pool_addr[2:]
        ),
        # Wrong token0
        (pool_addr.lower(), "0x0dfe1681", "latest"): "0x" + "0" * 24 + "11" * 20,
        (pool_addr.lower(), "0xddca3f43", "latest"): "0x" + hex(100)[2:].rjust(64, "0"),
    }

    class FakeRpc:
        def call(self, to: str, data: str, block: str | int = "latest") -> str:
            key = (to.lower(), data.lower(), str(block).lower())
            if key in call_responses:
                return call_responses[key]
            if data.startswith("0x1698ee82") or data.startswith("0x28af8d0b") or data.startswith("0x79bc57d5"):
                return "0x" + "0" * 64
            raise ContractCallError(f"No fake response for {to} {data}")

        def batch_call(self, calls):
            return [self.call(c[0], c[1], c[2]) for c in calls]

    rpc = FakeRpc()
    with pytest.raises(ValueError, match="token0 mismatch"):
        discover_xchain_pools(8453, rpc)


def test_store_xchain_pools():
    from mev_scout.store import Store
    from mev_scout.xchain import XChainPool

    store = Store(":memory:")
    pool1 = XChainPool(
        chain_id=8453,
        venue_name="uniswap_v3",
        address="0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=100,
        adapter_type="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
        quoter="0x3d4e44eb1374240ce5f1b871ab261cd16335b76a",
    )
    pool2 = XChainPool(
        chain_id=8453,
        venue_name="slipstream_1",
        address="0xb2cc224c1c9fe33e329736a701460002d2c1130e",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=100,
        adapter_type="slipstream",
        factory="0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a",
        quoter="0x254cf9e1e6e233aa1ac962cb9b05b2cfeaae15b0",
    )

    store.insert_xchain_pools([pool1, pool2])
    retrieved = store.get_xchain_pools(8453)
    assert len(retrieved) == 2
    by_addr = {p.address.lower(): p for p in retrieved}
    assert pool1.address.lower() in by_addr
    assert by_addr[pool1.address.lower()].adapter_type == "uniswap_v3"
    assert by_addr[pool1.address.lower()].pool_key == 100
    assert pool2.address.lower() in by_addr
    assert by_addr[pool2.address.lower()].adapter_type == "slipstream"


def test_generate_time_grid():
    from mev_scout.xchain import generate_time_grid

    # 15 minutes window: 0 to 900
    # Regular 5-min step (300 s): 0, 300, 600, 900
    # Dense range: 180:360 (every 60 s): 180, 240, 300, 360
    grid = generate_time_grid(
        start_ts=0,
        end_ts=900,
        step_s=300,
        dense_ranges=["180:360"],
        dense_step_s=60,
    )
    expected = [0, 180, 240, 300, 360, 600, 900]
    assert grid == expected


def test_find_block_by_timestamp_binary_search():
    from mev_scout.xchain import find_block_by_timestamp

    # 10 blocks: block N has timestamp 1000 + N * 2 (blocks every 2 s)
    # block 1: 1002, block 2: 1004, ..., block 10: 1020
    block_timestamps = {i: 1000 + i * 2 for i in range(1, 11)}
    queried_blocks = []

    class MockBlockRpc:
        def block_number(self) -> int:
            return 10

        def _call(self, method: str, params: list):
            if method == "eth_getBlockByNumber":
                b_num = int(params[0], 16) if isinstance(params[0], str) and params[0].startswith("0x") else int(params[0])
                queried_blocks.append(b_num)
                if b_num in block_timestamps:
                    return {"number": hex(b_num), "timestamp": hex(block_timestamps[b_num])}
                return None
            raise ValueError(f"Unexpected method: {method}")

    rpc = MockBlockRpc()

    # Query timestamp 1009 -> block 4 has ts 1008, block 5 has ts 1010 -> should return block 4
    blk, ts = find_block_by_timestamp(chain_id=8453, target_ts=1009, rpc=rpc, low=1, high=10)
    assert blk == 4
    assert ts == 1008

    # Query exact timestamp 1014 -> block 7 has ts 1014 -> should return block 7
    blk, ts = find_block_by_timestamp(chain_id=8453, target_ts=1014, rpc=rpc, low=1, high=10)
    assert blk == 7
    assert ts == 1014

    # Query target >= tip timestamp (e.g. 1025 >= 1020) -> returns tip (block 10)
    blk, ts = find_block_by_timestamp(chain_id=8453, target_ts=1025, rpc=rpc, low=1, high=10)
    assert blk == 10
    assert ts == 1020


def test_find_block_by_timestamp_caching():
    from mev_scout.xchain import find_block_by_timestamp
    from mev_scout.store import Store

    store = Store(":memory:")
    calls = []

    class MockBlockRpc:
        def block_number(self) -> int:
            return 5

        def _call(self, method: str, params: list):
            b_num = int(params[0], 16) if isinstance(params[0], str) and params[0].startswith("0x") else int(params[0])
            calls.append(b_num)
            return {"number": hex(b_num), "timestamp": hex(1000 + b_num * 2)}

    rpc = MockBlockRpc()
    # First search
    blk1, _ = find_block_by_timestamp(chain_id=10, target_ts=1005, rpc=rpc, store=store, low=1, high=5)
    call_count_1 = len(calls)
    assert call_count_1 > 0

    # Second search with same target and store
    blk2, _ = find_block_by_timestamp(chain_id=10, target_ts=1005, rpc=rpc, store=store, low=1, high=5)
    assert blk1 == blk2
    # Cached block timestamps mean fewer/no RPC calls for the same blocks
    call_count_2 = len(calls)
    assert call_count_2 == call_count_1


def test_check_moment_skew_guard():
    from mev_scout.xchain import check_moment_skew

    moment = 1_000_000

    # Case 1: Within 5 seconds on all chains -> passes
    # Chain 42161: block at 999,998 (age 2 s)
    # Chain 8453:  block at 999,997 (age 3 s)
    # Chain 10:    block at 999,996 (age 4 s)
    chain_blocks = {
        42161: (100, 999_998),
        8453: (200, 999_997),
        10: (300, 999_996),
    }
    passes, skew = check_moment_skew(moment, chain_blocks)
    assert passes is True
    assert skew == 999_998 - 999_996  # 2 seconds skew

    # Case 2: One chain's block is more than 5 s older (> 5 s) -> dropped
    # Chain 10: block at 999,994 (age 6 s > 5 s)
    chain_blocks_skewed = {
        42161: (100, 999_998),
        8453: (200, 999_997),
        10: (300, 999_994),
    }
    passes, skew = check_moment_skew(moment, chain_blocks_skewed)
    assert passes is False
    assert skew == 999_998 - 999_994  # 4 seconds

    # Case 3: Exactly 5 seconds -> passes (not more than 5 s older)
    chain_blocks_boundary = {
        42161: (100, 1_000_000),
        8453: (200, 999_995),  # 1_000_000 - 999_995 = 5 s
    }
    passes, skew = check_moment_skew(moment, chain_blocks_boundary)
    assert passes is True
    assert skew == 5


def test_encode_and_get_l1_fee():
    from mev_scout.xchain import (
        encode_get_l1_fee,
        get_l1_fee,
        GAS_PRICE_ORACLE_ADDRESS,
        GET_L1_FEE_SELECTOR,
    )

    # 1. Encoding check
    calldata = encode_get_l1_fee(400)
    assert calldata.startswith(GET_L1_FEE_SELECTOR)
    assert GET_L1_FEE_SELECTOR == "0x49948e0e"
    # Offset 32 (0x20)
    assert calldata[10 : 10 + 64] == "0" * 62 + "20"
    # Length 400 (0x190)
    assert calldata[10 + 64 : 10 + 128] == "0" * 61 + "190"
    # Padded data 416 bytes (832 hex chars)
    assert len(calldata[10 + 128 :]) == 832

    # 2. get_l1_fee for Arbitrum (42161) returns 0 without calling RPC
    class FailRpc:
        def call(self, *args, **kwargs):
            raise AssertionError("Should not be called for Arbitrum")

    assert get_l1_fee(FailRpc(), chain_id=42161, block=123) == 0

    # 3. get_l1_fee for Base (8453) and Optimism (10)
    class MockL1Rpc:
        def __init__(self, fee_wei: int):
            self.fee_wei = fee_wei
            self.calls = []

        def call(self, to: str, data: str, block: int | str):
            self.calls.append((to.lower(), data, block))
            return "0x" + hex(self.fee_wei)[2:].rjust(64, "0")

    mock_base = MockL1Rpc(fee_wei=2_880_749_262)
    fee_base = get_l1_fee(mock_base, chain_id=8453, block=1000)
    assert fee_base == 2_880_749_262
    assert mock_base.calls[0][0] == GAS_PRICE_ORACLE_ADDRESS.lower()
    assert mock_base.calls[0][2] == 1000

    mock_op = MockL1Rpc(fee_wei=4_678_797_641)
    fee_op = get_l1_fee(mock_op, chain_id=10, block=2000)
    assert fee_op == 4_678_797_641


def test_get_base_fee_per_gas():
    from mev_scout.xchain import get_base_fee_per_gas

    class MockBlockFeeRpc:
        def __init__(self, block_dict: dict):
            self.block_dict = block_dict

        def _call(self, method: str, params: list):
            assert method == "eth_getBlockByNumber"
            return self.block_dict

    # Valid hex base fee: 0x3b9aca00 = 1 gwei
    rpc = MockBlockFeeRpc({"number": "0x64", "baseFeePerGas": "0x3b9aca00"})
    assert get_base_fee_per_gas(rpc, 100) == 1_000_000_000

    # Missing base fee -> ValueError (no fallback)
    rpc_missing = MockBlockFeeRpc({"number": "0x64"})
    with pytest.raises(ValueError, match="no baseFeePerGas"):
        get_base_fee_per_gas(rpc_missing, 100)

    # Non-positive base fee -> ValueError (no fallback)
    rpc_zero = MockBlockFeeRpc({"number": "0x64", "baseFeePerGas": "0x0"})
    with pytest.raises(ValueError, match="non-positive"):
        get_base_fee_per_gas(rpc_zero, 100)


def test_decode_slot0_mid_price_and_prefilter():
    from decimal import Decimal
    from mev_scout.xchain import decode_slot0_weth_usdc_price, check_prefilter

    # For WETH = 2600 USD:
    # On Arbitrum/Base: WETH is token0 (18 dec), USDC is token1 (6 dec)
    # price = (sqrtPriceX96 / 2^96)^2 * 10^12 = 2600
    # sqrtPriceX96 = 2^96 * sqrt(2600 * 10^-12)
    # 2600 * 10^-12 = 2.6e-9
    # sqrt(2.6e-9) = 5.0990195135927845e-05
    # 2^96 ~ 7.922816251426434e+28
    # sqrtPriceX96 ~ 4.03986e+24
    sqrt_arb = int((Decimal(2600) / Decimal(10**12)).sqrt() * Decimal(2**96))
    slot0_arb = hex(sqrt_arb)[2:].rjust(64, "0") + "0" * 128
    price_arb = decode_slot0_weth_usdc_price(42161, slot0_arb)
    assert abs(price_arb - Decimal("2600")) < Decimal("0.01")

    # On Optimism: USDC is token0 (6 dec), WETH is token1 (18 dec)
    # price = (1 / (sqrtPriceX96 / 2^96)^2) * 10^12 = 2600
    # sqrtPriceX96 = 2^96 * sqrt(10^12 / 2600)
    sqrt_op = int((Decimal(10**12) / Decimal(2600)).sqrt() * Decimal(2**96))
    slot0_op = hex(sqrt_op)[2:].rjust(64, "0") + "0" * 128
    price_op = decode_slot0_weth_usdc_price(10, slot0_op)
    assert abs(price_op - Decimal("2600")) < Decimal("0.01")

    # Prefilter check:
    # Arbitrum fee = 500 (0.05%), Base fee = 100 (0.01%)
    # Total fee = 0.06% = 0.0006
    # rebalance_pct = 0.05% = 0.0005
    # Threshold = 0.0011 (11 bps)
    # Case 1: Gap = 8 bps (below threshold) -> passes = False
    p_cheaper = Decimal("2600.00")
    p_dearer_narrow = Decimal("2602.08")  # gap = 2.08 / 2600 = 0.0008 = 8 bps
    passes, gap, threshold = check_prefilter(
        price_a=p_cheaper,
        fee_a=500,
        price_b=p_dearer_narrow,
        fee_b=100,
        rebalance_pct=Decimal("0.0005"),
    )
    assert passes is False
    assert threshold == Decimal("0.0011")

    # Case 2: Gap = exactly threshold (11 bps) -> boundary exact: skip
    p_dearer_exact = p_cheaper * (Decimal("1") + threshold)
    passes, gap, _ = check_prefilter(
        price_a=p_cheaper,
        fee_a=500,
        price_b=p_dearer_exact,
        fee_b=100,
        rebalance_pct=Decimal("0.0005"),
    )
    assert passes is False

    # Case 3: Gap = 20 bps (above threshold) -> passes = True
    p_dearer_wide = p_cheaper * Decimal("1.0020")
    passes, gap, _ = check_prefilter(
        price_a=p_cheaper,
        fee_a=500,
        price_b=p_dearer_wide,
        fee_b=100,
        rebalance_pct=Decimal("0.0005"),
    )
    assert passes is True


def test_get_best_quotes_and_rejection():
    from mev_scout.xchain import (
        XChainPool,
        get_best_buy_quote,
        get_best_sell_quote,
    )
    from mev_scout.rpc import ContractCallError

    # Pools on Base
    pool_deep = XChainPool(
        chain_id=8453,
        venue_name="uniswap_v3",
        address="0x1111111111111111111111111111111111111111",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=500,
        adapter_type="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
    )
    pool_shallow = XChainPool(
        chain_id=8453,
        venue_name="uniswap_v3",
        address="0x2222222222222222222222222222222222222222",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=100,
        adapter_type="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
    )
    pool_reverting = XChainPool(
        chain_id=8453,
        venue_name="uniswap_v3",
        address="0x3333333333333333333333333333333333333333",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=3000,
        adapter_type="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
    )

    class MockQuoterRpc:
        def call(self, to: str, data: str, block: int | str):
            # Parse pool key or amount to distinguish
            fee = int(data[10 + 192 : 10 + 256], 16)
            amt_in = int(data[10 + 128 : 10 + 192], 16)

            if fee == 3000:
                raise ContractCallError("execution reverted")

            if fee == 100:
                # Shallow pool: 1x gives absurd high quote, 10x drops by 50%
                if amt_in < 50_000 * 10**6:  # 1x for 10k
                    out = int(4.0 * 10**18)
                else:  # 10x
                    out = int(20.0 * 10**18)  # 2.0 per 10k -> 50% drop
                gas = 120_000
                res = (
                    hex(out)[2:].rjust(64, "0")
                    + "0" * 128
                    + hex(gas)[2:].rjust(64, "0")
                )
                return "0x" + res

            if fee == 500:
                # Deep pool: 1x gives 3.84 WETH, 10x gives 38.3 WETH (drop = 0.26% <= 2%)
                if amt_in < 50_000 * 10**6:
                    out = int(3.84 * 10**18)
                else:
                    out = int(38.3 * 10**18)
                gas = 110_000
                res = (
                    hex(out)[2:].rjust(64, "0")
                    + "0" * 128
                    + hex(gas)[2:].rjust(64, "0")
                )
                return "0x" + res

            raise ValueError("unknown call")

    rpc = MockQuoterRpc()
    # Buy quote for 10k USD:
    # pool_shallow gives 4.0 WETH (higher than 3.84) but fails shallow check
    # pool_reverting reverts and is skipped
    # pool_deep passes shallow check and is chosen!
    best_buy = get_best_buy_quote(
        rpc=rpc,
        chain_id=8453,
        block=100,
        pools=[pool_shallow, pool_reverting, pool_deep],
        size_usd=Decimal("10000"),
    )
    assert best_buy is not None
    assert best_buy.pool.address == pool_deep.address
    assert best_buy.amount_out == int(3.84 * 10**18)
    assert best_buy.gas_estimate == 110_000


def test_evaluate_cross_chain_gap():
    from mev_scout.xchain import (
        XChainPool,
        evaluate_cross_chain_gap,
        XChainOpportunity,
    )

    # Chain 42161 (Arbitrum, cheaper): buy WETH with USDC
    pool_arb = XChainPool(
        chain_id=42161,
        venue_name="uniswap_v3",
        address="0xc6962004f452be9203591991d15f6b388e09e8d0",
        token0="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        token1="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
        pool_key=500,
        adapter_type="uniswap_v3",
        factory="0x1f98431c8ad98523631ae4a59f267346ea31f984",
    )

    # Chain 8453 (Base, dearer): sell WETH for USDC
    pool_base = XChainPool(
        chain_id=8453,
        venue_name="uniswap_v3",
        address="0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce",
        token0="0x4200000000000000000000000000000000000006",
        token1="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        pool_key=100,
        adapter_type="uniswap_v3",
        factory="0x33128a8fc17869897dce68ed026d694621f6fdfd",
    )

    class MockArbRpc:
        def _call(self, method: str, params: list):
            if method == "eth_getBlockByNumber":
                # Arbitrum base fee = 0.1 gwei = 100,000,000 wei
                return {"number": params[0], "baseFeePerGas": "0x5f5e100"}
            raise ValueError(f"unknown method {method}")

        def call(self, to: str, data: str, block: int | str):
            # Arbitrum buy quote: 10,000 USDC -> 3.846153846153846153 WETH
            # 10x gives 38.4 WETH
            amt_in = int(data[10 + 128 : 10 + 192], 16)
            if amt_in < 50_000 * 10**6:
                out = 3846153846153846153
            else:
                out = 38461538461538461530
            gas = 130_000
            res = hex(out)[2:].rjust(64, "0") + "0" * 128 + hex(gas)[2:].rjust(64, "0")
            return "0x" + res

    class MockBaseRpc:
        def _call(self, method: str, params: list):
            if method == "eth_getBlockByNumber":
                # Base base fee = 0.05 gwei = 50,000,000 wei
                return {"number": params[0], "baseFeePerGas": "0x2faf080"}
            raise ValueError(f"unknown method {method}")

        def call(self, to: str, data: str, block: int | str):
            if to.lower() == "0x420000000000000000000000000000000000000f":
                # Base L1 fee: 2,880,749,262 wei
                return "0x" + hex(2_880_749_262)[2:].rjust(64, "0")

            # Base sell quote: sell 3.846153846153846153 WETH -> 10,120.00 USDC
            # 10x gives 101,150.00 USDC
            amt_in = int(data[10 + 128 : 10 + 192], 16)
            if amt_in < 10 * 10**18:
                out = 10120 * 10**6
            else:
                out = 101150 * 10**6
            gas = 120_000
            res = hex(out)[2:].rjust(64, "0") + "0" * 128 + hex(gas)[2:].rjust(64, "0")
            return "0x" + res

    opp = evaluate_cross_chain_gap(
        moment=1700000000,
        chain_buy=42161,
        chain_sell=8453,
        block_buy=1000,
        block_sell=2000,
        rpc_buy=MockArbRpc(),
        rpc_sell=MockBaseRpc(),
        pools_buy=[pool_arb],
        pools_sell=[pool_base],
        size_usd=Decimal("10000"),
        mid_price_buy=Decimal("2600.00"),
        mid_price_sell=Decimal("2631.20"),
        rebalance_pct=Decimal("0.0005"),
        rebalance_fixed_usd=Decimal("1.00"),
    )

    assert isinstance(opp, XChainOpportunity)
    assert opp.moment == 1700000000
    assert opp.chain_buy == 42161
    assert opp.chain_sell == 8453
    assert opp.size_usd == Decimal("10000")
    # Gross profit: 10,120.00 - 10,000.00 = 120.00 USD
    assert opp.gross_usd == Decimal("120.00")
    # Gap: 120 / 10000 = 0.012 = 1.2%
    assert opp.gap_pct == Decimal("0.012")

    # Gas:
    # Buy (Arbitrum): (130k + 100k) * 100,000,000 wei = 2.3e13 wei = 0.000023 ETH * 2600 = $0.0598
    # Sell (Base): (120k + 100k) * 50,000,000 wei = 1.1e13 wei + 2,880,749,262 wei = 13,880,749,262 wei
    #   ETH = 0.000013880749262 * 2631.20 = $0.0365230224581744
    # Total gas: ~ $0.0963
    assert abs(opp.gas_usd - Decimal("0.0963")) < Decimal("0.01")

    # Rebalance: 10,000 * 0.0005 + 1.00 = 5.00 + 1.00 = 6.00 USD
    assert opp.rebalance_usd == Decimal("6.00")

    # Net: 120.00 - gas_usd - 6.00 ~ 113.90 USD
    assert opp.net_usd > Decimal("110.00")
    assert opp.is_opportunity is True


