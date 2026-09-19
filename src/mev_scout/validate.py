"""Validation module for sampled tie-out checks on receipts."""

from dataclasses import dataclass, field
import random

from mev_scout.chains import CHAINS, ConfigError
from mev_scout.decode import Liquidation
from mev_scout.rpc import RpcClient

ERC20_TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
GET_RESERVE_DATA_SELECTOR = "0x35ea6a75"


@dataclass
class ValidationResult:
    chain_id: int
    total_sampled: int = 0
    gas_checks_passed: int = 0
    gas_checks_failed: int = 0
    gas_disagreements: list[str] = field(default_factory=list)
    transfer_checks_passed: int = 0
    transfer_checks_failed: int = 0
    transfer_checks_not_run: int = 0
    not_run_reasons: list[str] = field(default_factory=list)
    transfer_disagreements: list[str] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return (self.gas_checks_failed > 0) or (self.transfer_checks_failed > 0)


def validate_chain(
    events: list[Liquidation],
    chain_id: int,
    rpc: RpcClient,
    seed: int = 42,
    max_sample: int = 20,
) -> ValidationResult:
    if chain_id not in CHAINS:
        raise ConfigError(f"Unsupported chain id: {chain_id}")

    pool = CHAINS[chain_id].pool
    result = ValidationResult(chain_id=chain_id)

    if not events:
        return result

    # Deterministic sample of up to max_sample events
    sorted_events = sorted(events, key=lambda e: (e.block, e.log_index, e.tx_hash))
    sample_size = min(len(sorted_events), max_sample)
    rng = random.Random(seed)
    sampled = rng.sample(sorted_events, sample_size)
    result.total_sampled = sample_size

    for e in sampled:
        receipt = rpc.receipt(e.tx_hash)

        # 1. Receipt gas check
        rcpt_gas_used = int(str(receipt.get("gasUsed", "0x0")), 0)
        eff_price_raw = receipt.get("effectiveGasPrice") or receipt.get("gasPrice") or "0x0"
        rcpt_gas_price = int(str(eff_price_raw), 0)

        rcpt_product = rcpt_gas_used * rcpt_gas_price
        event_product = e.gas_used * e.gas_price

        if rcpt_product == event_product:
            result.gas_checks_passed += 1
        else:
            result.gas_checks_failed += 1
            result.gas_disagreements.append(
                f"tx {e.tx_hash}: receipt gasUsed*price ({rcpt_product}) != log gasUsed*price ({event_product})"
            )

        # 2. Collateral Transfer check (underlying or aToken)
        reserve_data_call = GET_RESERVE_DATA_SELECTOR + "0" * 24 + e.collateral.removeprefix("0x").lower()
        raw_reserve = rpc.call(to=pool, data=reserve_data_call, block=e.block)
        clean_reserve = raw_reserve.removeprefix("0x").removeprefix("0X")

        # Must be 15 32-byte words (960 hex chars)
        if len(clean_reserve) != 15 * 64:
            result.transfer_checks_not_run += 1
            result.not_run_reasons.append(
                f"tx {e.tx_hash}: getReserveData returned {len(clean_reserve)//64} words, expected 15"
            )
            continue

        atoken_addr = "0x" + clean_reserve[8 * 64 : 9 * 64][-40:].lower()
        valid_emitters = {e.collateral.lower(), atoken_addr.lower()}
        target_liquidator = e.liquidator.removeprefix("0x").lower()

        found_transfer = False
        for log in receipt.get("logs", []):
            emitter = str(log.get("address", "")).lower()
            if emitter not in valid_emitters:
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            if topics[0].lower() != ERC20_TRANSFER_TOPIC0:
                continue

            to_addr = str(topics[2])[-40:].lower()
            if to_addr != target_liquidator:
                continue

            amount = int(str(log.get("data", "0x0")), 16)
            if amount == e.collateral_amount:
                found_transfer = True
                break

        if found_transfer:
            result.transfer_checks_passed += 1
        else:
            result.transfer_checks_failed += 1
            result.transfer_disagreements.append(
                f"tx {e.tx_hash}: no Transfer of collateral or aToken to {e.liquidator} for amount {e.collateral_amount}"
            )

    return result
