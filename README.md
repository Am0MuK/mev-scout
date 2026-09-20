# mev-scout

A read-only measurement tool that answers three questions for any EVM chain:
**is there MEV money here, who takes it, and how long does an opportunity survive?**

It was built to settle one decision with data instead of opinion — *should a newcomer build
a DeFi arbitrage or liquidation bot in 2026?* — and it answered that question with **no**.
The tool, the method and the measurements are published here because the measurement is
reusable even though the answer was negative.

No private keys, no transactions, no smart contracts, no mempool, no bridge calls. Every
number comes from historical on-chain state read through public archive RPC and block
explorers.

---

## 1. The kill criterion

The pass/fail rule was fixed **before any data was collected**, which is the only way a
go/no-go measurement means anything:

> Build a bot only for a niche that earns at least **300 EUR net per month in at least two
> thirds of the months**, in a market where the **largest single competitor takes at most
> 50%** of the profit.

Everything below is measured against that rule.

---

## 2. What was measured, and what came out

Measured September 2026. Full write-up of the reasoning: [`docs/FINDINGS.md`](docs/FINDINGS.md).

### 2.1 Aave V3 liquidations — market exists, but it is a speed race

Whole-market net profit (every bot combined), not what a newcomer would capture:

| Chain | Window | Net, whole market | Liquidators | Top-1 share |
|---|---|---|---|---|
| Arbitrum One | 12 months | 2.77 M EUR | 276 | 27% |
| Ethereum | 90 days | 882 k EUR | 103 | 42% |
| Sonic | 12 months | 550 k EUR | 41 | 24% |
| Base | 90 days | 52.9 k EUR | 64 | 22% |
| Avalanche | 90 days | 40.4 k EUR | 27 | 39% |
| Polygon | 90 days | 28.1 k EUR | 44 | 38% |
| Optimism, Gnosis, Linea, zkSync, Scroll, Celo | 90 days | under 2 k EUR each | few | monopolised |

- **The money is in crashes.** On Arbitrum the top 5 days of the year are 66% of the annual
  profit; 10 October 2025 alone is 29%.
- **Outside crashes** (crash days removed, debt ≥ $1,000), Arbitrum leaves roughly
  **68,100 EUR per month for the entire market**, split across 87 active liquidators, median
  $116 per liquidation.
- Liquidations under $100 of debt are **net negative** after costs on every chain measured.
- **Aave V4** (Ethereum, live since March 2026): 201 liquidations in five months, $98 k gross,
  36 liquidators, top-1 41%, median bonus 4.49% — the Dutch auction does not produce large
  bonuses in practice. On Arc (mainnet 16 September 2026): zero liquidations in the first
  3.4 days. *Measured with an ad-hoc script against the V4 `Spoke` ABI, not with the CLI in
  this repo — see [`docs/FINDINGS.md`](docs/FINDINGS.md).*

### 2.2 Same-chain DEX arbitrage (Arbitrum, 90 days) — dead

- 28,482 atomic arbitrages detected across Uniswap V3, SushiSwap V3 and PancakeSwap V3:
  **$12,796 net in total**, median **$0.00**, p90 $0.08. About 70% of all profit comes from
  15 transactions.
- Independently cross-checked: across 85 sampled blocks over 7 days, **not one** round trip
  was still profitable one block later.

Competition has already ground this to cents.

### 2.3 Cross-chain inventory arbitrage (Arbitrum / Base / Optimism, WETH/USDC) — real, but rare

| Window | Opportunities | Still open after 5 min | Best single trade | Upper-bound total |
|---|---|---|---|---|
| Calm week | **0** of 1,026 evaluations | — | — | $0 |
| Crash 10.10.2025 | 52 | 39 | $1,075 (Arbitrum→Base, $50 k, 2.2%) | ~$5,400 |
| Crash 31.01.2026 | 11 | 5 | $1,613 (Base→Arbitrum, $50 k, 3.3%) | ~$1,660 |
| Crash 05.02.2026 | 5 | 0 | $115 | ~$41 |
| Crash 04.11.2025 | 0 | 0 | — | $0 |

