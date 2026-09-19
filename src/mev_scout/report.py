"""Reporting module: metrics, concentration, verdicts, formatting, and exports."""

from collections import defaultdict
import csv
from dataclasses import asdict, dataclass, field
from decimal import Decimal
import io
import json
from typing import Any

from mev_scout.chains import CHAINS, UNCOVERED_CHAINS
from mev_scout.store import Store
from mev_scout.validate import ValidationResult
from mev_scout.value import ValuedLiquidation

BUCKET_ORDER = ["<100", "100–1k", "1k–10k", "≥10k"]
CONTESTABILITY_NOTE = 'Top-1 share is a proxy for "the market is still contestable".'


class CoverageError(Exception):
    """Raised when the stored block ranges have gaps for the requested window."""


def bucket_for_debt(debt_usd: Decimal) -> str:
    if debt_usd < Decimal("100"):
        return "<100"
    if debt_usd < Decimal("1000"):
        return "100–1k"
    if debt_usd < Decimal("10000"):
        return "1k–10k"
    return "≥10k"


def calculate_concentration(items: list[ValuedLiquidation]) -> dict[str, Decimal]:
    liq_net: dict[str, Decimal] = defaultdict(Decimal)
    for it in items:
        if not it.unpriced and it.net_usd is not None:
            liq_net[it.event.liquidator] += it.net_usd

    positive_nets = {k: v for k, v in liq_net.items() if v > 0}
    total_net = sum(positive_nets.values(), Decimal("0"))

    if total_net <= 0:
        return {
            "top1_share": Decimal("0"),
            "top3_share": Decimal("0"),
            "hhi": Decimal("0"),
        }

    sorted_shares = sorted(
        (v / total_net for v in positive_nets.values()),
        reverse=True,
    )
    top1_share = sorted_shares[0] if sorted_shares else Decimal("0")
    top3_share = sum(sorted_shares[:3], Decimal("0"))
    hhi = sum(((s * Decimal("100")) ** 2 for s in sorted_shares), Decimal("0"))

    return {
        "top1_share": top1_share,
        "top3_share": top3_share,
        "hhi": hhi,
    }


def evaluate_verdict(
    monthly_eur: list[Decimal],
    top1_share: Decimal,
    threshold_eur: Decimal = Decimal("300"),
) -> tuple[str, str]:
    """PASS needs the threshold in at least two thirds of the months, not on average.

    One crash month can carry a large average while every other month earns
    nothing (Sonic, June 2026); a bot needs income it can count on.
    """
    fails = []
    n = len(monthly_eur)
    required = (2 * n + 2) // 3
    ok = sum(1 for m in monthly_eur if m >= threshold_eur)
    if n == 0 or ok < required:
        fails.append(
            f"threshold {threshold_eur:.0f} EUR reached in {ok} of {n} months (needs {required})"
        )
    if top1_share > Decimal("0.50"):
        top1_pct = top1_share * Decimal("100")
        fails.append(f"top-1 liquidator share too concentrated ({top1_pct:.1f}% > 50.0%)")

    if fails:
        return "FAIL", " and ".join(fails)
    return "PASS", ""


def _monthly_net_eur(
    items: list[ValuedLiquidation], anchor_ts: int, m_count: int, eurusd: Decimal
) -> list[Decimal]:
    """Net EUR per 30-day month, newest first, half-open (start, end] periods."""
    out = []
    for m_idx in range(m_count):
        hi = anchor_ts - m_idx * 30 * 86400
        lo = hi - 30 * 86400
        net = sum(
            (it.net_usd for it in items if it.net_usd is not None and lo < it.event.timestamp <= hi),
            Decimal("0"),
        )
        out.append(net / eurusd)
    return out


@dataclass
class BucketReport:
    bucket: str
    event_count: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal
    avg_monthly_eur: Decimal
    top1_share: Decimal
    top3_share: Decimal
    hhi: Decimal
    verdict: str
    verdict_reason: str


@dataclass
class MonthReport:
    month_index: int
    start_ts: int
    end_ts: int
    event_count: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal
    top1_share: Decimal
    top3_share: Decimal
    hhi: Decimal
    buckets: dict[str, BucketReport]


@dataclass
class ChainReport:
    chain_id: int
    chain_name: str
    from_block: int
    to_block: int
    covered: bool
    event_count: int
    distinct_liquidators: int
    unpriced_count: int
    gross_usd: Decimal
    gas_usd: Decimal
    net_usd: Decimal
    net_eur: Decimal
    avg_monthly_eur: Decimal
    top1_share: Decimal
    top3_share: Decimal
    hhi: Decimal
    verdict: str
    verdict_reason: str
    months: list[MonthReport]
    buckets: dict[str, BucketReport]
    validation: ValidationResult
    anomalies: list[dict] = field(default_factory=list)


