"""Command-line interface for mev-scout."""

import argparse
from decimal import Decimal
import os
import sys
import time
import httpx

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.explorer import (
    BlockscoutClient,
    EtherscanClient,
    ExplorerError,
    create_log_source,
    redact,
)
from mev_scout.arb_census import (
    detect_arbitrages,
    generate_arb_census_report,
    generate_arb_csv,
    validate_arbitrages,
    value_arbitrages,
)
from mev_scout.arb_fetch import fetch_swaps
from mev_scout.arb_sample import generate_arb_sample_report, run_arb_sampling
from mev_scout.dex import discover_pools
from mev_scout.fetch import fetch
from mev_scout.report import CoverageError, generate_csv, generate_report
from mev_scout.rpc import RpcClient, RpcError, _redact_url
from mev_scout.store import Store
from mev_scout.validate import validate_chain
from mev_scout.value import DEFAULT_FLASH_FEE, DEFAULT_SWAP_COST, value_events



def _max_cps() -> float:
    """Client-side RPC pacing; 10 calls/s stays under Alchemy's free-tier compute-unit rate."""
    return float(os.environ.get("MEVSCOUT_MAX_CPS", "10"))

def _clean_error(err: Exception) -> str:
    msg = redact(str(err))
    for k, v in os.environ.items():
        if k.startswith("MEVSCOUT_RPC_") and v:
            msg = msg.replace(v, _redact_url(v))
    return msg


def fetch_cmd(chain_id: int, days: int, db_path: str) -> None:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain: {chain_id}")

    chain = CHAINS[chain_id]
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if chain.log_source == "etherscan" and not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        with httpx.Client(timeout=30.0) as http:
            explorer = create_log_source(chain=chain, http=http, api_key=api_key)
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            fetch(chain_id=chain_id, days=days, explorer=explorer, rpc=rpc, store=store)
    finally:
        store.close()


def value_cmd(
    chain_id: int,
    db_path: str,
    swap_cost: Decimal = DEFAULT_SWAP_COST,
    flash_fee: Decimal = DEFAULT_FLASH_FEE,
) -> None:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain: {chain_id}")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        if store.last_fetched_block(chain_id) is None:
            raise CoverageError(f"no fetched data for chain {chain_id}; run fetch first")
        events = store.get_liquidations(chain_id)
        with httpx.Client(timeout=30.0) as http:
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            valued = value_events(
                events=events,
                chain_id=chain_id,
                rpc=rpc,
                store=store,
                swap_cost=swap_cost,
                flash_fee=flash_fee,
            )
        unpriced = sum(1 for v in valued if v.unpriced)
        print(f"Valued {len(valued)} events on chain {chain_id} ({unpriced} unpriced)")
    finally:
        store.close()


def arb_pools_cmd(chain_id: int, db_path: str = "data/scout.db") -> None:
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage census currently only supports Arbitrum (42161), got {chain_id}")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        with httpx.Client(timeout=30.0) as http:
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            pools = discover_pools(chain_id=chain_id, rpc=rpc)
            store.insert_pools(pools)
            print(f"Discovered {len(pools)} pools on chain {chain_id}:")
            for p in pools:
                print(f"  [{p.dex}] {p.address}: token0={p.token0} token1={p.token1} fee={p.fee}")
    finally:
        store.close()


def arb_fetch_cmd(chain_id: int, days: int = 90, db_path: str = "data/scout.db") -> None:
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage census currently only supports Arbitrum (42161), got {chain_id}")

    chain = CHAINS[chain_id]
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        with httpx.Client(timeout=30.0) as http:
            explorer = create_log_source(chain=chain, http=http, api_key=api_key)
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            fetch_swaps(chain_id=chain_id, days=days, explorer=explorer, rpc=rpc, store=store)
            print(f"Fetched swaps for chain {chain_id} over {days} days")
    finally:
        store.close()


