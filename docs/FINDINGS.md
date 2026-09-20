# Findings — what mev-scout measured, September 2026

This is the full record behind the summary in the [README](../README.md): the numbers, how
they were obtained, what was cross-checked, and what each result does *not* prove.

All measurements are read-only, from historical on-chain state via public archive RPC
(Alchemy), Etherscan V2, Blockscout and Routescan. No keys, no transactions, no mempool.

**The kill criterion, fixed before any data was collected:** build a bot only for a niche
earning at least **300 EUR net per month in at least two thirds of the months**, in a market
where the **largest single competitor takes at most 50%**.

---

## 1. Aave V3 liquidations

### 1.1 Twelve months, Arbitrum One and Sonic

Costs modelled for a newcomer: real gas from receipts, a 0.3% collateral swap cost and a
0.05% flash-loan fee.

| | Arbitrum One | Sonic |
|---|---|---|
| Events (clean) | 11,112 | 4,209 |
| Liquidators | 276 | 41 |
| Net, 12 months | $3.18 M (2.77 M EUR) | $631 k (550 k EUR) |
| Top-1 share | 26.9% | 23.9% |
| Top-3 share | 45.9% | — |

Size buckets by debt: `<$100` is **net negative** on both chains. Arbitrum `$100–1k` passes
only weakly (80% of it falls in two months); `$1k–10k` and `≥$10k` pass. Sonic fails both
`<100` and `$100–1k` (7 of 12 months), passes `$1k–10k` and `≥$10k` (the latter at exactly
8 of 12 months).

**Concentration in time.** Arbitrum's top 5 days are 66% of the year's profit, and
10 October 2025 alone is 29%. On Sonic that single day is 53% of the year.

**With crash days removed** and debt ≥ $1,000 — the honest picture of a normal month:

| | Arbitrum | Sonic |
|---|---|---|
| Whole-market net per month | ~68,100 EUR | ~6,300 EUR |
| Active liquidators | 87 | 19 |
| Top-1 / top-3 share | 26% / 58% | 22% / — |
| Median net per liquidation | $116 | — |

**One anomaly, excluded.** 23 Arbitrum events showed $90.5 M of "net profit", almost all in
one transaction at block 460,068,736: the governance liquidation of the KelpDAO rsETH
attacker after the 18 April 2026 hack, where Aave deliberately raised the rsETH oracle price.
A filter now excludes events with zero debt or collateral exceeding debt by more than 1.2×,
and lists them separately rather than dropping them silently. Sonic had two small analogous
events on 10 October 2025.

### 1.2 Ten further chains, 90 days (21 June – 19 September 2026)

| Chain | Net, 90 days | Liquidators | Top-1 | Verdict |
|---|---|---|---|---|
| Ethereum | 882 k EUR | 103 | 42% | PASS (≥$10k bucket alone is 868 k; `<100` negative) |
| Base | 52.9 k EUR | 64 | 22% | PASS |
| Avalanche | 40.4 k EUR | 27 | 39% | PASS (`$1k–10k`, `≥$10k`) |
| Polygon | 28.1 k EUR | 44 | 38% | PASS (`$1k–10k`, `≥$10k`) |
| Optimism | 1.2 k EUR | 31 | 44% | FAIL — 843 of 863 events are under $100 |
| Gnosis | 2.1 k EUR (one event) | — | — | FAIL |
| Linea | 395 EUR | — | — | FAIL |
| zkSync Era | 56 EUR | — | — | FAIL |
| Scroll | 9 EUR | — | — | FAIL |
| Celo | 1 EUR | — | — | FAIL |

Notes: on Scroll the L1 data fee is 1,165% of the execution fee, so gas is badly
underestimated there — irrelevant at that size, but it would matter on a chain worth trading.
Celo required a retry through a Blockscout 429.

### 1.3 Aave V4 (Ethereum and Arc)

Measured with an ad-hoc script against the V4 `Spoke` ABI, **not** with the CLI in this repo.
V4 `LiquidationCall` has `topic0`
`0x2a1f12d996f530f89d8038aa293f9fde81cac44b6dfd6225e3358d09b78a4a37` (from
`aave/aave-v4 src/spoke/interfaces/ISpoke.sol`, including the `PremiumDelta` tuple); pricing
goes through `Spoke.ORACLE()` → `getReservePrice(uint256)`, with decimals from word 3 of
`getReserve(uint256)`. The liquidator's share of collateral is
`removed × sharesToLiquidator / sharesLiquidated`.

- **Ethereum** (17 April – 18 September 2026): 201 liquidations across 6 Spokes, $98 k gross
  ($59 k of it in June), 2.06 ETH of gas, 36 liquidators, top-1 41%, top-3 68%, median bonus
  **4.49%** — the Dutch auction does not produce large bonuses in practice. No anomalies.
- **Arc** (chain 5042, mainnet since 16 September 2026): **zero** liquidations in 579,409
  blocks (~3.4 days).

### 1.4 What the liquidation result does and does not say

The market clears the size and concentration tests on six chains. That is a statement about
the market, **not** about a newcomer: liquidations are won in the same block, so entering
means a latency race against 87 incumbent bots on Arbitrum alone. What share a newcomer would
capture is not measured here and cannot be inferred from these numbers.

---

## 2. Same-chain DEX arbitrage (Arbitrum One, 90 days)

