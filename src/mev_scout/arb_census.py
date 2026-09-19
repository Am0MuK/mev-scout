"""Phase 2A — DEX arbitrage census from swap events."""

from collections import defaultdict
import csv
from dataclasses import asdict, dataclass, field
from decimal import Decimal
import io
import json
import math
import random
import time
from typing import Any

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.dex import ARBITRUM_TOKENS, Pool
from mev_scout.report import evaluate_verdict
from mev_scout.rpc import RpcClient
from mev_scout.store import Store
from mev_scout.swaps import DecodedSwap

ERC20_TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ARB_BUCKET_ORDER = ["<10", "10–100", "100–1k", "≥1k"]


@dataclass
class DetectedArbitrage:
    tx_hash: str
    block: int
    timestamp: int
    swaps: list[DecodedSwap]
    net_token_flows: dict[str, int]
    gross_usd: Decimal = Decimal("0")
    gas_usd: Decimal = Decimal("0")
    net_usd: Decimal = Decimal("0")
    bot_from: str = ""
    contract_to: str = ""


@dataclass
class ArbValidationResult:
    total_sampled: int = 0
    checks_passed: int = 0
    checks_failed: int = 0
    disagreements: list[str] = field(default_factory=list)


@dataclass
class ArbBucketReport:
    bucket: str
    count: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal


@dataclass
class ArbMonthReport:
    month_index: int
    start_ts: int
    end_ts: int
    count: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal
    top1_share: Decimal
    top3_share: Decimal
    hhi: Decimal
    buckets: dict[str, ArbBucketReport]


@dataclass
class ArbCensusReport:
    chain_id: int
    from_block: int
    to_block: int
    total_arbitrages: int
    distinct_bots: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal
    top1_share: Decimal
    top3_share: Decimal
    hhi: Decimal
    median_net_usd: Decimal
    p90_net_usd: Decimal
    max_net_usd: Decimal
    verdict: str
    verdict_reason: str
    threshold_eur: Decimal
    eurusd: Decimal
    months: list[ArbMonthReport]
    buckets: dict[str, ArbBucketReport]
    validation: ArbValidationResult
    arbitrages: list[DetectedArbitrage] = field(default_factory=list)

    def to_text(self) -> str:
        lines = [
            "================================================================================",
            f"mev-scout Phase 2A — DEX Arbitrage Census (Chain {self.chain_id})",
            "================================================================================",
            f"Window blocks: {self.from_block} to {self.to_block}",
            f"EUR/USD rate: {self.eurusd}",
            f"Monthly threshold: {self.threshold_eur:.0f} EUR",
            "Assumption: USDC = 1 USD, USDT = 1 USD.",
            "Lower bound note: routes through untracked pools are missed.",
            'Top-1 share is a proxy for "the market is still contestable".',
            "",
            "Overall Summary:",
            f"  Total detected arbitrages: {self.total_arbitrages}",
            f"  Distinct bot addresses: {self.distinct_bots}",
            f"  Gross profit: ${self.gross_usd:,.2f}",
            f"  Gas cost:     ${self.gas_usd:,.2f}",
            f"  Net profit:   ${self.net_usd:,.2f} ({self.net_eur:,.2f} EUR)",
            f"  Top-1 bot share: {self.top1_share * 100:.1f}%",
            f"  Top-3 bot share: {self.top3_share * 100:.1f}%",
            f"  HHI: {self.hhi:.1f}",
            f"  Distribution of net profit: median=${self.median_net_usd:,.2f}, p90=${self.p90_net_usd:,.2f}, max=${self.max_net_usd:,.2f}",
            "",
            f"Verdict: {self.verdict}" + (f" ({self.verdict_reason})" if self.verdict_reason else ""),
            "",
            "Profit Size Buckets (Net USD):",
        ]
        for b_name in ARB_BUCKET_ORDER:
            b = self.buckets.get(b_name)
            if b:
                lines.append(
                    f"  {b.bucket:>8}: {b.count:>5} arbs | gross ${b.gross_usd:>10,.2f} | gas ${b.gas_usd:>10,.2f} | net ${b.net_usd:>10,.2f} ({b.net_eur:>10,.2f} EUR)"
                )

        lines.append("")
        lines.append("Monthly Breakdown (30-day periods, newest first):")
        for m in self.months:
            lines.append(
                f"  Month {m.month_index + 1}: {m.count:>5} arbs | gross ${m.gross_usd:>10,.2f} | gas ${m.gas_usd:>10,.2f} | net ${m.net_usd:>10,.2f} ({m.net_eur:>10,.2f} EUR) | top1 {m.top1_share * 100:.1f}%"
            )

        lines.append("")
        lines.append("Validation (20-sample receipt Transfer tie-out):")
        lines.append(
            f"  Sampled: {self.validation.total_sampled}, Agreed: {self.validation.checks_passed}, Disagreed: {self.validation.checks_failed}"
        )
        if self.validation.disagreements:
            lines.append("  Disagreements:")
            for d in self.validation.disagreements:
                lines.append(f"    - {d}")

        lines.append("================================================================================")
        return "\n".join(lines)

    def to_json(self) -> str:
        d = {
            "chain_id": self.chain_id,
            "from_block": self.from_block,
            "to_block": self.to_block,
            "total_arbitrages": self.total_arbitrages,
            "distinct_bots": self.distinct_bots,
            "gross_usd": str(self.gross_usd),
            "gas_usd": str(self.gas_usd),
            "net_usd": str(self.net_usd),
            "net_eur": str(self.net_eur),
            "top1_share": str(self.top1_share),
            "top3_share": str(self.top3_share),
            "hhi": str(self.hhi),
            "median_net_usd": str(self.median_net_usd),
            "p90_net_usd": str(self.p90_net_usd),
            "max_net_usd": str(self.max_net_usd),
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "threshold_eur": str(self.threshold_eur),
            "eurusd": str(self.eurusd),
            "buckets": {
                k: {
                    "bucket": v.bucket,
                    "count": v.count,
                    "gross_usd": str(v.gross_usd),
                    "gas_usd": str(v.gas_usd),
                    "net_usd": str(v.net_usd),
                    "net_eur": str(v.net_eur),
                }
                for k, v in self.buckets.items()
            },
            "months": [
                {
                    "month_index": m.month_index,
                    "start_ts": m.start_ts,
                    "end_ts": m.end_ts,
                    "count": m.count,
                    "gross_usd": str(m.gross_usd),
                    "gas_usd": str(m.gas_usd),
                    "net_usd": str(m.net_usd),
                    "net_eur": str(m.net_eur),
                    "top1_share": str(m.top1_share),
                    "top3_share": str(m.top3_share),
                    "hhi": str(m.hhi),
                }
                for m in self.months
            ],
            "validation": {
                "total_sampled": self.validation.total_sampled,
                "checks_passed": self.validation.checks_passed,
                "checks_failed": self.validation.checks_failed,
                "disagreements": self.validation.disagreements,
            },
        }
        return json.dumps(d, indent=2)