In a normal week the best round trip is negative *before* gas (−0.025% at best, typically
−0.07%). This is the one mechanism measured where **opportunities last minutes rather than
milliseconds**, so latency is not the binding constraint — but they appear only during
crashes, only during *some* crashes, and they require $40 k–$200 k of inventory pre-positioned
on both chains and exposed while it waits.

### 2.4 Verdict

**No niche passes the criterion.** Same-chain arbitrage is dead; liquidations are a speed
race for scraps outside a handful of crash days; cross-chain gaps are real but too rare and
too capital-hungry to clear 300 EUR in two thirds of months. The recommendation the data
supports is to **not build the bot**.

### A caveat that matters

The CLI prints `PASS` for several of these markets. **That `PASS` only means the market is
large enough and not monopolised — it is not a claim that a newcomer would capture any of
it.** Capture depends on latency against incumbent bots, which this tool does not measure.
Read every verdict in the report as a statement about the market, never about you.

---

## 3. What the tool does

| Command group | Question it answers |
|---|---|
| `fetch` / `value` / `report` | Aave V3 liquidations on 12 chains: size, concentration, size buckets, verdict |
| `arb-pools` / `arb-fetch` / `arb-census` | What DEX arbitrage bots actually earned, reconstructed from historical `Swap` logs (2A) |
| `arb-sample` / `arb-report` | What was left on the table at sampled past blocks, quoted for real and tracked for persistence (2B) |
| `xchain-sample` / `xchain-report` | Cross-chain inventory arbitrage across Arbitrum, Base and Optimism (2C) |

Supported chains for the liquidation census: Arbitrum One (42161), Ethereum (1), Sonic (146),
Base (8453), Optimism (10), Polygon (137), Avalanche (43114), Gnosis (100), Linea (59144),
zkSync Era (324), Scroll (534352), Celo (42220). BNB Chain (56) is unsupported — no free log
source was found for it.

---

## 4. Installation

Requirements: Python ≥ 3.11.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

### Environment variables

- `ETHERSCAN_API_KEY` — Etherscan V2 API key (log fetching).
- `MEVSCOUT_RPC_<CHAINID>` — archive JSON-RPC endpoint per chain, e.g. `MEVSCOUT_RPC_42161`,
  `MEVSCOUT_RPC_146`.
- `MEVSCOUT_MAX_CPS` — client-side RPC pacing, calls per second (default 10, sized for a free
  Alchemy tier).

API keys and URL paths are never printed or logged, and are redacted from every error message.

### Free-tier limits worth knowing before a long run

- Alchemy's free tier allows roughly **10 calls/second** and only **10 blocks per
  `eth_getLogs`**. Do not run two heavy jobs against the same key.
- Etherscan V2's free tier **refuses Base, Optimism and BNB** (`status 0`, "Free API access is
  not supported for this chain"); those chains fall back to Blockscout/Routescan.
- Alchemy intermittently returns transient errors ("layer stale", 503) that must be retried.
  Every sampler here persists per block and resumes, because one unretried error once cost a
  109-minute run.

---

## 5. Quickstart

```bash
# Liquidation census: fetch, value, report
mev-scout fetch  --chain 42161 --days 90 --db data/scout.db
mev-scout value  --chain 42161 --db data/scout.db
mev-scout report --chain 42161 --eurusd 1.17 --threshold-eur 300

# Same-chain DEX arbitrage on Arbitrum
mev-scout arb-pools  --chain 42161 --db data/scout.db
mev-scout arb-fetch  --chain 42161 --days 90 --db data/scout.db
mev-scout arb-census --chain 42161 --days 90 --eurusd 1.1460

# Cross-chain, during a crash window
mev-scout xchain-sample --days 1 --every-min 10 --db data/xcrash.db
mev-scout xchain-report --eurusd 1.1460 --db data/xcrash.db
```

