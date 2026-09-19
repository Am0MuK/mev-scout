# mev-scout Phase 2C — cross-chain price-gap census (inventory arbitrage)

Runs after 2A and 2B. Owner observation: price gaps between chains are the ones that "work
best". This measures whether they survive all costs, how long they last, and how much capital
they need.

## Mechanism measured

Not atomic. The trader holds inventory (USDC and WETH) on each chain, buys where WETH is
cheaper and sells where it is dearer at the same moment, and periodically rebalances through a
bridge. Profit per trade = executable sell proceeds − executable buy cost − gas on both chains −
the trade's share of rebalancing cost. Risks the report states: price moves between the two
legs, bridge and stablecoin risk (native vs bridged USDC), capital tied up on every chain.

## Venues (probed live 2026-09-19, `latest` block)

| Chain | DEX | Factory | Quoter | Pool key | Deep WETH/USDC pool | 10 WETH → USDC |
|---|---|---|---|---|---|---|
| Arbitrum 42161 | Uniswap V3 (+ Sushi, Pancake from 2A/2B) | see SPEC-phase2 | see SPEC-phase2 | fee | fee 500 `0xc6962004…` | (2A/2B) |
| Base 8453 | Uniswap V3 | `0x33128a8fc17869897dce68ed026d694621f6fdfd` | `0x3d4e44eb1374240ce5f1b871ab261cd16335b76a` | fee | fee 100 `0xb4cb8009…` | 26,105 |
| Base 8453 | Aerodrome Slipstream | `0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a` | `0x254cf9e1e6e233aa1ac962cb9b05b2cfeaae15b0` | **tickSpacing** | tickSpacing 100 `0xb2cc224c…` | 26,448 |
| Optimism 10 | Uniswap V3 | `0x1f98431c8ad98523631ae4a59f267346ea31f984` | `0x61ffe014ba17989e743c5f6cb21bf9697530b21e` | fee | fee 3000 `0xc1738d90…` | 26,409 |

Every quoter answered `factory()` with the factory listed. Tokens: WETH on Base and Optimism
`0x4200000000000000000000000000000000000006`; native USDC Base
`0x833589fcd6edb6e08f4c7c32d4f71b54bda02913`, Optimism
`0x0b2c639c533813f4aa9d7837caf62653d097ff85`, Arbitrum
`0xaf88d065e77c8cc2239327c5edb3a432268e5831`.

Aerodrome Slipstream uses `getPool(address,address,int24 tickSpacing)` `0x28af8d0b` and
`quoteExactInputSingle((address,address,uint256,int24 tickSpacing,uint160))` `0x9e7defe6`
— a separate adapter; do not reuse the Uniswap fee-tier path. Tick spacings to discover:
1, 50, 100, 200.

Velodrome Slipstream (Optimism), probed 2026-09-19: factory
`0xcc0bddb707055e04e497ab22a59c2af4391cd12f`, quoter `0x89d8218ed5ff1e46d8dcd33fb0bbee3be1621466`
(`factory()` matches), same interface as Aerodrome. tickSpacing 100 pool `0x478946bc…`:
10 WETH → 26,382 USDC (about as deep as Uniswap 0.3%). tickSpacing 1 pool exists but its quote
reverts with `execution reverted: Unexpected error` (code 3) — a revert, skipped and counted.
Include Velodrome.

## Live facts the code must respect

- Shallow pools again return absurd quotes without an error: Optimism Uniswap 0.01%
  1 WETH → 13.95 USDC; Aerodrome tickSpacing 200 1 WETH → 546, 10 WETH → 546. Per chain and
  size, use the **best executable quote across all discovered pools**, and require the chosen
  pool to quote 10× the size at no worse than 2% below the 1× price (else flag it shallow and
  skip it).
- Public RPCs throttle hard (`mainnet.base.org` HTTP 429 after a few calls; `base.llamarpc.com`
  HTTP 525) and keep no history. Use Alchemy archive endpoints (`MEVSCOUT_RPC_<CHAINID>`) with
  the existing batching and back-off; one heavy job per key.

## Method

1. **Time grid.** Every 5 minutes over the window (default 30 days), plus every minute inside
   `--dense FROM:TO` ranges (crash days from the liquidation census). For each moment and chain,
   the last block with timestamp ≤ the moment (binary search on `eth_getBlockByNumber` timestamps,
   cached). Record the chain skew (timestamp difference between the chosen blocks); drop moments
   where any chain's block is more than 5 s older than the moment.
2. **Prefilter.** Read `slot0` of each chain's deepest pool (one call per chain). If the
   mid-price gap between two chains is not larger than both pools' fees plus the minimum
   rebalancing cost, skip that chain pair for this moment and count it.
3. **Executable quotes.** For pairs that pass: on the cheaper chain, quote USDC → WETH; on the
   dearer chain, quote WETH → USDC for the WETH received; sizes 1,000, 10,000, 50,000 USD.
4. **Costs.** Gas per leg = quoter `gasEstimate` + 100,000 overhead at that block's base fee
   (+ L1 data fee for Base and Optimism: `GasPriceOracle`
   `0x420000000000000000000000000000000000000F` `getL1Fee(bytes)` `0x49948e0e` for a
   400-byte calldata; probed 2026-09-19: Optimism 4,678,797,641 wei, Base 2,880,749,262 wei —
   about 1e-5 USD, negligible today but read it anyway so a fee spike is not missed). Rebalancing: `--rebalance-pct` (default 0.05%) of
   the size plus `--rebalance-fixed-usd` (default 1.00), charged per trade.
5. **Persistence.** For each profitable gap, re-check the same pair and size at +1 and +5
   minutes (and at +2 s, the next blocks on both chains) and record how long it lasted. Only
   gaps still profitable at the next blocks on both chains count as **executable**.
6. **Capital.** Required inventory = the largest size traded, held as both USDC and WETH on
   every chain in the pair. Report profit per month and as a percentage of that capital.
7. **Who already does it (optional, cheap).** From 2A's swap data on Arbitrum and the same
   pools on Base/Optimism, list addresses whose EOA traded WETH/USDC on two chains within the
   same minute in opposite directions; report counts and volumes.

## Report and verdict

Per chain pair and size: share of moments with an executable gap, net per trade (median, p90,
max), persistence distribution, monthly net (EUR), capital required, return on capital. Verdict
with the Phase 1 rule (≥ 300 EUR net in two thirds of months) **and** the capital needed printed
next to it. The report states that results are an upper bound: real execution adds latency,
partial fills and the price risk between legs.

## Non-goals

No execution, no keys, no bridge calls, no CEX data. Chains beyond these three only after 2C
is verified.