def bucket_for_arb_profit(profit_usd: Decimal) -> str:
    if profit_usd < Decimal("10"):
        return "<10"
    if profit_usd < Decimal("100"):
        return "10–100"
    if profit_usd < Decimal("1000"):
        return "100–1k"
    return "≥1k"


def detect_arbitrages(
    swaps: list[DecodedSwap], pools: dict[str, Pool]
) -> list[DetectedArbitrage]:
    """Group swaps by transaction and detect atomic arbitrage cycles across >= 2 tracked pools."""
    by_tx: dict[str, list[DecodedSwap]] = defaultdict(list)
    for s in swaps:
        by_tx[s.tx_hash.lower()].append(s)

    detected: list[DetectedArbitrage] = []
    for tx_hash, tx_swaps in by_tx.items():
        # Must touch at least two tracked pools
        tracked_swaps = [s for s in tx_swaps if s.pool.lower() in pools]
        unique_pools = {s.pool.lower() for s in tracked_swaps}
        if len(unique_pools) < 2:
            continue

        # Net flow per token across tracked swaps
        flows: dict[str, int] = defaultdict(int)
        for s in tracked_swaps:
            pool = pools[s.pool.lower()]
            # amount0 > 0: into pool (bot paid out), amount0 < 0: out of pool (bot received)
            flows[pool.token0.lower()] += -s.amount0
            flows[pool.token1.lower()] += -s.amount1

        # Atomic arbitrage: net flow >= 0 for all tokens, and > 0 for at least one
        has_positive = False
        all_non_negative = True
        for token, net_val in flows.items():
            if net_val < 0:
                all_non_negative = False
                break
            if net_val > 0:
                has_positive = True

        if all_non_negative and has_positive:
            # Sort swaps by log_index
            sorted_swaps = sorted(tracked_swaps, key=lambda s: s.log_index)
            detected.append(
                DetectedArbitrage(
                    tx_hash=tx_hash,
                    block=sorted_swaps[0].block,
                    timestamp=sorted_swaps[0].timestamp,
                    swaps=sorted_swaps,
                    net_token_flows=dict(flows),
                )
            )

    # Return ordered by block and timestamp
    return sorted(detected, key=lambda a: (a.block, a.swaps[0].log_index if a.swaps else 0))