Exit codes: `0` report produced, `2` configuration, data-source or coverage error.

---
## 6. Reference: how the liquidation numbers are computed

All raw token math is performed using exact integer arithmetic. Valuation into money is performed strictly using `Decimal` (never floating-point).

For each event at its block `b`:
1. **Oracle Resolution**:
   - `Pool.ADDRESSES_PROVIDER()` (`0x0542975c`) → `provider.getPriceOracle()` (`0xfca513a8`)
   - `oracle.BASE_CURRENCY_UNIT()` (`0x8c89b64f`)
   - `oracle.getAssetPrice(asset)` (`0xb3596f07`)
   - Token `decimals()` (`0x313ce567`)
2. **USD Valuation**:
   - $\text{collateral\_usd} = \frac{\text{liquidatedCollateralAmount}}{10^{\text{dec}_c}} \times \frac{\text{price}_c}{\text{BASE\_UNIT}}$
   - $\text{debt\_usd} = \frac{\text{debtToCover}}{10^{\text{dec}_d}} \times \frac{\text{price}_d}{\text{BASE\_UNIT}}$
   - $\text{gross\_usd} = \text{collateral\_usd} - \text{debt\_usd}$
3. **Gas Cost**:
   - $\text{gas\_usd} = \frac{\text{gasUsed} \times \text{gasPrice}}{10^{18}} \times \frac{\text{price(wrapped native)}}{BASE\_UNIT}$
   - When multiple liquidation events share a transaction hash, the transaction gas is split equally among them.
4. **Newcomer Cost Assumptions**:
   - Swap cost: `--swap-cost 0.003` $\times \text{collateral\_usd}$
   - Flash loan fee: `--flash-fee 0.0005` $\times \text{debt\_usd}$
5. **Net Profit**:
   - $\text{net\_usd} = \text{gross\_usd} - \text{gas\_usd} - \text{swap\_cost} - \text{flash\_fee}$
   - $\text{net\_eur} = \frac{\text{net\_usd}}{\text{eurusd}}$
6. **Market Concentration**:
   - **Top-1 Share**: Share of total net profit captured by the largest liquidator. Top-1 share is a proxy for "the market is still contestable".
   - **Top-3 Share**: Share of total net profit captured by the top 3 liquidators.
   - **HHI (Herfindahl-Hirschman Index)**: $\sum (\text{share}_i \times 100)^2$ on a 0 to 10,000 scale.
7. **Size Buckets**:
   Metrics are broken down by debt size: `<100`, `100–1k`, `1k–10k`, `≥10k`.
8. **Verdict**:
   - **PASS**: Average monthly net profit $\ge \text{threshold\_eur}$ (default 300) **and** top-1 share $\le 50\%$.
   - **FAIL**: Otherwise, with the failing condition(s) explicitly named.

---
## 7. Reference: sampled tie-out validation

For a deterministic sample of up to 20 events per chain (fixed seed), the transaction receipt is fetched from the archive RPC to verify:
1. `receipt.gasUsed × receipt.effectiveGasPrice` matches the log's `gasUsed × gasPrice`.
2. Receipt contains an ERC-20 `Transfer` to `liquidator` of either the collateral token or its `aToken` (`Pool.getReserveData(asset)`) matching `liquidatedCollateralAmount`.

Any disagreement is displayed as a `WARNING` banner at the top of the report.

---

## 8. Reference: Phase 2 — DEX Arbitrage Census (Arbitrum One)

Phase 2 investigates DEX-to-DEX arbitrage on Arbitrum One (`chain_id 42161`) across Uniswap V3, SushiSwap V3, and PancakeSwap V3 across two complementary measurements:
- **2A — What bots actually earned**: Reconstruct past atomic arbitrage transactions from historical `Swap` logs.
- **2B — What was left on the table**: At sampled past blocks, check whether an executable round trip between pools was profitable after fees, price impact, and gas.

### Supported DEX Venues & Tokens

