# mev-scout

Phase 1 liquidation opportunity census for Aave V3 on Arbitrum One and Sonic.

---

## 1. Purpose & The Kill Criterion

Before writing any bot, smart contract, or deploying capital: on which chain and which debt size range do Aave V3 liquidations leave at least **300 EUR per month** of estimated net profit, in a market where **one liquidator does not take almost everything**? If none does, we stop.

This phase is strictly read-only. No keys, no transactions, no contracts, no mempool, and no external state modification.

---

## 2. Scope & Chains

### Supported Chains

| Chain | Chain ID | Aave V3 Pool | Wrapped Native (Gas Pricing) |
|---|---|---|---|
| Arbitrum One | 42161 | `0x794a61358d6845594f94dc1db02a252b5b4814ad` | WETH `0x82af49447d8a07e3bd95bd0d56f35241523fbab1` |
| Sonic | 146 | `0x5362dbb1e601abf3a4c14c22ffeda64042e5eaa3` | wS `0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38` |

At run start, each chain is verified on-chain to confirm that its wrapped-native token is registered in `Pool.getReservesList()`. If not, execution aborts with exit code 2.

### Uncovered Chains (Out of Scope)

- **Base** (`8453`): Etherscan V2 free tier refuses it (`status 0`, "Free API access is not supported for this chain"), and Alchemy free tier limits `eth_getLogs` to 10 blocks.
- **Optimism** (`10`): Etherscan V2 free tier refuses it (`status 0`, "Free API access is not supported for this chain"), and Alchemy free tier limits `eth_getLogs` to 10 blocks.

The census report explicitly lists both covered and uncovered chains.

---

## 3. Installation & Setup

Requirements:
- Python ≥ 3.11
- `httpx`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

### Environment Variables

The CLI expects standard environment variables for Etherscan V2 and chain RPC endpoints:
- `ETHERSCAN_API_KEY`: API key for Etherscan V2.
- `MEVSCOUT_RPC_<CHAINID>`: JSON-RPC archive endpoint for each chain, e.g.:
  - `MEVSCOUT_RPC_42161` for Arbitrum One
  - `MEVSCOUT_RPC_146` for Sonic

Sensitive API keys and URL paths are never printed or logged, and are redacted from all error messages.

---

## 4. Usage

### 1. Fetch liquidation events

Fetch historical `LiquidationCall` event logs from Etherscan V2 in chunks of at most 500,000 blocks and store them in SQLite:

```bash
mev-scout fetch --chain 42161 --days 90 [--db data/scout.db]
mev-scout fetch --chain 146 --days 90 [--db data/scout.db]
```

### 2. Value liquidations

Compute historical valuations for all stored liquidations using on-chain Aave V3 price oracles at each event's block number:

```bash
mev-scout value --chain 42161 [--db data/scout.db] [--swap-cost 0.003] [--flash-fee 0.0005]
mev-scout value --chain 146 [--db data/scout.db] [--swap-cost 0.003] [--flash-fee 0.0005]
```

Every `eth_call` result is cached locally in SQLite `call_cache` keyed by `(chain, to, data, block)`.

### 3. Generate census report

Verify coverage without gaps, run sampled receipt validation, compute concentration metrics, and output the verdict:

```bash
# Text report to stdout
mev-scout report --chain 42161 --chain 146 --eurusd 1.17 [--threshold-eur 300] [--days 90]

# Machine-readable JSON output
mev-scout report --chain 42161 --chain 146 --eurusd 1.17 --json

# Export events CSV
mev-scout report --chain 42161 --chain 146 --eurusd 1.17 --csv data/events.csv
```

Exit codes:
- `0`: Report produced successfully.
- `2`: Configuration, data-source, or coverage error.

---

## 5. How Numbers Are Computed

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

## 6. Sampled Tie-Out Validation

For a deterministic sample of up to 20 events per chain (fixed seed), the transaction receipt is fetched from the archive RPC to verify:
1. `receipt.gasUsed × receipt.effectiveGasPrice` matches the log's `gasUsed × gasPrice`.
2. Receipt contains an ERC-20 `Transfer` to `liquidator` of either the collateral token or its `aToken` (`Pool.getReserveData(asset)`) matching `liquidatedCollateralAmount`.

Any disagreement is displayed as a `WARNING` banner at the top of the report.

---

## 7. Phase 2: DEX Arbitrage Census (Arbitrum One)

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

## 8. Phase 2C: Cross-Chain Inventory Arbitrage (Arbitrum, Base, Optimism)

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

## 9. Limitations & Non-Goals

- **Phase 1 Scope**: Strictly observational liquidation census for Aave V3 on Arbitrum One and Sonic.
- **Phase 2 Scope**: Strictly observational atomic DEX arbitrage on Arbitrum One (2A/2B), and cross-chain inventory arbitrage across Arbitrum, Base, and Optimism (2C).
- **No Active Execution**: No private keys, no trade execution, no bridge calls, no mempool listeners, no CEX data.
- **Upper Bound Nature**: The report explicitly states that results represent an upper bound of potential earnings: live execution introduces latency, partial fills, gas bidding competition, and inter-leg price risk.
- **RPC & Error Discipline**: Transport errors propagate; only `ContractCallError` reverts are skipped and counted; missing data is marked unpriced and counted.