def arb_census_cmd(
    chain_id: int,
    days: int,
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    db_path: str = "data/scout.db",
    as_json: bool = False,
    csv_path: str | None = None,
) -> str:
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage census currently only supports Arbitrum (42161), got {chain_id}")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        pools_list = store.get_pools(chain_id)
        if not pools_list:
            raise CoverageError(f"no pools found for chain {chain_id}; run arb-pools first")
        pools = {p.address.lower(): p for p in pools_list}

        if store.conn.execute("SELECT 1 FROM swaps WHERE chain_id = ? LIMIT 1", (chain_id,)).fetchone() is None:
            raise CoverageError(f"no swaps found for chain {chain_id}; run arb-fetch first")
        # Only transactions touching >= 2 pools can be arbitrage; loading all swaps
        # (9.5M on Arbitrum for 90 days) does not fit in memory.
        swaps = store.get_multi_pool_swaps(chain_id)
        raw_arbs = detect_arbitrages(swaps, pools=pools)

        with httpx.Client(timeout=30.0) as http:
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            valued_arbs = value_arbitrages(raw_arbs, rpc=rpc, pools=pools, store=store, chain_id=chain_id)
            val_res = validate_arbitrages(valued_arbs, rpc=rpc)

        now_ts = int(time.time())
        report = generate_arb_census_report(
            chain_id=chain_id,
            arbitrages=valued_arbs,
            eurusd=eurusd,
            threshold_eur=threshold_eur,
            days=days,
            end_ts=now_ts,
            validation=val_res,
        )

        if csv_path:
            csv_content = generate_arb_csv(valued_arbs, eurusd=eurusd)
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(csv_content)

        return report.to_json() if as_json else report.to_text()
    finally:
        store.close()


def arb_sample_cmd(
    chain_id: int,
    days: int = 30,
    every_min: int = 10,
    dense: list[str] | None = None,
    db_path: str = "data/scout.db",
) -> None:
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage sampling currently only supports Arbitrum (42161), got {chain_id}")

    chain = CHAINS[chain_id]
    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        with httpx.Client(timeout=30.0) as http:
            explorer = create_log_source(chain=chain, http=http, api_key=api_key)
            rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())
            meta = run_arb_sampling(
                chain_id=chain_id,
                days=days,
                every_min=every_min,
                dense_ranges=dense,
                rpc=rpc,
                explorer=explorer,
                store=store,
            )
            print(
                f"Completed sampling {meta.total_sampled_blocks} blocks on chain {chain_id} "
                f"({meta.skipped_prefilter_pairs} pairs skipped by prefilter, {meta.reverted_quotes} reverted quotes)"
            )
    finally:
        store.close()


def arb_report_cmd(
    chain_id: int,
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    days: int = 30,
    db_path: str = "data/scout.db",
    as_json: bool = False,
) -> str:
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage report currently only supports Arbitrum (42161), got {chain_id}")

    store = Store(db_path)
    try:
        meta = store.get_arb_sample_meta(chain_id)
        if not meta:
            raise CoverageError(f"no sampled arbitrage data for chain {chain_id}; run arb-sample first")
        results = store.get_arb_samples(chain_id)
        report = generate_arb_sample_report(
            meta=meta,
            results=results,
            eurusd=eurusd,
            threshold_eur=threshold_eur,
            days=days,
        )
        return report.to_json() if as_json else report.to_text()
    finally:
        store.close()


def xchain_sample_cmd(
    days: int,
    every_min: int,
    dense: list[str] | None,
    rebalance_pct: Decimal,
    rebalance_fixed_usd: Decimal,
    db_path: str,
) -> None:
    from mev_scout.xchain import run_xchain_sample

    rpcs = {}
    with httpx.Client(timeout=30.0) as http:
        for cid in (42161, 8453, 10):
            rpc_env = f"MEVSCOUT_RPC_{cid}"
            rpc_url = os.environ.get(rpc_env)
            if not rpc_url:
                raise ConfigError(f"{rpc_env} environment variable is required")
            rpcs[cid] = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())

        store = Store(db_path)
        try:
            run_xchain_sample(
                rpcs=rpcs,
                store=store,
                days=days,
                every_min=every_min,
                dense_ranges=dense,
                rebalance_pct=rebalance_pct,
                rebalance_fixed_usd=rebalance_fixed_usd,
                progress_stream=sys.stderr,
            )
        finally:
            store.close()