| DEX | Factory | QuoterV2 | Fee Tiers | Swap topic0 |
|---|---|---|---|---|
| Uniswap V3 | `0x1f98431c8ad98523631ae4a59f267346ea31f984` | `0x61ffe014ba17989e743c5f6cb21bf9697530b21e` | 100, 500, 3000, 10000 | `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67` |
| SushiSwap V3 | `0x1af415a1eba07a4986a52b6f2e7de7003d82231e` | `0x0524e833ccd057e4d7a296e3aaab9f7675964ce1` | 100, 500, 3000, 10000 | `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67` |
| PancakeSwap V3 | `0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865` | `0xb048bbc1ee6b733fffcfb9e9cef7375518e25997` | 100, 500, 2500, 10000 | `0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83` |

*Note on PancakeSwap*: PancakeSwap's `Swap` event includes two extra protocol fee parameters (`protocolFeesToken0`, `protocolFeesToken1`), resulting in a distinct `topic0`. Filtering by the Uniswap topic would silently drop PancakeSwap swaps.

**Tracked Tokens**: WETH, USDC, USDT, WBTC, ARB.
**Tracked Pairs**: WETH/USDC, WETH/USDT, WBTC/WETH, ARB/WETH, ARB/USDC.

### Phase 2 CLI Commands

```bash
# 1. Discover and record all active pools for tracked DEXes, pairs, and fee tiers
mev-scout arb-pools --chain 42161 [--db data/scout.db]

# 2. Fetch historical swap logs per pool with coverage tracking
mev-scout arb-fetch --chain 42161 --days 90 [--db data/scout.db]

# 3. 2A: Detect arbitrage cycles, value profit/gas, and output report & verdict
mev-scout arb-census --chain 42161 --days 90 --eurusd 1.1460 [--threshold-eur 300] [--json] [--csv data/arbs.csv]

# 4. 2B: Sample past blocks, prefilter mid-prices, execute round trips, and track persistence
mev-scout arb-sample --chain 42161 --days 30 [--every-min 10] [--dense FROM:TO ...] [--db data/scout.db]

# 5. 2B: Generate leftover opportunity report and upper bound monthly verdict
mev-scout arb-report --chain 42161 --eurusd 1.1460 [--threshold-eur 300] [--days 30] [--json]
```

### 2A Valuation & Verification Discipline
- **Atomic Cycle Condition**: Touches $\ge 2$ tracked pools; net flow across tracked swaps $\ge 0$ for all tokens and $> 0$ for at least one. Multi-hop plain swaps ending in another token are excluded.
- **Valuation**: Priced at that block using `sqrtPriceX96` against stable pools (USDC/USDT = 1 USD). Gas computed from transaction receipt (`gasUsed * effectiveGasPrice`).
- **Attribution & Concentration**: Attributed to transaction `from` (bot EOA) and `to` (contract). Monthly metrics include top-1 share, top-3 share, HHI, and size buckets (`<10`, `10–100`, `100–1k`, `≥1k`).
- **Validation**: Fixed-seed deterministic sample of 20 detected arbitrages recomputes net token flows from receipt ERC-20 `Transfer` events.

### 2B Prefilter & Quoting Rules
- **Prefilter**: Mid-price gap between pool pairs must strictly exceed combined fees ($gap > fee_A + fee_B$). Pairs exactly at or below the fee boundary are skipped without querying the quoter.
- **Round Trips**: Quoted on QuoterV2 at sizes of $1,000, $10,000, and $50,000 USD. Gas priced via base fee from `eth_getBlockByNumber` plus 100,000 overhead.
- **Reverts**: Reverted quotes are skipped and counted; never treated as zero.
- **Persistence**: Re-quotes at +1, +2, +5, and +20 blocks; stops at the first block without profit.
- **Shallow Pools**: Opportunities on pools with liquidity below 1,000,000,000 are explicitly flagged.

---

## 9. Reference: Phase 2C — Cross-Chain Inventory Arbitrage (Arbitrum, Base, Optimism)

