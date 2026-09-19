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
        if cached.startswith("REVERT:"):
            raise ContractCallError(cached.removeprefix("REVERT:"))
        return cached
    try:
        res = rpc.call(to=to, data=data, block=block)
    except ContractCallError as exc:
        store.set_call_cache(chain_id, to, data, block, f"REVERT:{exc}")
        raise
    store.set_call_cache(chain_id, to, data, block, res)
    return res


def get_cached_decimals(
    chain_id: int,
    token: str,
    block: int | str,
    rpc: RpcClient,
    store: Store,
) -> int:
    """Read decimals for a token on a chain, ignoring block if already cached."""
    dec = store.get_decimals(chain_id, token)
    if dec is not None:
        return dec
    raw = cached_call(chain_id, token, DECIMALS_SELECTOR, block, rpc, store)
    dec = int(raw, 16)
    store.set_decimals(chain_id, token, dec)
    return dec


def _prefetch_events(
    events: list[Liquidation],
    chain_id: int,
    pool: str,
    wrapped_native: str,
    rpc: RpcClient,
    store: Store,
) -> None:
    """Pre-fetch oracle data, asset prices, and decimals using JSON-RPC batching."""
    if not events:
        return

    # Batch only with a client whose class really implements it; anything else
    # (e.g. a minimal test double) goes through rpc.call one call at a time.
    batch_fn = getattr(rpc, "batch_call", None) if hasattr(type(rpc), "batch_call") else None
    if not callable(batch_fn):
        def mock_batch(calls):
            results = []
            for c in calls:
                try:
                    res = rpc.call(c[0], c[1], c[2])
                    results.append(res)
                except ContractCallError as exc:
                    results.append(exc)
                except Exception as exc:
                    results.append(exc)
            return results
        batch_fn = mock_batch

    last_block = max(e.block for e in events)

    # 1. Pre-fetch decimals for missing tokens once at last_block
    all_tokens = {e.collateral.lower() for e in events} | {e.debt.lower() for e in events}
    missing_tokens = [t for t in all_tokens if store.get_decimals(chain_id, t) is None]
    if missing_tokens:
        dec_calls = [(t, DECIMALS_SELECTOR, last_block) for t in missing_tokens]
        dec_results = batch_fn(dec_calls)
        for t, res in zip(missing_tokens, dec_results):
            if isinstance(res, str) and res not in ("", "0x"):
                try:
                    store.set_decimals(chain_id, t, int(res, 16))
                except (ValueError, TypeError):
                    pass
            elif isinstance(res, ContractCallError):
                store.set_call_cache(chain_id, t, DECIMALS_SELECTOR, last_block, f"REVERT:{res}")

    # 2. Pre-fetch pool.ADDRESSES_PROVIDER() for unique blocks
    unique_blocks = sorted({e.block for e in events})
    missing_provider_blocks = [
        b for b in unique_blocks
        if store.get_call_cache(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b) is None
    ]
    if missing_provider_blocks:
        prov_calls = [(pool, ADDRESSES_PROVIDER_SELECTOR, b) for b in missing_provider_blocks]
        prov_results = batch_fn(prov_calls)
        for b, res in zip(missing_provider_blocks, prov_results):
            if isinstance(res, ContractCallError):
                store.set_call_cache(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b, f"REVERT:{res}")
            elif isinstance(res, str):
                store.set_call_cache(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b, res)

    # 3. Pre-fetch provider.getPriceOracle() for unique blocks
    missing_oracle_blocks = []
    oracle_calls = []
    for b in unique_blocks:
        prov_data = store.get_call_cache(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b)
        if prov_data and not prov_data.startswith("REVERT:"):
            prov = "0x" + prov_data[-40:].lower()
            if store.get_call_cache(chain_id, prov, PRICE_ORACLE_SELECTOR, b) is None:
                missing_oracle_blocks.append((b, prov))
                oracle_calls.append((prov, PRICE_ORACLE_SELECTOR, b))
    if oracle_calls:
        oracle_results = batch_fn(oracle_calls)
        for (b, prov), res in zip(missing_oracle_blocks, oracle_results):
            if isinstance(res, ContractCallError):
                store.set_call_cache(chain_id, prov, PRICE_ORACLE_SELECTOR, b, f"REVERT:{res}")
            elif isinstance(res, str):
                store.set_call_cache(chain_id, prov, PRICE_ORACLE_SELECTOR, b, res)

    # 4. Pre-fetch oracle calls: BASE_CURRENCY_UNIT and getAssetPrice
    oracle_query_calls = []
    seen_queries = set()

    for e in events:
        b = e.block
        prov_data = store.get_call_cache(chain_id, pool, ADDRESSES_PROVIDER_SELECTOR, b)
        if not prov_data or prov_data.startswith("REVERT:"):
            continue
        prov = "0x" + prov_data[-40:].lower()
        oracle_data = store.get_call_cache(chain_id, prov, PRICE_ORACLE_SELECTOR, b)
        if not oracle_data or oracle_data.startswith("REVERT:"):
            continue
        oracle = "0x" + oracle_data[-40:].lower()

        # BASE_CURRENCY_UNIT
        base_key = (chain_id, oracle, BASE_CURRENCY_UNIT_SELECTOR.lower(), str(b).lower())
        if base_key not in seen_queries and store.get_call_cache(chain_id, oracle, BASE_CURRENCY_UNIT_SELECTOR, b) is None:
            seen_queries.add(base_key)
            oracle_query_calls.append((oracle, BASE_CURRENCY_UNIT_SELECTOR, b))

        # Wrapped native price
        native_data = GET_ASSET_PRICE_SELECTOR + "0" * 24 + wrapped_native.removeprefix("0x").lower()
        native_key = (chain_id, oracle, native_data.lower(), str(b).lower())
        if native_key not in seen_queries and store.get_call_cache(chain_id, oracle, native_data, b) is None:
            seen_queries.add(native_key)
            oracle_query_calls.append((oracle, native_data, b))

        # Collateral price
        c_data = GET_ASSET_PRICE_SELECTOR + "0" * 24 + e.collateral.removeprefix("0x").lower()
        c_key = (chain_id, oracle, c_data.lower(), str(b).lower())
        if c_key not in seen_queries and store.get_call_cache(chain_id, oracle, c_data, b) is None:
            seen_queries.add(c_key)
            oracle_query_calls.append((oracle, c_data, b))

        # Debt price
        d_data = GET_ASSET_PRICE_SELECTOR + "0" * 24 + e.debt.removeprefix("0x").lower()
        d_key = (chain_id, oracle, d_data.lower(), str(b).lower())
        if d_key not in seen_queries and store.get_call_cache(chain_id, oracle, d_data, b) is None:
            seen_queries.add(d_key)
            oracle_query_calls.append((oracle, d_data, b))

    if oracle_query_calls:
        query_results = batch_fn(oracle_query_calls)
        for (to, data, b), res in zip(oracle_query_calls, query_results):
            if isinstance(res, ContractCallError):
                store.set_call_cache(chain_id, to, data, b, f"REVERT:{res}")
            elif isinstance(res, str):
                store.set_call_cache(chain_id, to, data, b, res)


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

        # 2. Token decimals (ignoring block by caching per chain, token)
        dec_c = get_cached_decimals(chain_id, event.collateral, b, rpc, store)
        dec_d = get_cached_decimals(chain_id, event.debt, b, rpc, store)

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

    # Pre-fetch needed data in JSON-RPC batches
    _prefetch_events(
        events=events,
        chain_id=chain_id,
        pool=chain.pool,
        wrapped_native=chain.wrapped_native,
        rpc=rpc,
        store=store,
    )

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