@dataclass
class CensusReport:
    chains: list[ChainReport]
    uncovered_chains: list[dict[str, Any]]
    eurusd: Decimal
    threshold_eur: Decimal
    days: int
    market_note: str = CONTESTABILITY_NOTE

    def to_text(self) -> str:
        lines: list[str] = []

        # 1. Validation warnings at top
        warnings = []
        for cr in self.chains:
            v = cr.validation
            if v.has_warnings:
                for dis in v.gas_disagreements + v.transfer_disagreements + v.l1_warnings:
                    warnings.append(f"WARNING [{cr.chain_name}]: {dis}")
        if warnings:
            lines.append("=" * 80)
            lines.append("VALIDATION WARNINGS:")
            for w in warnings:
                lines.append(f"  {w}")
            lines.append("=" * 80)
            lines.append("")

        lines.append("MEV-SCOUT PHASE 1: LIQUIDATION OPPORTUNITY CENSUS")
        lines.append(f"Window: {self.days} days | EUR/USD: {self.eurusd} | Threshold: {self.threshold_eur} EUR/mo")
        lines.append(self.market_note)
        lines.append("-" * 80)

        # Covered Chains
        for cr in self.chains:
            lines.append(f"Chain: {cr.chain_name} (ID: {cr.chain_id})")
            lines.append(f"  Blocks: {cr.from_block} to {cr.to_block} (Coverage: {'Gaps None (100%)' if cr.covered else 'GAPS DETECTED'})")
            lines.append(f"  Events: {cr.event_count} total ({cr.unpriced_count} unpriced) | Distinct liquidators: {cr.distinct_liquidators}")
            if cr.anomalies:
                total_anom = sum(Decimal(x["net_usd"]) for x in cr.anomalies)
                lines.append(
                    f"  Anomalous liquidations excluded (collateral > debt x {ANOMALY_COLLATERAL_TO_DEBT} or debt 0): "
                    f"{len(cr.anomalies)} events, ${total_anom:,.2f} net"
                )
                for x in sorted(cr.anomalies, key=lambda x: Decimal(x["net_usd"]), reverse=True)[:5]:
                    lines.append(f"    {x['tx_hash']} block {x['block']}: debt ${Decimal(x['debt_usd']):,.2f}, collateral ${Decimal(x['collateral_usd']):,.2f}")
            lines.append(f"  Totals: Gross ${cr.gross_usd:,.2f} | Gas ${cr.gas_usd:,.2f} | Net ${cr.net_usd:,.2f} ({cr.net_eur:,.2f} EUR)")
            lines.append(f"  Monthly Average Net: {cr.avg_monthly_eur:,.2f} EUR/mo")
            lines.append(f"  Concentration: Top-1 {cr.top1_share * 100:.1f}% | Top-3 {cr.top3_share * 100:.1f}% | HHI {cr.hhi:,.0f}")
            lines.append(f"  Overall Chain Verdict: [{cr.verdict}]" + (f" - {cr.verdict_reason}" if cr.verdict_reason else ""))

            # Validation tie-out
            tie_out_line = (
                f"  Sampled Tie-out (up to 20): Gas agree {cr.validation.gas_checks_passed}/{cr.validation.total_sampled} | "
                f"Transfer agree {cr.validation.transfer_checks_passed}/{cr.validation.total_sampled}"
            )
            if cr.validation.l1_fee_share is not None:
                tie_out_line += f" | Avg L1 fee share: {cr.validation.l1_fee_share * 100:.1f}%"
            lines.append(tie_out_line)

            # Size Buckets
            lines.append("  Breakdown by Debt Size Bucket:")
            for b_name in BUCKET_ORDER:
                b = cr.buckets.get(b_name)
                if not b:
                    continue
                lines.append(
                    f"    {b.bucket:7s} | Events: {b.event_count:4d} | Net: ${b.net_usd:9,.2f} ({b.net_eur:8,.2f} EUR, {b.avg_monthly_eur:8,.2f} EUR/mo) | "
                    f"Top-1: {b.top1_share*100:5.1f}% | Verdict: [{b.verdict}]" + (f" ({b.verdict_reason})" if b.verdict_reason else "")
                )
            lines.append("-" * 80)

        # Uncovered Chains
        lines.append("Uncovered Chains (Out of Scope):")
        for uc in self.uncovered_chains:
            lines.append(f"  - {uc['name']} (ID {uc['chain_id']}): {uc['reason']}")
        lines.append("-" * 80)

        return "\n".join(lines)

    def to_json(self) -> str:
        def decimal_default(obj):
            if isinstance(obj, Decimal):
                return str(obj)
            if hasattr(obj, "__dataclass_fields__"):
                return asdict(obj)
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

        return json.dumps(asdict(self), default=decimal_default, indent=2)