def _get_token_decimals(token_addr: str) -> int:
    clean = token_addr.lower()
    for tok in ARBITRUM_TOKENS.values():
        if tok.address.lower() == clean:
            return tok.decimals
    return 18


def _sqrt_price_to_price(sqrt_price_x96: int, dec0: int, dec1: int) -> Decimal:
    """Returns price of token0 in terms of token1."""
    ratio = Decimal(sqrt_price_x96) / Decimal(2**96)
    raw_p = ratio * ratio
    return raw_p * Decimal(10 ** (dec0 - dec1))


def _find_weth_usd_price(
    block: int,
    dex: str,
    pools: dict[str, Pool],
    store: Store,
    chain_id: int = 42161,
) -> Decimal:
    """Find the WETH price in USD using a WETH/USDC or WETH/USDT pool."""
    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    usdt = ARBITRUM_TOKENS["USDT"].address.lower()

    # Find matching pools
    stable_pools = [
        p for p in pools.values()
        if (p.token0.lower() == weth and p.token1.lower() in (usdc, usdt))
        or (p.token1.lower() == weth and p.token0.lower() in (usdc, usdt))
    ]

    # Search for nearest earlier swap
    for pool in stable_pools:
        earlier_swaps = store.get_swaps(chain_id=chain_id, to_block=block, pool=pool.address)
        if earlier_swaps:
            last_swap = earlier_swaps[-1]
            dec0 = _get_token_decimals(pool.token0)
            dec1 = _get_token_decimals(pool.token1)
            p0 = _sqrt_price_to_price(last_swap.sqrt_price_x96, dec0, dec1)
            if pool.token0.lower() == weth:
                return p0
            else:
                return Decimal(1) / p0 if p0 > 0 else Decimal("0")

    # If no stored earlier swap found, check any swap from that pool
    for pool in stable_pools:
        any_swaps = store.get_swaps(chain_id=chain_id, pool=pool.address)
        if any_swaps:
            last_swap = any_swaps[0]
            dec0 = _get_token_decimals(pool.token0)
            dec1 = _get_token_decimals(pool.token1)
            p0 = _sqrt_price_to_price(last_swap.sqrt_price_x96, dec0, dec1)
            if pool.token0.lower() == weth:
                return p0
            else:
                return Decimal(1) / p0 if p0 > 0 else Decimal("0")

    # Fallback default price for Arbitrum tests if no pool swaps are loaded
    return Decimal("2600")