def xchain_report_cmd(
    eurusd: Decimal,
    threshold_eur: Decimal,
    days: int,
    db_path: str,
    as_json: bool = False,
    csv_path: str | None = None,
) -> str:
    from mev_scout.xchain import generate_xchain_report

    store = Store(db_path)
    try:
        opps = store.get_xchain_opportunities()
        completed = store.get_completed_xchain_moments()
        total_moments = len(completed) if completed else (len({o.moment for o in opps}) if opps else 0)
        from_ts = min((o.moment for o in opps), default=0)
        to_ts = max((o.moment for o in opps), default=0)
        rep = generate_xchain_report(
            opportunities=opps,
            total_moments=total_moments,
            days=days,
            eurusd=eurusd,
            threshold_eur=threshold_eur,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        if csv_path:
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(rep.to_csv())
        if as_json:
            return rep.to_json()
        return rep.to_text()
    finally:
        store.close()


def report_cmd(
    chain_ids: list[int],
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    days: int = 90,
    db_path: str = "data/scout.db",
    as_json: bool = False,
    csv_path: str | None = None,
    swap_cost: Decimal = DEFAULT_SWAP_COST,
    flash_fee: Decimal = DEFAULT_FLASH_FEE,
) -> str:
    for cid in chain_ids:
        if cid not in CHAINS:
            raise ConfigError(f"Unsupported chain: {cid}")

    api_key = os.environ.get("ETHERSCAN_API_KEY")
    needs_etherscan = any(CHAINS[cid].log_source == "etherscan" for cid in chain_ids)
    if needs_etherscan and not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    store = Store(db_path)
    try:
        from_blocks: dict[int, int] = {}
        to_blocks: dict[int, int] = {}
        valued_events: dict[int, list] = {}
        validation_results: dict[int, Any] = {}

        now_ts = int(time.time())
        start_ts = now_ts - (days * 86400)

        with httpx.Client(timeout=30.0) as http:
            for cid in chain_ids:
                chain = CHAINS[cid]
                explorer = create_log_source(chain=chain, http=http, api_key=api_key)

                rpc_env = f"MEVSCOUT_RPC_{cid}"
                rpc_url = os.environ.get(rpc_env)
                if not rpc_url:
                    raise ConfigError(f"{rpc_env} environment variable is required")

                rpc = RpcClient(url=rpc_url, http=http, max_calls_per_sec=_max_cps())

                fb = explorer.block_by_time(cid, start_ts)
                # The window ends at the last fetched block, not the live tip: on fast
                # chains the tip moves between fetch and report, which is not a gap.
                last = store.last_fetched_block(cid)
                if last is None:
                    raise CoverageError(f"no fetched data for chain {cid}; run fetch first")
                tb = min(rpc.block_number(), last)
                if fb > tb:
                    fb = tb

                from_blocks[cid] = fb
                to_blocks[cid] = tb

                # Check coverage first
                gaps = store.covered(cid, fb, tb)
                if gaps:
                    raise CoverageError(
                        f"Coverage gap(s) detected for chain {cid} across window [{fb}, {tb}]: {gaps}"
                    )

                events = store.get_liquidations(cid, from_block=fb, to_block=tb)
                valued = value_events(
                    events=events,
                    chain_id=cid,
                    rpc=rpc,
                    store=store,
                    swap_cost=swap_cost,
                    flash_fee=flash_fee,
                )
                valued_events[cid] = valued

                val_res = validate_chain(events=events, chain_id=cid, rpc=rpc)
                validation_results[cid] = val_res

        report = generate_report(
            chain_ids=chain_ids,
            from_blocks=from_blocks,
            to_blocks=to_blocks,
            store=store,
            valued_events=valued_events,
            validation_results=validation_results,
            eurusd=eurusd,
            end_ts=now_ts,
            threshold_eur=threshold_eur,
            days=days,
        )

        if csv_path:
            all_valued = []
            for cid in chain_ids:
                all_valued.extend(valued_events.get(cid, []))
            csv_content = generate_csv(all_valued, eurusd=eurusd)
            with open(csv_path, "w", encoding="utf-8") as f:
                f.write(csv_content)

        return report.to_json() if as_json else report.to_text()

    finally:
        store.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="mev-scout", description="Aave V3 liquidation opportunity census")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Fetch
    p_fetch = subparsers.add_parser("fetch", help="Fetch liquidation events for a chain")
    p_fetch.add_argument("--chain", type=int, required=True, help="Chain ID (e.g. 42161 or 146)")
    p_fetch.add_argument("--days", type=int, default=90, help="Days of history to fetch (default: 90)")
    p_fetch.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")

    # Value
    p_value = subparsers.add_parser("value", help="Value stored liquidation events in USD")
    p_value.add_argument("--chain", type=int, required=True, help="Chain ID")
    p_value.add_argument("--db", default="data/scout.db", help="SQLite DB path")
    p_value.add_argument("--swap-cost", type=Decimal, default=DEFAULT_SWAP_COST, help="Swap cost fraction (default: 0.003)")
    p_value.add_argument("--flash-fee", type=Decimal, default=DEFAULT_FLASH_FEE, help="Flash-loan fee fraction (default: 0.0005)")

    # Report
    p_report = subparsers.add_parser("report", help="Generate census report and verdict")
    p_report.add_argument("--chain", type=int, action="append", required=True, help="Chain ID (can repeat)")
    p_report.add_argument("--eurusd", type=Decimal, required=True, help="EUR/USD exchange rate")
    p_report.add_argument("--threshold-eur", type=Decimal, default=Decimal("300"), help="Monthly threshold in EUR (default: 300)")
    p_report.add_argument("--days", type=int, default=90, help="Window length in days (default: 90)")
    p_report.add_argument("--db", default="data/scout.db", help="SQLite DB path")
    p_report.add_argument("--json", action="store_true", help="Output JSON format")
    p_report.add_argument("--csv", help="Optional path to output events CSV")
    p_report.add_argument("--swap-cost", type=Decimal, default=DEFAULT_SWAP_COST, help="Swap cost fraction (default: 0.003)")
    p_report.add_argument("--flash-fee", type=Decimal, default=DEFAULT_FLASH_FEE, help="Flash-loan fee fraction (default: 0.0005)")

    # arb-pools
    p_arb_pools = subparsers.add_parser("arb-pools", help="Discover and store DEX pools for arbitrage")
    p_arb_pools.add_argument("--chain", type=int, required=True, help="Chain ID (42161)")
    p_arb_pools.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")

    # arb-fetch
    p_arb_fetch = subparsers.add_parser("arb-fetch", help="Fetch swap logs for discovered DEX pools")
    p_arb_fetch.add_argument("--chain", type=int, required=True, help="Chain ID (42161)")
    p_arb_fetch.add_argument("--days", type=int, default=90, help="Days of history to fetch (default: 90)")
    p_arb_fetch.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")

    # arb-census
    p_arb_census = subparsers.add_parser("arb-census", help="Analyze and report past atomic DEX arbitrage")
    p_arb_census.add_argument("--chain", type=int, required=True, help="Chain ID (42161)")
    p_arb_census.add_argument("--days", type=int, default=90, help="Days of history to analyze (default: 90)")
    p_arb_census.add_argument("--eurusd", type=Decimal, required=True, help="EUR/USD exchange rate")
    p_arb_census.add_argument("--threshold-eur", type=Decimal, default=Decimal("300"), help="Monthly threshold in EUR (default: 300)")
    p_arb_census.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")
    p_arb_census.add_argument("--json", action="store_true", help="Output JSON format")
    p_arb_census.add_argument("--csv", help="Optional path to output events CSV")

    # arb-sample
    p_arb_sample = subparsers.add_parser("arb-sample", help="Sample past blocks for leftover opportunities")
    p_arb_sample.add_argument("--chain", type=int, required=True, help="Chain ID (42161)")
    p_arb_sample.add_argument("--days", type=int, default=30, help="Window length in days (default: 30)")
    p_arb_sample.add_argument("--every-min", type=int, default=10, help="Sampling interval in minutes (default: 10)")
    p_arb_sample.add_argument("--dense", action="append", help="Dense block range FROM:TO (can repeat)")
    p_arb_sample.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")

    # arb-report
    p_arb_report = subparsers.add_parser("arb-report", help="Report leftover opportunities and upper bound verdict")
    p_arb_report.add_argument("--chain", type=int, required=True, help="Chain ID (42161)")
    p_arb_report.add_argument("--eurusd", type=Decimal, required=True, help="EUR/USD exchange rate")
    p_arb_report.add_argument("--threshold-eur", type=Decimal, default=Decimal("300"), help="Monthly threshold in EUR (default: 300)")
    p_arb_report.add_argument("--days", type=int, default=30, help="Window length in days (default: 30)")
    p_arb_report.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")
    p_arb_report.add_argument("--json", action="store_true", help="Output JSON format")

    # xchain-sample
    p_xchain_sample = subparsers.add_parser("xchain-sample", help="Sample cross-chain inventory arbitrage opportunities")
    p_xchain_sample.add_argument("--days", type=int, default=30, help="Window length in days (default: 30)")
    p_xchain_sample.add_argument("--every-min", type=int, default=5, help="Sampling interval in minutes (default: 5)")
    p_xchain_sample.add_argument("--dense", action="append", help="Dense timestamp range FROM:TO (can repeat)")
    p_xchain_sample.add_argument("--rebalance-pct", type=Decimal, default=Decimal("0.0005"), help="Rebalance cost pct (default: 0.0005)")
    p_xchain_sample.add_argument("--rebalance-fixed-usd", type=Decimal, default=Decimal("1.00"), help="Rebalance fixed cost USD (default: 1.00)")
    p_xchain_sample.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")

    # xchain-report
    p_xchain_report = subparsers.add_parser("xchain-report", help="Report cross-chain inventory arbitrage opportunities")
    p_xchain_report.add_argument("--eurusd", type=Decimal, required=True, help="EUR/USD exchange rate")
    p_xchain_report.add_argument("--threshold-eur", type=Decimal, default=Decimal("300"), help="Monthly threshold in EUR (default: 300)")
    p_xchain_report.add_argument("--days", type=int, default=30, help="Window length in days (default: 30)")
    p_xchain_report.add_argument("--db", default="data/scout.db", help="SQLite DB path (default: data/scout.db)")
    p_xchain_report.add_argument("--json", action="store_true", help="Output JSON format")
    p_xchain_report.add_argument("--csv", help="Optional path to output opportunities CSV")

    args = parser.parse_args(argv)

    try:
        if args.command == "fetch":
            fetch_cmd(chain_id=args.chain, days=args.days, db_path=args.db)
            sys.exit(0)

        elif args.command == "value":
            value_cmd(
                chain_id=args.chain,
                db_path=args.db,
                swap_cost=args.swap_cost,
                flash_fee=args.flash_fee,
            )
            sys.exit(0)

        elif args.command == "report":
            output = report_cmd(
                chain_ids=args.chain,
                eurusd=args.eurusd,
                threshold_eur=args.threshold_eur,
                days=args.days,
                db_path=args.db,
                as_json=args.json,
                csv_path=args.csv,
                swap_cost=args.swap_cost,
                flash_fee=args.flash_fee,
            )
            print(output)
            sys.exit(0)

        elif args.command == "arb-pools":
            arb_pools_cmd(chain_id=args.chain, db_path=args.db)
            sys.exit(0)

        elif args.command == "arb-fetch":
            arb_fetch_cmd(chain_id=args.chain, days=args.days, db_path=args.db)
            sys.exit(0)

        elif args.command == "arb-census":
            output = arb_census_cmd(
                chain_id=args.chain,
                days=args.days,
                eurusd=args.eurusd,
                threshold_eur=args.threshold_eur,
                db_path=args.db,
                as_json=args.json,
                csv_path=args.csv,
            )
            print(output)
            sys.exit(0)

        elif args.command == "arb-sample":
            arb_sample_cmd(
                chain_id=args.chain,
                days=args.days,
                every_min=args.every_min,
                dense=args.dense,
                db_path=args.db,
            )
            sys.exit(0)

        elif args.command == "arb-report":
            output = arb_report_cmd(
                chain_id=args.chain,
                eurusd=args.eurusd,
                threshold_eur=args.threshold_eur,
                days=args.days,
                db_path=args.db,
                as_json=args.json,
            )
            print(output)
            sys.exit(0)

        elif args.command == "xchain-sample":
            xchain_sample_cmd(
                days=args.days,
                every_min=args.every_min,
                dense=args.dense,
                rebalance_pct=args.rebalance_pct,
                rebalance_fixed_usd=args.rebalance_fixed_usd,
                db_path=args.db,
            )
            sys.exit(0)

        elif args.command == "xchain-report":
            output = xchain_report_cmd(
                eurusd=args.eurusd,
                threshold_eur=args.threshold_eur,
                days=args.days,
                db_path=args.db,
                as_json=args.json,
                csv_path=args.csv,
            )
            print(output)
            sys.exit(0)

    except Exception as exc:
        clean_msg = _clean_error(exc)
        sys.stderr.write(f"Error: {clean_msg}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