def generate_csv(items: list[ValuedLiquidation], eurusd: Decimal) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "chain_id",
        "block",
        "timestamp",
        "tx_hash",
        "log_index",
        "collateral",
        "debt",
        "user",
        "liquidator",
        "debt_to_cover",
        "collateral_amount",
        "collateral_usd",
        "debt_usd",
        "gross_usd",
        "gas_usd",
        "swap_cost",
        "flash_fee",
        "net_usd",
        "net_eur",
        "unpriced",
    ])
    for it in items:
        e = it.event
        net_eur = (it.net_usd / eurusd) if it.net_usd is not None else None
        writer.writerow([
            e.chain_id,
            e.block,
            e.timestamp,
            e.tx_hash,
            e.log_index,
            e.collateral,
            e.debt,
            e.user,
            e.liquidator,
            e.debt_to_cover,
            e.collateral_amount,
            str(it.collateral_usd) if it.collateral_usd is not None else "",
            str(it.debt_usd) if it.debt_usd is not None else "",
            str(it.gross_usd) if it.gross_usd is not None else "",
            str(it.gas_usd) if it.gas_usd is not None else "",
            str(it.swap_cost) if it.swap_cost is not None else "",
            str(it.flash_fee) if it.flash_fee is not None else "",
            str(it.net_usd) if it.net_usd is not None else "",
            str(net_eur) if net_eur is not None else "",
            1 if it.unpriced else 0,
        ])
    return output.getvalue()


def _build_bucket_reports(
    items: list[ValuedLiquidation],
    num_months: Decimal,
    eurusd: Decimal,
    threshold_eur: Decimal,
    anchor_ts: int,
    m_count: int,
) -> dict[str, BucketReport]:
    buckets_data: dict[str, list[ValuedLiquidation]] = {b: [] for b in BUCKET_ORDER}
    for it in items:
        if not it.unpriced and it.debt_usd is not None:
            b_name = bucket_for_debt(it.debt_usd)
            buckets_data[b_name].append(it)

    reports = {}
    for b_name in BUCKET_ORDER:
        b_items = buckets_data[b_name]
        gross = sum((it.gross_usd for it in b_items if it.gross_usd is not None), Decimal("0"))
        gas = sum((it.gas_usd for it in b_items if it.gas_usd is not None), Decimal("0"))
        net_u = sum((it.net_usd for it in b_items if it.net_usd is not None), Decimal("0"))
        net_e = net_u / eurusd
        avg_monthly_e = net_e / num_months

        conc = calculate_concentration(b_items)
        monthly = _monthly_net_eur(b_items, anchor_ts, m_count, eurusd)
        verdict, reason = evaluate_verdict(monthly, conc["top1_share"], threshold_eur)

        reports[b_name] = BucketReport(
            bucket=b_name,
            event_count=len(b_items),
            gross_usd=gross,
            gas_usd=gas,
            net_usd=net_u,
            net_eur=net_e,
            avg_monthly_eur=avg_monthly_e,
            top1_share=conc["top1_share"],
            top3_share=conc["top3_share"],
            hhi=conc["hhi"],
            verdict=verdict,
            verdict_reason=reason,
        )
    return reports


# Aave V3 liquidation bonuses are at most ~15%. Collateral worth more than the debt
# plus this margin, or a zero debt, is not a market liquidation (e.g. the April 2026
# rsETH governance liquidation after the KelpDAO hack).
ANOMALY_COLLATERAL_TO_DEBT = Decimal("1.2")


def _is_anomalous(it: ValuedLiquidation) -> bool:
    if it.unpriced or it.debt_usd is None or it.collateral_usd is None:
        return False
    return it.debt_usd <= 0 or it.collateral_usd > it.debt_usd * ANOMALY_COLLATERAL_TO_DEBT