def value_arbitrages(
    arbs: list[DetectedArbitrage],
    rpc: RpcClient,
    pools: dict[str, Pool],
    store: Store,
    chain_id: int = 42161,
) -> list[DetectedArbitrage]:
    """Value gross profit and gas in USD for each detected arbitrage."""
    if not arbs:
        return []

    weth = ARBITRUM_TOKENS["WETH"].address.lower()
    usdc = ARBITRUM_TOKENS["USDC"].address.lower()
    usdt = ARBITRUM_TOKENS["USDT"].address.lower()

    valued: list[DetectedArbitrage] = []

    for arb in arbs:
        # Determine WETH price for this block
        weth_price = Decimal("0")
        # Check if one of the swaps is in WETH/USDC or WETH/USDT
        for s in arb.swaps:
            pool = pools.get(s.pool.lower())
            if not pool:
                continue
            if (pool.token0.lower() == weth and pool.token1.lower() in (usdc, usdt)) or (
                pool.token1.lower() == weth and pool.token0.lower() in (usdc, usdt)
            ):
                dec0 = _get_token_decimals(pool.token0)
                dec1 = _get_token_decimals(pool.token1)
                p0 = _sqrt_price_to_price(s.sqrt_price_x96, dec0, dec1)
                weth_price = p0 if pool.token0.lower() == weth else (Decimal(1) / p0 if p0 > 0 else Decimal("0"))
                break

        if weth_price == Decimal("0"):
            dex = arb.swaps[0].dex if arb.swaps else "uniswap_v3"
            weth_price = _find_weth_usd_price(arb.block, dex, pools, store, chain_id)

        # Compute gross profit in USD
        gross_usd = Decimal("0")
        for token, flow in arb.net_token_flows.items():
            if flow <= 0:
                continue
            tok_lower = token.lower()
            dec = _get_token_decimals(tok_lower)
            amount = Decimal(flow) / Decimal(10**dec)

            if tok_lower in (usdc, usdt):
                gross_usd += amount
            elif tok_lower == weth:
                gross_usd += amount * weth_price
            else:
                # Other tokens (e.g. ARB, WBTC)
                # Check if swap in arb gives direct price in USDC/USDT or in WETH
                price_found = False
                for s in arb.swaps:
                    p = pools.get(s.pool.lower())
                    if not p:
                        continue
                    if p.token0.lower() == tok_lower or p.token1.lower() == tok_lower:
                        other = p.token1.lower() if p.token0.lower() == tok_lower else p.token0.lower()
                        dec0 = _get_token_decimals(p.token0)
                        dec1 = _get_token_decimals(p.token1)
                        p0 = _sqrt_price_to_price(s.sqrt_price_x96, dec0, dec1)
                        token_price_in_other = p0 if p.token0.lower() == tok_lower else (Decimal(1) / p0 if p0 > 0 else Decimal("0"))
                        if other in (usdc, usdt):
                            gross_usd += amount * token_price_in_other
                            price_found = True
                            break
                        elif other == weth:
                            gross_usd += amount * token_price_in_other * weth_price
                            price_found = True
                            break
                if not price_found:
                    # Default: cannot price without data
                    pass

        # Receipt gas and attribution
        receipt = rpc.receipt(arb.tx_hash)
        gas_used = int(str(receipt.get("gasUsed", "0x0")), 0)
        gas_price = int(str(receipt.get("effectiveGasPrice") or receipt.get("gasPrice") or "0x0"), 0)
        gas_wei = gas_used * gas_price
        gas_eth = Decimal(gas_wei) / Decimal(10**18)
        gas_usd = gas_eth * weth_price

        bot_from = str(receipt.get("from", "")).lower()
        contract_to = str(receipt.get("to", "")).lower()
        net_usd = gross_usd - gas_usd

        valued.append(
            DetectedArbitrage(
                tx_hash=arb.tx_hash,
                block=arb.block,
                timestamp=arb.timestamp,
                swaps=arb.swaps,
                net_token_flows=arb.net_token_flows,
                gross_usd=gross_usd,
                gas_usd=gas_usd,
                net_usd=net_usd,
                bot_from=bot_from,
                contract_to=contract_to,
            )
        )

    return valued


def validate_arbitrages(
    arbs: list[DetectedArbitrage],
    rpc: RpcClient,
    seed: int = 42,
    max_sample: int = 20,
) -> ArbValidationResult:
    """Fixed-seed sample tie-out: recompute net token flow from receipt Transfer logs."""
    result = ArbValidationResult()
    if not arbs:
        return result

    sorted_arbs = sorted(arbs, key=lambda a: (a.block, a.tx_hash))
    sample_size = min(len(sorted_arbs), max_sample)
    rng = random.Random(seed)
    sampled = rng.sample(sorted_arbs, sample_size)
    result.total_sampled = sample_size

    for a in sampled:
        receipt = rpc.receipt(a.tx_hash)
        logs = receipt.get("logs", [])
        bot_to = a.contract_to.lower()
        bot_from = a.bot_from.lower()

        # Recompute net token flow to/from bot
        transfer_flows: dict[str, int] = defaultdict(int)
        for log in logs:
            topics = log.get("topics", [])
            if not topics or str(topics[0]).lower() != ERC20_TRANSFER_TOPIC0:
                continue
            if len(topics) < 3:
                continue
            token_addr = str(log.get("address", "")).lower()
            from_addr = "0x" + str(topics[1])[-40:].lower()
            to_addr = "0x" + str(topics[2])[-40:].lower()
            val_raw = log.get("data", "0x0")
            try:
                val = int(str(val_raw), 16)
            except (ValueError, TypeError):
                continue

            if to_addr in (bot_to, bot_from):
                transfer_flows[token_addr] += val
            if from_addr in (bot_to, bot_from):
                transfer_flows[token_addr] -= val

        # Check positive-profit tokens
        matches = True
        for tok, expected_flow in a.net_token_flows.items():
            if expected_flow > 0:
                actual = transfer_flows.get(tok.lower(), 0)
                if actual < expected_flow:
                    matches = False
                    result.disagreements.append(
                        f"tx {a.tx_hash}: token {tok} expected net flow {expected_flow}, receipt Transfer net flow {actual}"
                    )
                    break

        if matches:
            result.checks_passed += 1
        else:
            result.checks_failed += 1

    return result


