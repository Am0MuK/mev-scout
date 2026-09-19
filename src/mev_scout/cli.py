"""Command-line interface for mev-scout."""

import argparse
from decimal import Decimal
import os
import sys
import time
import httpx

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.explorer import EtherscanClient, ExplorerError, redact
from mev_scout.fetch import fetch
from mev_scout.report import CoverageError, generate_csv, generate_report
from mev_scout.rpc import RpcClient, RpcError, _redact_url
from mev_scout.store import Store
from mev_scout.validate import validate_chain
from mev_scout.value import DEFAULT_FLASH_FEE, DEFAULT_SWAP_COST, value_events


def _clean_error(err: Exception) -> str:
    msg = redact(str(err))
    for k, v in os.environ.items():
        if k.startswith("MEVSCOUT_RPC_") and v:
            msg = msg.replace(v, _redact_url(v))
    return msg


def fetch_cmd(chain_id: int, days: int, db_path: str) -> None:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain: {chain_id}")

    api_key = os.environ.get("ETHERSCAN_API_KEY")
    if not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    rpc_env = f"MEVSCOUT_RPC_{chain_id}"
    rpc_url = os.environ.get(rpc_env)
    if not rpc_url:
        raise ConfigError(f"{rpc_env} environment variable is required")

    store = Store(db_path)
    try:
        with httpx.Client() as http:
            explorer = EtherscanClient(api_key=api_key, http=http)
            rpc = RpcClient(url=rpc_url, http=http)
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
        events = store.get_liquidations(chain_id)
        with httpx.Client() as http:
            rpc = RpcClient(url=rpc_url, http=http)
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
    if not api_key:
        raise ConfigError("ETHERSCAN_API_KEY environment variable is required")

    store = Store(db_path)
    try:
        from_blocks: dict[int, int] = {}
        to_blocks: dict[int, int] = {}
        valued_events: dict[int, list] = {}
        validation_results: dict[int, Any] = {}

        now_ts = int(time.time())
        start_ts = now_ts - (days * 86400)

        with httpx.Client() as http:
            explorer = EtherscanClient(api_key=api_key, http=http)

            for cid in chain_ids:
                rpc_env = f"MEVSCOUT_RPC_{cid}"
                rpc_url = os.environ.get(rpc_env)
                if not rpc_url:
                    raise ConfigError(f"{rpc_env} environment variable is required")

                rpc = RpcClient(url=rpc_url, http=http)

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

    except Exception as exc:
        clean_msg = _clean_error(exc)
        sys.stderr.write(f"Error: {clean_msg}\n")
        sys.exit(2)


if __name__ == "__main__":
    main()
