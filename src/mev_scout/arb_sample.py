"""Phase 2B — Leftover arbitrage opportunities at sampled blocks."""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
import json
import sys
import time
from typing import Any, Sequence

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.dex import (
    ARBITRUM_PAIRS,
    ARBITRUM_TOKENS,
    DEXES,
    LIQUIDITY_SELECTOR,
    Pool,
    QUOTE_EXACT_INPUT_SINGLE_SELECTOR,
    SLOT0_SELECTOR,
    discover_pools,
)
from mev_scout.explorer import LogSource
from mev_scout.report import evaluate_verdict
from mev_scout.rpc import ContractCallError, RpcClient, RpcError
from mev_scout.store import Store

SHALLOW_LIQUIDITY_THRESHOLD = 1_000_000_000
OVERHEAD_GAS = 100_000
PERSISTENCE_OFFSETS = [1, 2, 5, 20]
SIZES_USD = [1_000, 10_000, 50_000]


@dataclass
class ArbSampleResult:
    block: int
    pair: str
    pool_a: str
    pool_b: str
    size_usd: int
    gross_usd: Decimal | None = None
    gas_usd: Decimal | None = None
    net_usd: Decimal | None = None
    is_opportunity: bool = False
    persisted_blocks: int = 0
    is_shallow: bool = False
    reverted: bool = False
    profit_usd: Decimal | None = None


@dataclass
class ArbSampleMeta:
    chain_id: int
    total_sampled_blocks: int
    skipped_prefilter_pairs: int
    reverted_quotes: int
    from_block: int
    to_block: int
    unpriced_blocks: int = 0


@dataclass
class PairOpportunityReport:
    pair: str
    opportunity_blocks: int
    total_tested_blocks: int
    opportunity_share: Decimal
    median_net_usd: Decimal
    p90_net_usd: Decimal
    max_net_usd: Decimal
    persistence_distribution: dict[int, int]
    shallow_count: int


@dataclass
class ArbSampleReport:
    chain_id: int
    meta: ArbSampleMeta
    eurusd: Decimal
    threshold_eur: Decimal
    days: int
    pairs: dict[str, PairOpportunityReport]
    monthly_upper_bound_usd: Decimal
    monthly_upper_bound_eur: Decimal
    verdict: str
    verdict_reason: str

    def to_text(self) -> str:
        lines = [
            "================================================================================",
            f"mev-scout Phase 2B — Leftover Opportunities Census (Chain {self.chain_id})",
            "================================================================================",
            f"Sampled blocks: {self.meta.total_sampled_blocks} (from block {self.meta.from_block} to {self.meta.to_block})",
            f"Pre-filter skipped pairs: {self.meta.skipped_prefilter_pairs}",
            f"Reverted quotes (counted & skipped): {self.meta.reverted_quotes}",
            f"EUR/USD: {self.eurusd}",
            f"Monthly threshold: {self.threshold_eur:.0f} EUR",
            f"Shallow pool threshold: liquidity < {SHALLOW_LIQUIDITY_THRESHOLD:,}",
            "Note: Upper bound of what a slow bot could earn, scaled from sampled opportunities.",
            "",
            f"Monthly Upper Bound: ${self.monthly_upper_bound_usd:,.2f} ({self.monthly_upper_bound_eur:,.2f} EUR)",
            f"Verdict: {self.verdict}" + (f" ({self.verdict_reason})" if self.verdict_reason else ""),
            "",
            "Results Per Token Pair:",
        ]
        for pair_name, rep in sorted(self.pairs.items()):
            lines.append(f"Pair: {pair_name}")
            lines.append(
                f"  Opportunity rate: {rep.opportunity_blocks}/{rep.total_tested_blocks} blocks ({rep.opportunity_share * 100:.1f}%)"
            )
            lines.append(
                f"  Net profit at best size: median=${rep.median_net_usd:,.2f}, p90=${rep.p90_net_usd:,.2f}, max=${rep.max_net_usd:,.2f}"
            )
            lines.append(f"  Shallow pool opportunities: {rep.shallow_count}")
            lines.append(f"  Persistence distribution (blocks survived): {dict(rep.persistence_distribution)}")
            lines.append("")

        lines.append("================================================================================")
        return "\n".join(lines)

    def to_json(self) -> str:
        d = {
            "chain_id": self.chain_id,
            "total_sampled_blocks": self.meta.total_sampled_blocks,
            "from_block": self.meta.from_block,
            "to_block": self.meta.to_block,
            "skipped_prefilter_pairs": self.meta.skipped_prefilter_pairs,
            "reverted_quotes": self.meta.reverted_quotes,
            "eurusd": str(self.eurusd),
            "threshold_eur": str(self.threshold_eur),
            "days": self.days,
            "monthly_upper_bound_usd": str(self.monthly_upper_bound_usd),
            "monthly_upper_bound_eur": str(self.monthly_upper_bound_eur),
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "pairs": {
                k: {
                    "pair": v.pair,
                    "opportunity_blocks": v.opportunity_blocks,
                    "total_tested_blocks": v.total_tested_blocks,
                    "opportunity_share": str(v.opportunity_share),
                    "median_net_usd": str(v.median_net_usd),
                    "p90_net_usd": str(v.p90_net_usd),
                    "max_net_usd": str(v.max_net_usd),
                    "persistence_distribution": v.persistence_distribution,
                    "shallow_count": v.shallow_count,
                }
                for k, v in self.pairs.items()
            },
        }
        return json.dumps(d, indent=2)