Venues: Uniswap V3, SushiSwap V3, PancakeSwap V3. Pairs: WETH/USDC, WETH/USDT, WBTC/WETH,
ARB/WETH, ARB/USDC. 9.48 M swaps fetched from 59 pools (Uniswap 6.53 M, PancakeSwap 2.68 M,
Sushi 0.27 M).

### 2A — what bots actually earned

Atomic arbitrage cycles reconstructed from historical `Swap` logs, gas from real receipts:

- **28,482 arbitrages**: 26,241 across two pools, 2,172 across three.
- **$13,326 gross, $530.57 gas, $12,796 net** (11,166 EUR) over 90 days.
- Median net **$0.00**, p90 $0.08, max $3,373. 27,971 of them are under $1.
- Concentration: top-1 bot 27.4%, top-3 47.7%.
- By size: `<$10` — 28,397 arbitrages worth $1,850 in total; `$10–100` — 70 worth $2,212;
  `$100–1k` — 13 worth $4,359; `≥$1k` — **2 worth $4,375**.
- Monthly net (newest first): 2,642 / 965 / 7,558 EUR, top-1 share 32.7% / 60.1% / 39.8%.

The CLI verdict is `PASS` (≥300 EUR in 3 of 3 months, whole market) and that verdict is
misleading on its own: roughly **70% of the net comes from 15 transactions**, and the bulk of
the distribution is literally cents.

### 2B — what was left on the table

Rather than reconstructing the past, 2B asks whether an executable round trip was still
profitable at sampled past blocks: `slot0` mid-price prefilter, then real QuoterV2 round trips
at $1,000 / $10,000 / $50,000, gas from the block's base fee, persistence re-quoted at +1, +2,
+5 and +20 blocks.

Across **85 sampled blocks over 7 days, not one** round trip was still profitable at the next
block.

### 2C caveat on scope

Only Uniswap V3, SushiSwap V3 and PancakeSwap V3 pools are tracked. Routes through Camelot,
Uniswap V4, Balancer and others are not, so these figures are a **lower bound** on total
same-chain arbitrage — but a lower bound of cents is still cents on the venues that carry most
of the volume.

---

## 3. Cross-chain inventory arbitrage (Arbitrum / Base / Optimism, WETH/USDC)

The mechanism: hold WETH and USDC on both chains, buy WETH where it is cheap and
simultaneously sell it where it is dear, with rebalancing charged as a per-trade cost
(0.05% of size plus $1.00 fixed). No bridge is in the critical path, so the trade is not
latency-bound the way an atomic arbitrage is.

Costs include the L1 data fee on Base and Optimism via
`GasPriceOracle.getL1Fee(bytes)` with a 400-byte payload. A gap counts as **executable** only
if it is still profitable when re-quoted at the next block on both chains.

### Calm week

1,026 evaluations, **0 profitable**. The best round trip was negative *before* gas: −0.025%
at best, typically −0.07%.

### Crash windows (the four largest crash days of the year, sampled every 10–15 minutes)

| Crash | Opportunities | Still open after 5 min | Best single trade | Upper-bound total |
|---|---|---|---|---|
| 10 October 2025 | 52 | 39 | $1,075 — Arbitrum→Base, $50 k, 2.2% | ~$5,400 |
| 31 January 2026 | 11 | 5 | $1,613 — Base→Arbitrum, $50 k, 3.3% | ~$1,660 |
| 5 February 2026 | 5 | 0 | $115 | ~$41 |
| 4 November 2025 | 0 | 0 | — | $0 |

### Reading

The hypothesis that cross-chain gaps "work best" is **half right**. The gaps are real and,
uniquely among everything measured here, they **persist for minutes** — 39 of 52 opportunities
in the October crash were still open five minutes later. Speed is therefore not the binding
constraint.

But they appear only during crashes, only during *some* crashes (two of the four produced
almost nothing), and capturing them requires $40,000–$200,000 of inventory sitting
pre-positioned and exposed on both chains while it waits. At roughly 4–6 crashes a year, most
months earn **zero**, which fails "300 EUR in two thirds of months" decisively.

Two of four crashes producing nothing is also a warning about sample size: any future claim
about this mechanism needs several crashes, not one.

**Known limitation:** Arbitrum gas here is `quoter gasEstimate × baseFee`, which excludes
Arbitrum's L1 component (Arbitrum folds it into `gasUsed` on real receipts). The Arbitrum leg
is therefore underestimated, by cents.

---

## 4. Conclusion

**No niche passes the criterion set in advance.**

- **Same-chain arbitrage:** dead. Competed down to cents; nothing survives one block.
- **Liquidations:** the market exists and is not monopolised, but outside a handful of crash
  days it is a speed race for a median of $116 against dozens of incumbents.
- **Cross-chain:** the most interesting mechanism found — minutes, not milliseconds — but rare,
  capital-hungry, and concentrated into a few minutes a year.

The decision the data supports is to **not build the bot**, exactly as the criterion says.

The measurement itself is the durable output: for any chain, it answers whether there is money
there, who is taking it, and how long an opportunity survives. The one extension that would add
real information is a **live watch during the next crash**, recording with no capital at risk
whether a newcomer could have captured anything — sketched in
[`PHASE3-live-observatory.md`](PHASE3-live-observatory.md).