def generate_report(
    chain_ids: list[int],
    from_blocks: dict[int, int],
    to_blocks: dict[int, int],
    store: Store,
    valued_events: dict[int, list[ValuedLiquidation]],
    validation_results: dict[int, ValidationResult],
    eurusd: Decimal,
    threshold_eur: Decimal = Decimal("300"),
    days: int = 90,
    end_ts: int | None = None,
) -> CensusReport:
    num_months = Decimal(days) / Decimal(30)
    chain_reports: list[ChainReport] = []

    for cid in chain_ids:
        fb = from_blocks[cid]
        tb = to_blocks[cid]

        gaps = store.covered(cid, fb, tb)
        if gaps:
            raise CoverageError(f"Coverage gap(s) detected for chain {cid} across [{fb}, {tb}]: {gaps}")

        all_items = valued_events.get(cid, [])
        anomalies = [
            {"tx_hash": it.event.tx_hash, "block": it.event.block, "collateral": it.event.collateral,
             "debt_usd": str(it.debt_usd), "collateral_usd": str(it.collateral_usd), "net_usd": str(it.net_usd)}
            for it in all_items if _is_anomalous(it)
        ]
        items = [it for it in all_items if not _is_anomalous(it)]
        v_res = validation_results.get(cid, ValidationResult(chain_id=cid))

        # Overall numbers
        event_count = len(items)
        distinct_liq = len({it.event.liquidator.lower() for it in items})
        unpriced = sum(1 for it in items if it.unpriced)

        gross = sum((it.gross_usd for it in items if it.gross_usd is not None), Decimal("0"))
        gas = sum((it.gas_usd for it in items if it.gas_usd is not None), Decimal("0"))
        net_u = sum((it.net_usd for it in items if it.net_usd is not None), Decimal("0"))
        net_e = net_u / eurusd
        avg_monthly_e = net_e / num_months

        # Months are consecutive 30-day periods ending at the window end; anchoring
        # at the last event's time would shift every month.
        max_ts = end_ts if end_ts is not None else max((it.event.timestamp for it in items), default=0)
        m_count = max(1, int(days // 30))

        conc = calculate_concentration(items)
        verdict, reason = evaluate_verdict(
            _monthly_net_eur(items, max_ts, m_count, eurusd), conc["top1_share"], threshold_eur
        )

        month_reports: list[MonthReport] = []
        for m_idx in range(m_count):
            m_end = max_ts - (m_idx * 30 * 86400)
            m_start = max_ts - ((m_idx + 1) * 30 * 86400)
            # Half-open (start, end]: an event on a boundary belongs to exactly one month.
            m_items = [it for it in items if m_start < it.event.timestamp <= m_end]

            m_gross = sum((it.gross_usd for it in m_items if it.gross_usd is not None), Decimal("0"))
            m_gas = sum((it.gas_usd for it in m_items if it.gas_usd is not None), Decimal("0"))
            m_net_u = sum((it.net_usd for it in m_items if it.net_usd is not None), Decimal("0"))
            m_net_e = m_net_u / eurusd

            m_conc = calculate_concentration(m_items)
            m_buckets = _build_bucket_reports(m_items, Decimal("1"), eurusd, threshold_eur, m_end, 1)

            month_reports.append(
                MonthReport(
                    month_index=m_idx + 1,
                    start_ts=m_start,
                    end_ts=m_end,
                    event_count=len(m_items),
                    gross_usd=m_gross,
                    gas_usd=m_gas,
                    net_usd=m_net_u,
                    net_eur=m_net_e,
                    top1_share=m_conc["top1_share"],
                    top3_share=m_conc["top3_share"],
                    hhi=m_conc["hhi"],
                    buckets=m_buckets,
                )
            )

        chain_buckets = _build_bucket_reports(items, num_months, eurusd, threshold_eur, max_ts, m_count)

        chain_reports.append(
            ChainReport(
                chain_id=cid,
                chain_name=CHAINS[cid].name if cid in CHAINS else f"Chain {cid}",
                from_block=fb,
                to_block=tb,
                covered=True,
                event_count=event_count,
                distinct_liquidators=distinct_liq,
                unpriced_count=unpriced,
                gross_usd=gross,
                gas_usd=gas,
                net_usd=net_u,
                net_eur=net_e,
                avg_monthly_eur=avg_monthly_e,
                top1_share=conc["top1_share"],
                top3_share=conc["top3_share"],
                hhi=conc["hhi"],
                verdict=verdict,
                verdict_reason=reason,
                months=month_reports,
                buckets=chain_buckets,
                validation=v_res,
                anomalies=anomalies,
            )
        )

    return CensusReport(
        chains=chain_reports,
        uncovered_chains=UNCOVERED_CHAINS,
        eurusd=eurusd,
        threshold_eur=threshold_eur,
        days=days,
    )