def encode_quote_exact_input_single(
    token_in: str,
    token_out: str,
    amount_in: int,
    fee: int,
    sqrt_price_limit_x96: int = 0,
) -> str:
    """Encode QuoterV2.quoteExactInputSingle((address,address,uint256,uint24,uint160))."""
    clean_in = token_in.removeprefix("0x").lower().rjust(64, "0")
    clean_out = token_out.removeprefix("0x").lower().rjust(64, "0")
    clean_amt = hex(amount_in).removeprefix("0x").rjust(64, "0")
    clean_fee = hex(fee).removeprefix("0x").rjust(64, "0")
    clean_limit = hex(sqrt_price_limit_x96).removeprefix("0x").rjust(64, "0")
    return f"{QUOTE_EXACT_INPUT_SINGLE_SELECTOR}{clean_in}{clean_out}{clean_amt}{clean_fee}{clean_limit}"


def decode_quote_result(data_hex: str) -> tuple[int, int]:
    """Decode amountOut (word 0) and gasEstimate (word 3) from QuoterV2 result."""
    clean = data_hex.removeprefix("0x").removeprefix("0X")
    if len(clean) < 256:
        raise ValueError(f"Invalid quote result length: {len(clean)}")
    amount_out = int(clean[0:64], 16)
    gas_estimate = int(clean[192:256], 16)
    return amount_out, gas_estimate


def check_prefilter(
    price_a: Decimal, price_b: Decimal, fee_a: int, fee_b: int
) -> tuple[bool, Decimal]:
    """Check if mid-price gap between pool A and pool B is larger than combined fees.

    Boundary exact: equal gap = skip.
    """
    combined_fees = (Decimal(fee_a) + Decimal(fee_b)) / Decimal("1000000")
    if price_a <= Decimal("0"):
        return False, Decimal("0")
    gap = (price_b - price_a) / price_a
    passes = gap > combined_fees
    return passes, gap


