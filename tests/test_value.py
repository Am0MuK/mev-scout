from decimal import Decimal
from unittest.mock import MagicMock
import pytest

from mev_scout.chains import CHAINS
from mev_scout.decode import Liquidation
from mev_scout.rpc import ContractCallError, RpcError
from mev_scout.store import Store
from mev_scout.value import ValuedLiquidation, value_events, value_liquidation


def _make_event(tx_hash="0x1", block=100, log_index=0, gas_used=100_000, gas_price=10**9):
    return Liquidation(
        chain_id=146,
        block=block,
        timestamp=1700000000,
        tx_hash=tx_hash,
        log_index=log_index,
        collateral="0xc011",
        debt="0xdeb7",
        user="0xuser",
        debt_to_cover=1000 * 10**6,  # 1000 USDC (6 dec)
        collateral_amount=1 * 10**18,  # 1 WETH (18 dec)
        liquidator="0xliq",
        receive_atoken=False,
        gas_used=gas_used,
        gas_price=gas_price,
    )


def _setup_mock_rpc(collateral_price=1100 * 10**8, debt_price=1 * 10**8, native_price=2 * 10**8):
    rpc = MagicMock()

    def mock_call(to, data, block):
        # Pool.ADDRESSES_PROVIDER -> provider
        if data == "0x0542975c":
            return "0x" + "0" * 24 + "1111111111111111111111111111111111111111"
        # provider.getPriceOracle -> oracle
        if data == "0xfca513a8":
            return "0x" + "0" * 24 + "2222222222222222222222222222222222222222"
        # oracle.BASE_CURRENCY_UNIT -> 1e8
        if data == "0x8c89b64f":
            return hex(10**8)
        # Token decimals:
        if data == "0x313ce567":
            if "c011" in to.lower():
                return hex(18)
            if "deb7" in to.lower():
                return hex(6)
            return hex(18)
        # oracle.getAssetPrice(asset):
        if data.startswith("0xb3596f07"):
            if "c011" in data.lower():
                return hex(collateral_price)
            if "deb7" in data.lower():
                return hex(debt_price)
            # wrapped native
            return hex(native_price)
        raise ValueError(f"Unexpected call: to={to}, data={data}")

    rpc.call.side_effect = mock_call
    return rpc


def test_value_liquidation_single_event():
    store = Store(":memory:")
    rpc = _setup_mock_rpc(collateral_price=1100 * 10**8, debt_price=1 * 10**8, native_price=2 * 10**8)
    event = _make_event(gas_used=100_000, gas_price=10**9)

    chain = CHAINS[146]
    res = value_liquidation(
        event=event,
        tx_event_count=1,
        pool=chain.pool,
        wrapped_native=chain.wrapped_native,
        rpc=rpc,
        store=store,
    )

    assert not res.unpriced
    # collateral = 1 * 1100 = 1100 USD
    assert res.collateral_usd == Decimal("1100")
    # debt = 1000 * 1 = 1000 USD
    assert res.debt_usd == Decimal("1000")
    # gross = 1100 - 1000 = 100 USD
    assert res.gross_usd == Decimal("100")
    # gas = 100,000 * 10^9 / 10^18 * 2 = 0.0001 * 2 = 0.0002 USD
    assert res.gas_usd == Decimal("0.0002")
    # swap_cost = 1100 * 0.003 = 3.3 USD
    assert res.swap_cost == Decimal("3.3")
    # flash_fee = 1000 * 0.0005 = 0.5 USD
    assert res.flash_fee == Decimal("0.5")
    # net = 100 - 0.0002 - 3.3 - 0.5 = 96.1998 USD
    assert res.net_usd == Decimal("96.1998")


def test_gas_split_across_events_in_same_tx():
    store = Store(":memory:")
    rpc = _setup_mock_rpc()
    e1 = _make_event(tx_hash="0xshared", log_index=1, gas_used=200_000, gas_price=10**9)
    e2 = _make_event(tx_hash="0xshared", log_index=2, gas_used=200_000, gas_price=10**9)

    results = value_events([e1, e2], chain_id=146, rpc=rpc, store=store)
    assert len(results) == 2
    # gas_usd for 200,000 gas, 10^9 gas price, 2 USD native price = 0.0004 USD total.
    # Split equally between 2 events -> 0.0002 each.
    assert results[0].gas_usd == Decimal("0.0002")
    assert results[1].gas_usd == Decimal("0.0002")


def test_value_caching_in_store():
    store = Store(":memory:")
    rpc = _setup_mock_rpc()
    e1 = _make_event(tx_hash="0xtx1", block=100)
    e2 = _make_event(tx_hash="0xtx2", block=100)

    value_events([e1], chain_id=146, rpc=rpc, store=store)
    first_call_count = rpc.call.call_count

    # Second run at same block should use cached calls
    value_events([e2], chain_id=146, rpc=rpc, store=store)
    # No additional RPC calls because all oracle/decimal/price queries at block 100 are cached!
    assert rpc.call.call_count == first_call_count


def test_contract_call_error_marks_event_unpriced():
    store = Store(":memory:")
    rpc = MagicMock()
    rpc.call.side_effect = ContractCallError("execution reverted in oracle")
    event = _make_event()

    results = value_events([event], chain_id=146, rpc=rpc, store=store)
    assert len(results) == 1
    assert results[0].unpriced is True
    assert results[0].net_usd is None


def test_rpc_error_raises():
    store = Store(":memory:")
    rpc = MagicMock()
    rpc.call.side_effect = RpcError("network transport failure")
    event = _make_event()

    with pytest.raises(RpcError, match="network transport failure"):
        value_events([event], chain_id=146, rpc=rpc, store=store)


@pytest.mark.parametrize("which", ["collateral", "debt", "native"])
def test_zero_oracle_price_marks_event_unpriced_not_zero(which):
    # An oracle that answers 0 has no price for that asset; valuing the event at
    # $0 would silently erase it from the monthly net.
    prices = {"collateral_price": 1100 * 10**8, "debt_price": 10**8, "native_price": 2 * 10**8}
    prices[f"{which}_price"] = 0
    store = Store(":memory:")
    results = value_events([_make_event()], chain_id=146, rpc=_setup_mock_rpc(**prices), store=store)
    assert results[0].unpriced is True
    assert results[0].net_usd is None
