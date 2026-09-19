"""Valuation of liquidation events in USD using on-chain oracles."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.decode import Liquidation
from mev_scout.rpc import ContractCallError, RpcClient
from mev_scout.store import Store

ADDRESSES_PROVIDER_SELECTOR = "0x0542975c"
PRICE_ORACLE_SELECTOR = "0xfca513a8"
BASE_CURRENCY_UNIT_SELECTOR = "0x8c89b64f"
GET_ASSET_PRICE_SELECTOR = "0xb3596f07"
DECIMALS_SELECTOR = "0x313ce567"

DEFAULT_SWAP_COST = Decimal("0.003")
DEFAULT_FLASH_FEE = Decimal("0.0005")


@dataclass(frozen=True)
class ValuedLiquidation:
    event: Liquidation
    unpriced: bool = False
    collateral_usd: Decimal | None = None
    debt_usd: Decimal | None = None
    gross_usd: Decimal | None = None
    gas_usd: Decimal | None = None
    swap_cost: Decimal | None = None
    flash_fee: Decimal | None = None
    net_usd: Decimal | None = None
    collateral_price_usd: Decimal | None = None
    debt_price_usd: Decimal | None = None
    native_price_usd: Decimal | None = None


def cached_call(
    chain_id: int,
    to: str,
    data: str,
    block: int | str,
    rpc: RpcClient,
    store: Store,
) -> str:
    cached = store.get_call_cache(chain_id, to, data, block)
    if cached is not None:
        return cached
    res = rpc.call(to=to, data=data, block=block)
    store.set_call_cache(chain_id, to, data, block, res)
    return res


def value_liquidation(
    event: Liquidation,
    tx_event_count: int,
    pool: str,
    wrapped_native: str,
    rpc: RpcClient,
    store: Store,
    swap_cost: Decimal = DEFAULT_SWAP_COST,
    flash_fee: Decimal = DEFAULT_FLASH_FEE,
) -> ValuedLiquidation:
    b = event.block
    chain_id = event.chain_id

    try:
        # 1. Oracle resolution
        provider_data = cached_call(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b, rpc, store)
        provider = "0x" + provider_data[-40:].lower()

        oracle_data = cached_call(chain_id, provider, PRICE_ORACLE_SELECTOR, b, rpc, store)
        oracle = "0x" + oracle_data[-40:].lower()

        base_unit_data = cached_call(chain_id, oracle, BASE_CURRENCY_UNIT_SELECTOR, b, rpc, store)
        base_unit = int(base_unit_data, 16)
        if base_unit == 0:
            raise ContractCallError(f"BASE_CURRENCY_UNIT returned 0 on oracle {oracle}")

        # 2. Token decimals
        dec_c_data = cached_call(chain_id, event.collateral, DECIMALS_SELECTOR, b, rpc, store)
        dec_c = int(dec_c_data, 16)

        dec_d_data = cached_call(chain_id, event.debt, DECIMALS_SELECTOR, b, rpc, store)
        dec_d = int(dec_d_data, 16)

        # 3. Asset prices
        c_price_data = cached_call(
            chain_id,
            oracle,
            GET_ASSET_PRICE_SELECTOR + "0" * 24 + event.collateral.removeprefix("0x"),
            b,
            rpc,
            store,
        )
        price_c = int(c_price_data, 16)

        d_price_data = cached_call(
            chain_id,
            oracle,
            GET_ASSET_PRICE_SELECTOR + "0" * 24 + event.debt.removeprefix("0x"),
            b,
            rpc,
            store,
        )
        price_d = int(d_price_data, 16)

        native_price_data = cached_call(
            chain_id,
            oracle,
            GET_ASSET_PRICE_SELECTOR + "0" * 24 + wrapped_native.removeprefix("0x"),
            b,
            rpc,
            store,
        )
        price_native = int(native_price_data, 16)

        # A zero price means the oracle has no price for that asset. Valuing the
        # event at $0 would silently drop it from the totals, so it is unpriced.
        if 0 in (price_c, price_d, price_native):
            raise ContractCallError(f"oracle {oracle} returned price 0 at block {b}")

    except ContractCallError:
        return ValuedLiquidation(event=event, unpriced=True)

    D = Decimal
    base_unit_dec = D(base_unit)

    price_c_usd = D(price_c) / base_unit_dec
    price_d_usd = D(price_d) / base_unit_dec
    price_native_usd = D(price_native) / base_unit_dec

    collateral_usd = (D(event.collateral_amount) / D(10**dec_c)) * price_c_usd
    debt_usd = (D(event.debt_to_cover) / D(10**dec_d)) * price_d_usd
    gross_usd = collateral_usd - debt_usd

    # Split gas equally across events sharing the same tx_hash
    k = max(1, tx_event_count)
    gas_usd = (D(event.gas_used) / D(k)) * (D(event.gas_price) / D(10**18)) * price_native_usd

    swap_cost_usd = Decimal(str(swap_cost)) * collateral_usd
    flash_fee_usd = Decimal(str(flash_fee)) * debt_usd
    net_usd = gross_usd - gas_usd - swap_cost_usd - flash_fee_usd

    return ValuedLiquidation(
        event=event,
        unpriced=False,
        collateral_usd=collateral_usd,
        debt_usd=debt_usd,
        gross_usd=gross_usd,
        gas_usd=gas_usd,
        swap_cost=swap_cost_usd,
        flash_fee=flash_fee_usd,
        net_usd=net_usd,
        collateral_price_usd=price_c_usd,
        debt_price_usd=price_d_usd,
        native_price_usd=price_native_usd,
    )


def value_events(
    events: list[Liquidation],
    chain_id: int,
    rpc: RpcClient,
    store: Store,
    swap_cost: Decimal = DEFAULT_SWAP_COST,
    flash_fee: Decimal = DEFAULT_FLASH_FEE,
) -> list[ValuedLiquidation]:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain id: {chain_id}")

    chain = CHAINS[chain_id]
    tx_counts = Counter(e.tx_hash for e in events)

    return [
        value_liquidation(
            event=e,
            tx_event_count=tx_counts[e.tx_hash],
            pool=chain.pool,
            wrapped_native=chain.wrapped_native,
            rpc=rpc,
            store=store,
            swap_cost=swap_cost,
            flash_fee=flash_fee,
        )
        for e in events
    ]