def quote_round_trip(
    pool_a: Pool,
    pool_b: Pool,
    quote_token: str,
    base_token: str,
    size_usd: int,
    start_amount: int,
    block: int | str,
    rpc: RpcClient,
    weth_price_usd: Decimal,
) -> ArbSampleResult:
    """Execute round trip quote on QuoterV2: buy on A, sell on B."""
    quoter_a = DEXES[pool_a.dex].quoter
    quoter_b = DEXES[pool_b.dex].quoter

    # Leg 1: buy Base on Pool A with Quote
    calldata_1 = encode_quote_exact_input_single(
        token_in=quote_token,
        token_out=base_token,
        amount_in=start_amount,
        fee=pool_a.fee,
    )
    try:
        res_1 = rpc.call(quoter_a, calldata_1, block)
        intermediate_amount, gas_1 = decode_quote_result(res_1)
    except (ContractCallError, ValueError):
        return ArbSampleResult(
            block=int(str(block), 0) if str(block).isdigit() else 0,
            pair="",
            pool_a=pool_a.address,
            pool_b=pool_b.address,
            size_usd=size_usd,
            reverted=True,
        )

    # Leg 2: sell Base on Pool B for Quote
    calldata_2 = encode_quote_exact_input_single(
        token_in=base_token,
        token_out=quote_token,
        amount_in=intermediate_amount,
        fee=pool_b.fee,
    )
    try:
        res_2 = rpc.call(quoter_b, calldata_2, block)
        final_amount, gas_2 = decode_quote_result(res_2)
    except (ContractCallError, ValueError):
        return ArbSampleResult(
            block=int(str(block), 0) if str(block).isdigit() else 0,
            pair="",
            pool_a=pool_a.address,
            pool_b=pool_b.address,
            size_usd=size_usd,
            reverted=True,
        )

    profit_raw = final_amount - start_amount

    # Convert profit to USD
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    usdt = ARBITRUM_TOKENS["USDT"].address.lower()

    if quote_token.lower() in (usdc, usdt):
        profit_usd = Decimal(profit_raw) / Decimal(10**6)
    elif quote_token.lower() == weth:
        profit_usd = (Decimal(profit_raw) / Decimal(10**18)) * weth_price_usd
    else:
        profit_usd = Decimal(profit_raw) / Decimal(10**18)

    # Gas estimate
    total_gas = gas_1 + gas_2 + OVERHEAD_GAS
    # No guessed base fee: a missing block or field is an error, not 0.1 gwei.
    block_obj = rpc._call("eth_getBlockByNumber", [hex(block) if isinstance(block, int) else block, False])
    if not isinstance(block_obj, dict) or block_obj.get("baseFeePerGas") is None:
        raise RpcError(f"block {block} has no baseFeePerGas")
    base_fee_val = int(str(block_obj["baseFeePerGas"]), 0)

    # A base fee of 0 is a real value (not a missing one); no substitute.

    gas_wei = total_gas * base_fee_val
    gas_eth = Decimal(gas_wei) / Decimal(10**18)
    gas_usd = gas_eth * weth_price_usd
    net_usd = profit_usd - gas_usd
    is_opportunity = net_usd > Decimal("0")

    return ArbSampleResult(
        block=int(str(block), 0) if str(block).isdigit() else 0,
        pair="",
        pool_a=pool_a.address,
        pool_b=pool_b.address,
        size_usd=size_usd,
        gross_usd=profit_usd,
        gas_usd=gas_usd,
        net_usd=net_usd,
        profit_usd=profit_usd,
        is_opportunity=is_opportunity,
        reverted=False,
    )


def track_persistence(
    pool_a: Pool,
    pool_b: Pool,
    quote_token: str,
    base_token: str,
    size_usd: int,
    start_amount: int,
    initial_block: int,
    rpc: RpcClient,
    weth_price_usd: Decimal,
) -> int:
    """Track persistence at +1, +2, +5, +20 blocks.

    Persistence stops at the first block without profit.
    """
    persisted_blocks = 0
    for offset in PERSISTENCE_OFFSETS:
        target_block = initial_block + offset
        res = quote_round_trip(
            pool_a=pool_a,
            pool_b=pool_b,
            quote_token=quote_token,
            base_token=base_token,
            size_usd=size_usd,
            start_amount=start_amount,
            block=target_block,
            rpc=rpc,
            weth_price_usd=weth_price_usd,
        )
        if res.is_opportunity and res.net_usd is not None and res.net_usd > Decimal("0"):
            persisted_blocks = offset
        else:
            # First block without profit: stop persistence check immediately
            break
    return persisted_blocks


def sample_blocks(
    from_block: int,
    to_block: int,
    blocks_per_step: int,
    dense_ranges: Sequence[str] | None = None,
) -> list[int]:
    """Sample blocks regularly spaced plus all blocks in dense ranges."""
    sampled = set(range(from_block, to_block + 1, max(1, blocks_per_step)))
    if dense_ranges:
        for dr in dense_ranges:
            if ":" in dr:
                parts = dr.split(":")
                df, dt = int(parts[0]), int(parts[1])
                sampled.update(range(df, dt + 1))
    return sorted(sampled)