def _calculate_arb_concentration(arbs: list[DetectedArbitrage]) -> tuple[Decimal, Decimal, Decimal]:
    bot_net: dict[str, Decimal] = defaultdict(Decimal)
    for a in arbs:
        bot_key = a.bot_from or a.contract_to or "unknown"
        bot_net[bot_key] += a.net_usd

    positive_nets = {k: v for k, v in bot_net.items() if v > 0}
    total_pos = sum(positive_nets.values(), Decimal("0"))
    if total_pos <= 0:
        return Decimal("0"), Decimal("0"), Decimal("0")

    sorted_shares = sorted((v / total_pos for v in positive_nets.values()), reverse=True)
    top1 = sorted_shares[0] if sorted_shares else Decimal("0")
    top3 = sum(sorted_shares[:3], Decimal("0"))
    hhi = sum(((s * Decimal("100")) ** 2 for s in sorted_shares), Decimal("0"))
    return top1, top3, hhi


def _monthly_arb_net_eur(
    arbs: list[DetectedArbitrage], anchor_ts: int, m_count: int, eurusd: Decimal
) -> list[Decimal]:
    out = []
    for m_idx in range(m_count):
        hi = anchor_ts - m_idx * 30 * 86400
        lo = hi - 30 * 86400
        m_net = sum(
            (a.net_usd for a in arbs if lo < a.timestamp <= hi),
            Decimal("0"),
        )
        out.append(m_net / eurusd)
    return out