Phase 2C censuses cross-chain inventory arbitrage opportunities between Arbitrum One (`42161`), Base (`8453`), and Optimism (`10`) for the WETH/USDC pair.

### Mechanism
The trader holds inventory in both WETH and USDC on each chain. When an executable cross-chain price gap exists between two chains:
1. Buy WETH with USDC on the cheaper chain.
2. Simultaneously sell WETH for USDC on the dearer chain for the exact WETH received.
3. Rebalancing is accounted for via parameterized per-trade costs.

### Venues & Adapters

| Chain | Chain ID | DEX Venues | Adapters & Selectors | Canonical Deep Pool |
|---|---|---|---|---|
| Arbitrum | 42161 | Uniswap V3, SushiSwap V3, PancakeSwap V3 | Uniswap V3 fee adapter (`0x1698ee82`, `0xc6a5026a`) | Uni V3 0.05% (`0xc6962004f452be9203591991d15f6b388e09e8d0`) |
| Base | 8453 | Uniswap V3, Slipstream 1, Slipstream Gauge Caps, Slipstream MinUnstake, Aerodrome Classic | Slipstream tickSpacing adapter (`0x28af8d0b`, `0x9e7defe6`); Classic Aerodrome adapter (`0x79bc57d5`, `0xf140a35a`) | Uni V3 0.01% (`0xb4cb800922cc596700c50d4f3b64c12ea85fa8ce`) |
| Optimism | 10 | Uniswap V3, Velodrome Slipstream, Aero CL | Uniswap V3 fee adapter; Slipstream tickSpacing adapter | Uni V3 0.30% (`0xc1738d90c0f3056157f44d8525b642674e2d2740`) |

### Methodology & Execution Rules

1. **Time Grid & Block Search**: Samples moments every 5 minutes over the window (default 30 days), plus every 1 minute inside `--dense FROM:TO` ranges. Binary search on `eth_getBlockByNumber` resolves the last block with timestamp $\le$ moment.
2. **Skew Guard**: Moments where any chain's block is $> 5$ seconds older than the moment are dropped and recorded.
3. **Mid-Price Prefilter**: Reads `slot0` of each chain's deepest canonical pool. A chain pair is skipped if relative mid-price gap $\le (fee_A + fee_B) / 10^6 + \text{min\_rebalance\_pct}$. Boundary condition is exact: equal gap is skipped.
4. **Best Executable Quotes & Shallow Check**: Queries all discovered pools on the chain at $1,000, $10,000, and $50,000 USD sizes. Requires chosen pool to quote $10\times$ size at no worse than 2% below $1\times$ price; shallow pools are flagged and skipped.
5. **Gas & L1 Fee Costs**: Execution gas = quoter `gasEstimate` + 100,000 overhead priced at the block's `baseFeePerGas`. On Base and Optimism, the L1 data fee is added via `GasPriceOracle(0x420000000000000000000000000000000000000F).getL1Fee(bytes)` with 400-byte calldata payload.
6. **Rebalance Costs**: Charged per trade via `--rebalance-pct` (default 0.05%) $\times \text{size}$ plus `--rebalance-fixed-usd` (default $1.00).
7. **Persistence & Executability**: Profitable gaps are re-checked at the next blocks on both chains (+2 s), at +1 minute, and at +5 minutes. Only gaps still profitable at the next blocks count as **executable**.
8. **Capital & RoC**: Capital required = $4 \times \text{size}$ (held as both USDC and WETH on both chains in the pair). Return on capital and monthly net profit are reported per pair and size.
9. **Resume & Durability**: Results are committed to SQLite after every sampled moment, resuming automatically on restart and logging progress every 10 moments.

### Phase 2C CLI Commands