def generate_arb_sample_report(
    meta: ArbSampleMeta,
    results: list[ArbSampleResult],
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    days: int = 30,
) -> ArbSampleReport:
    """Aggregate per-pair statistics, persistence, and upper-bound monthly verdict."""
    by_pair: dict[str, list[ArbSampleResult]] = defaultdict(list)
    for r in results:
        by_pair[r.pair].append(r)

    pairs_report: dict[str, PairOpportunityReport] = {}
    total_best_net_usd = Decimal("0")

    # Map block -> best opportunity net across all pairs
    block_best_nets: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))

    for pair_name, p_results in by_pair.items():
        # Group by block to find opportunities and best size
        by_block: dict[int, list[ArbSampleResult]] = defaultdict(list)
        for r in p_results:
            by_block[r.block].append(r)

        opp_blocks = 0
        best_nets_for_pair: list[Decimal] = []
        persist_dist: dict[int, int] = defaultdict(int)
        shallow_count = 0

        for b, b_res in by_block.items():
            opps = [r for r in b_res if r.is_opportunity and r.net_usd is not None and r.net_usd > 0]
            if opps:
                opp_blocks += 1
                best_opp = max(opps, key=lambda r: r.net_usd or Decimal("0"))
                best_net = best_opp.net_usd or Decimal("0")
                best_nets_for_pair.append(best_net)
                persist_dist[best_opp.persisted_blocks] += 1
                if best_opp.is_shallow:
                    shallow_count += 1
                if best_net > block_best_nets[b]:
                    block_best_nets[b] = best_net

        total_tested = len(by_block)
        opp_share = Decimal(opp_blocks) / Decimal(total_tested) if total_tested > 0 else Decimal("0")

        if best_nets_for_pair:
            sorted_nets = sorted(best_nets_for_pair)
            n = len(sorted_nets)
            median_net = sorted_nets[n // 2]
            p90_net = sorted_nets[min(n - 1, int(0.90 * n))]
            max_net = sorted_nets[-1]
        else:
            median_net = Decimal("0")
            p90_net = Decimal("0")
            max_net = Decimal("0")

        pairs_report[pair_name] = PairOpportunityReport(
            pair=pair_name,
            opportunity_blocks=opp_blocks,
            total_tested_blocks=total_tested,
            opportunity_share=opp_share,
            median_net_usd=median_net,
            p90_net_usd=p90_net,
            max_net_usd=max_net,
            persistence_distribution=dict(persist_dist),
            shallow_count=shallow_count,
        )

    # Upper bound: sum of best-size net across samples, scaled to the month (30 days)
    sum_best_net = sum(block_best_nets.values(), Decimal("0"))
    monthly_upper_usd = (sum_best_net / Decimal(days)) * Decimal("30") if days > 0 else Decimal("0")
    monthly_upper_eur = monthly_upper_usd / eurusd

    # Verdict with same rule
    # Treating scaled monthly net as upper bound: passes if monthly_upper_eur >= threshold_eur
    if monthly_upper_eur >= threshold_eur:
        verdict = "PASS"
        verdict_reason = f"upper bound {monthly_upper_eur:.0f} EUR >= {threshold_eur:.0f} EUR"
    else:
        verdict = "FAIL"
        verdict_reason = f"upper bound {monthly_upper_eur:.0f} EUR < {threshold_eur:.0f} EUR"

    return ArbSampleReport(
        chain_id=meta.chain_id,
        meta=meta,
        eurusd=eurusd,
        threshold_eur=threshold_eur,
        days=days,
        pairs=pairs_report,
        monthly_upper_bound_usd=monthly_upper_usd,
        monthly_upper_bound_eur=monthly_upper_eur,
        verdict=verdict,
        verdict_reason=verdict_reason,
    )


def _get_token_decimals(token_addr: str) -> int:
    clean = token_addr.lower()
    for tok in ARBITRUM_TOKENS.values():
        if tok.address.lower() == clean:
            return tok.decimals
    raise ValueError(f"unknown token {token_addr}: decimals not configured")


def _read_slot0_mids(pools: list[Pool], block: int, rpc: RpcClient) -> tuple[dict[str, Decimal], int]:
    """Mid price (token0 in token1) per pool at `block`.

    A revert or empty result skips that pool and is counted; a transport error
    (RpcError) propagates, because an outage is not information about the pool.
    """
    mids: dict[str, Decimal] = {}
    reverted = 0
    for p in pools:
        try:
            res = rpc.call(p.address, SLOT0_SELECTOR, block)
        except ContractCallError:
            reverted += 1
            continue
        clean = res.removeprefix("0x").removeprefix("0X")
        sqrt_p = int(clean[0:64], 16)
        ratio = Decimal(sqrt_p) / Decimal(2**96)
        mids[p.address.lower()] = (ratio * ratio) * Decimal(
            10 ** (_get_token_decimals(p.token0) - _get_token_decimals(p.token1))
        )
    return mids, reverted


def _weth_usd_from_mids(pools: list[Pool], mids: dict[str, Decimal]) -> Decimal | None:
    """Median WETH/USD over all WETH/USDC and WETH/USDT pools with a price.

    The median ignores a shallow pool's absurd mid price (seen live: 9.14 vs ~2,648).
    None when no stable pool has a price; never a default.
    """
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    stables = {ARBITRUM_TOKENS["USDC"].address.lower(), ARBITRUM_TOKENS["USDT"].address.lower()}
    prices = []
    for p in pools:
        t0, t1 = p.token0.lower(), p.token1.lower()
        if not ((t0 == weth and t1 in stables) or (t1 == weth and t0 in stables)):
            continue
        m = mids.get(p.address.lower())
        if m is None or m <= 0:
            continue
        prices.append(m if t0 == weth else Decimal(1) / m)
    if not prices:
        return None
    prices.sort()
    n = len(prices)
    return prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2


def run_arb_sampling(
    chain_id: int,
    days: int,
    every_min: int,
    dense_ranges: Sequence[str] | None,
    rpc: RpcClient,
    explorer: LogSource,
    store: Store,
    now: int | None = None,
) -> ArbSampleMeta:
    """Execute block sampling, slot0 prefiltering, round trips, and persistence."""
    if chain_id != 42161:
        raise ConfigError(f"Arbitrage sampling currently only supports Arbitrum (42161), got {chain_id}")

    now_ts = int(time.time()) if now is None else now
    start_ts = now_ts - (days * 86400)
    from_block = explorer.block_by_time(chain_id, start_ts)
    to_block = rpc.block_number()

    if from_block > to_block:
        from_block = to_block

    total_span = max(1, to_block - from_block)
    step_seconds = every_min * 60
    blocks_per_step = max(1, int(step_seconds * total_span / max(1, days * 86400)))

    blocks = sample_blocks(from_block, to_block, blocks_per_step, dense_ranges)

    pools = store.get_pools(chain_id)
    if not pools:
        pools = discover_pools(chain_id, rpc)
        store.insert_pools(pools)

    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    usdt = ARBITRUM_TOKENS["USDT"].address.lower()

    pools_by_pair: dict[str, list[Pool]] = defaultdict(list)
    for p in pools:
        for sym_a, sym_b in ARBITRUM_PAIRS:
            tok_a = ARBITRUM_TOKENS[sym_a].address.lower()
            tok_b = ARBITRUM_TOKENS[sym_b].address.lower()
            if p.token0.lower() == min(tok_a, tok_b) and p.token1.lower() == max(tok_a, tok_b):
                pair_key = f"{sym_a}/{sym_b}"
                pools_by_pair[pair_key].append(p)
                break

    skipped_prefilter_pairs = 0
    reverted_quotes = 0
    unpriced_blocks = 0
    all_results: list[ArbSampleResult] = []

    # Results are saved after every sampled block and finished blocks are skipped
    # on restart: a 109-minute run was once lost to a single transient RPC error.
    done = store.get_sampled_blocks(chain_id)
    snap = (0, 0, 0, 0)
    prev_block: int | None = None

    def _flush(blk: int) -> None:
        store.insert_arb_samples(chain_id, all_results[snap[0]:])
        store.mark_sampled_block(
            chain_id, blk,
            skipped=skipped_prefilter_pairs - snap[1],
            reverted=reverted_quotes - snap[2],
            unpriced=unpriced_blocks - snap[3],
        )

    todo = [b for b in blocks if b not in done]
    for idx, b in enumerate(todo):
        if prev_block is not None:
            _flush(prev_block)
            if idx % 10 == 0:
                print(f"arb-sample: {len(done) + idx}/{len(blocks)} blocks", file=sys.stderr, flush=True)
        snap = (len(all_results), skipped_prefilter_pairs, reverted_quotes, unpriced_blocks)
        prev_block = b
        slot0_map, reverted = _read_slot0_mids(pools, b, rpc)
        reverted_quotes += reverted
        weth_price = _weth_usd_from_mids(pools, slot0_map)
        if weth_price is None:
            unpriced_blocks += 1
            continue

        for sym_a, sym_b in ARBITRUM_PAIRS:
            pair_key = f"{sym_a}/{sym_b}"
            pair_pools = pools_by_pair.get(pair_key, [])
            if len(pair_pools) < 2:
                continue

            base_tok = ARBITRUM_TOKENS[sym_a].address.lower()
            quote_tok = ARBITRUM_TOKENS[sym_b].address.lower()

            for p_a in pair_pools:
                for p_b in pair_pools:
                    if p_a.address.lower() == p_b.address.lower():
                        continue

                    p_mid_a = slot0_map.get(p_a.address.lower())
                    p_mid_b = slot0_map.get(p_b.address.lower())

                    if p_mid_a is None or p_mid_b is None:
                        continue

                    if p_a.token0.lower() == base_tok:
                        price_base_a = p_mid_a
                    else:
                        price_base_a = Decimal(1) / p_mid_a if p_mid_a > 0 else Decimal("0")

                    if p_b.token0.lower() == base_tok:
                        price_base_b = p_mid_b
                    else:
                        price_base_b = Decimal(1) / p_mid_b if p_mid_b > 0 else Decimal("0")

                    passes, gap = check_prefilter(price_base_a, price_base_b, p_a.fee, p_b.fee)
                    if not passes:
                        skipped_prefilter_pairs += 1
                        continue

                    is_shallow = False
                    for p_check in (p_a, p_b):
                        try:
                            liq_res = rpc.call(p_check.address, LIQUIDITY_SELECTOR, b)
                            liq_val = int(liq_res.removeprefix("0x").removeprefix("0X")[0:64], 16)
                            if liq_val < SHALLOW_LIQUIDITY_THRESHOLD:
                                is_shallow = True
                        except ContractCallError:
                            reverted_quotes += 1

                    for sz in SIZES_USD:
                        if quote_tok in (usdc, usdt):
                            start_amt = sz * (10**6)
                        elif quote_tok == weth:
                            start_amt = int((Decimal(sz) / weth_price) * Decimal(10**18))
                        else:
                            start_amt = sz * (10**18)

                        res = quote_round_trip(
                            pool_a=p_a,
                            pool_b=p_b,
                            quote_token=quote_tok,
                            base_token=base_tok,
                            size_usd=sz,
                            start_amount=start_amt,
                            block=b,
                            rpc=rpc,
                            weth_price_usd=weth_price,
                        )
                        if res.reverted:
                            reverted_quotes += 1
                            continue

                        res.pair = pair_key
                        res.is_shallow = is_shallow

                        if res.is_opportunity:
                            persisted = track_persistence(
                                pool_a=p_a,
                                pool_b=p_b,
                                quote_token=quote_tok,
                                base_token=base_tok,
                                size_usd=sz,
                                start_amount=start_amt,
                                initial_block=b,
                                rpc=rpc,
                                weth_price_usd=weth_price,
                            )
                            res.persisted_blocks = persisted

                        all_results.append(res)

    if prev_block is not None:
        _flush(prev_block)
    skipped_prefilter_pairs, reverted_quotes, unpriced_blocks = store.sampled_block_totals(chain_id)

    meta = ArbSampleMeta(
        chain_id=chain_id,
        total_sampled_blocks=len(blocks),
        skipped_prefilter_pairs=skipped_prefilter_pairs,
        reverted_quotes=reverted_quotes,
        from_block=from_block,
        to_block=to_block,
        unpriced_blocks=unpriced_blocks,
    )
    store.set_arb_sample_meta(meta)
    return meta