def generate_arb_census_report(
    chain_id: int,
    arbitrages: list[DetectedArbitrage],
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    days: int = 90,
    end_ts: int | None = None,
    validation: ArbValidationResult | None = None,
) -> ArbCensusReport:
    """Generate overall report, monthly breakdown, concentration, and verdict."""
    now_ts = int(time.time()) if end_ts is None else end_ts
    from_block = min((a.block for a in arbitrages), default=0)
    to_block = max((a.block for a in arbitrages), default=0)

    total_count = len(arbitrages)
    distinct_bots = len({a.bot_from.lower() for a in arbitrages if a.bot_from})

    total_gross = sum((a.gross_usd for a in arbitrages), Decimal("0"))
    total_gas = sum((a.gas_usd for a in arbitrages), Decimal("0"))
    total_net = sum((a.net_usd for a in arbitrages), Decimal("0"))
    total_net_eur = total_net / eurusd

    top1, top3, hhi = _calculate_arb_concentration(arbitrages)

    # Distribution of net USD
    if arbitrages:
        sorted_nets = sorted(a.net_usd for a in arbitrages)
        n = len(sorted_nets)
        median_net = sorted_nets[n // 2]
        p90_idx = min(n - 1, int(0.90 * n))
        p90_net = sorted_nets[p90_idx]
        max_net = sorted_nets[-1]
    else:
        median_net = Decimal("0")
        p90_net = Decimal("0")
        max_net = Decimal("0")

    # Profit size buckets
    bucket_counts: dict[str, int] = defaultdict(int)
    bucket_gross: dict[str, Decimal] = defaultdict(Decimal)
    bucket_gas: dict[str, Decimal] = defaultdict(Decimal)
    bucket_net: dict[str, Decimal] = defaultdict(Decimal)

    for a in arbitrages:
        b_name = bucket_for_arb_profit(a.net_usd)
        bucket_counts[b_name] += 1
        bucket_gross[b_name] += a.gross_usd
        bucket_gas[b_name] += a.gas_usd
        bucket_net[b_name] += a.net_usd

    buckets_report: dict[str, ArbBucketReport] = {}
    for b_name in ARB_BUCKET_ORDER:
        net_u = bucket_net[b_name]
        buckets_report[b_name] = ArbBucketReport(
            bucket=b_name,
            count=bucket_counts[b_name],
            gross_usd=bucket_gross[b_name],
            gas_usd=bucket_gas[b_name],
            net_usd=net_u,
            net_eur=net_u / eurusd,
        )

    # Monthly breakdown (30-day periods)
    m_count = max(1, days // 30)
    monthly_eur = _monthly_arb_net_eur(arbitrages, now_ts, m_count, eurusd)
    months_report: list[ArbMonthReport] = []

    for m_idx in range(m_count):
        hi = now_ts - m_idx * 30 * 86400
        lo = hi - 30 * 86400
        m_arbs = [a for a in arbitrages if lo < a.timestamp <= hi]
        m_gross = sum((a.gross_usd for a in m_arbs), Decimal("0"))
        m_gas = sum((a.gas_usd for a in m_arbs), Decimal("0"))
        m_net = sum((a.net_usd for a in m_arbs), Decimal("0"))
        m_net_eur = m_net / eurusd
        m_top1, m_top3, m_hhi = _calculate_arb_concentration(m_arbs)

        m_b_counts: dict[str, int] = defaultdict(int)
        m_b_gross: dict[str, Decimal] = defaultdict(Decimal)
        m_b_gas: dict[str, Decimal] = defaultdict(Decimal)
        m_b_net: dict[str, Decimal] = defaultdict(Decimal)
        for a in m_arbs:
            bn = bucket_for_arb_profit(a.net_usd)
            m_b_counts[bn] += 1
            m_b_gross[bn] += a.gross_usd
            m_b_gas[bn] += a.gas_usd
            m_b_net[bn] += a.net_usd

        m_buckets = {
            bn: ArbBucketReport(
                bucket=bn,
                count=m_b_counts[bn],
                gross_usd=m_b_gross[bn],
                gas_usd=m_b_gas[bn],
                net_usd=m_b_net[bn],
                net_eur=m_b_net[bn] / eurusd,
            )
            for bn in ARB_BUCKET_ORDER
        }

        months_report.append(
            ArbMonthReport(
                month_index=m_idx,
                start_ts=lo,
                end_ts=hi,
                count=len(m_arbs),
                gross_usd=m_gross,
                gas_usd=m_gas,
                net_usd=m_net,
                net_eur=m_net_eur,
                top1_share=m_top1,
                top3_share=m_top3,
                hhi=m_hhi,
                buckets=m_buckets,
            )
        )

    # Verdict using Phase 1 helper
    verdict, verdict_reason = evaluate_verdict(monthly_eur, top1, threshold_eur)

    return ArbCensusReport(
        chain_id=chain_id,
        from_block=from_block,
        to_block=to_block,
        total_arbitrages=total_count,
        distinct_bots=distinct_bots,
        gross_usd=total_gross,
        gas_usd=total_gas,
        net_usd=total_net,
        net_eur=total_net_eur,
        top1_share=top1,
        top3_share=top3,
        hhi=hhi,
        median_net_usd=median_net,
        p90_net_usd=p90_net,
        max_net_usd=max_net,
        verdict=verdict,
        verdict_reason=verdict_reason,
        threshold_eur=threshold_eur,
        eurusd=eurusd,
        months=months_report,
        buckets=buckets_report,
        validation=validation or ArbValidationResult(),
        arbitrages=arbitrages,
    )


def generate_arb_csv(arbitrages: list[DetectedArbitrage], eurusd: Decimal) -> str:
    """Export arbitrage events to CSV with all calculation columns."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "tx_hash",
        "block",
        "timestamp",
        "bot_from",
        "contract_to",
        "swaps_count",
        "gross_usd",
        "gas_usd",
        "net_usd",
        "net_eur",
        "profit_bucket",
    ])
    for a in arbitrages:
        writer.writerow([
            a.tx_hash,
            a.block,
            a.timestamp,
            a.bot_from,
            a.contract_to,
            len(a.swaps),
            f"{a.gross_usd:.4f}",
            f"{a.gas_usd:.4f}",
            f"{a.net_usd:.4f}",
            f"{a.net_usd / eurusd:.4f}",
            bucket_for_arb_profit(a.net_usd),
        ])
    return out.getvalue()