```bash
# 1. Sample cross-chain moments across Arbitrum, Base, and Optimism
mev-scout xchain-sample [--days 30] [--every-min 5] [--dense FROM:TO ...] [--rebalance-pct 0.0005] [--rebalance-fixed-usd 1.00] [--db data/scout.db]

# 2. Generate report and monthly capital verdict
mev-scout xchain-report --eurusd 1.1460 [--threshold-eur 300] [--days 30] [--json] [--csv data/xchain_opps.csv] [--db data/scout.db]
```

---

## 10. What the live runs found that a green test suite did not

Every bug below passed the offline test suite and was caught only by running against real
chains. They are listed because they are the failure modes of *any* on-chain measurement
code, not because they are unique to this project.

- **Silent guesses instead of failures.** A hardcoded `WETH = $2,600` fallback, `decimals`
  defaulting to 18, gas silently set to 0 on a missing receipt field, and a substituted base
  fee of 0.1 gwei when the real one was 0. Each produced a plausible number from no data.
  Fixed by marking values `unpriced`, counting them, and excluding them from totals — a
  missing price is never zero.
- **Network errors counted as data.** Broad `except` blocks turned RPC outages into "no
  opportunity found" and transport errors into dropped pools. Only genuine contract reverts
  are skipped now, and they are counted; every transport error propagates or retries.
- **Reports produced from zero data.** A report over an empty sample still rendered a verdict.
  The reporters now refuse to emit a verdict when nothing was sampled.
- **A verdict that bypassed its own rule.** The 2C verdict was fed one averaged month, which
  would have let a single crash pass the "two thirds of months" criterion. Fixed to evaluate
  real 30-day months.
- **Invented contract addresses.** An agent handed a truncated address in a spec
  (`0xb4cb8009…`) completed it with plausible invented characters; every chain pair was then
  skipped silently rather than erroring. Never pass truncated addresses to anything.
- **Shallow pools quote absurdly without erroring.** A Sushi 0.01% pool quoted 1 WETH → 9.14
  USDC against a real price near $2,648. Mid-price comparison alone manufactures opportunities;
  every candidate pool is now depth-checked (10× size must quote within 2% of 1× price).
- **A wrong event topic drops data in silence.** PancakeSwap's `Swap` event carries two extra
  protocol-fee parameters and therefore a different `topic0`; filtering by the Uniswap topic
  silently discards every PancakeSwap swap.

---

## 11. Limitations & non-goals

- **Observational only**: a liquidation census for Aave V3 across 12 chains, atomic DEX arbitrage on Arbitrum One (2A/2B), and cross-chain inventory arbitrage across Arbitrum, Base and Optimism (2C). Nothing here trades.
- **Protocol coverage**: Aave V3 only. Aave V4 was measured separately with an ad-hoc script and is not supported by this CLI.
- **Venue coverage**: Uniswap V3, SushiSwap V3 and PancakeSwap V3 (plus Aerodrome/Velodrome Slipstream venues on Base and Optimism for 2C). Camelot, Uniswap V4, Balancer and others are not tracked, so same-chain arbitrage figures are a lower bound.
- **Market, not capture**: a `PASS` verdict means the market is large enough and not monopolised. It is not a measurement of what a newcomer would win — that depends on latency against incumbent bots, which this tool does not measure.
- **No active execution**: no private keys, no trade execution, no bridge calls, no mempool listeners, no CEX data.
- **Upper bound**: reports state explicitly that results are an upper bound on potential earnings. Live execution adds latency, partial fills, gas-bidding competition and inter-leg price risk.
- **RPC and error discipline**: transport errors propagate or retry; only `ContractCallError` reverts are skipped, and they are counted; missing data is marked `unpriced` and counted, never treated as zero.

---

## 12. Status and licence

The measurement programme is complete and the project is **archived as a research tool**:
the question it was built to answer has been answered. Issues and forks are welcome; no
roadmap is planned. The one extension that would still add information is a live watch
during the next crash — recording, with no capital at risk, whether a newcomer could have
captured anything — sketched in [`docs/PHASE3-live-observatory.md`](docs/PHASE3-live-observatory.md).

Licensed under the MIT Licence; see [`LICENSE`](LICENSE).
