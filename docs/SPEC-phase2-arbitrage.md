# mev-scout Phase 2 — DEX arbitrage census (Arbitrum)

## Question

Is there DEX-to-DEX arbitrage on Arbitrum that a newcomer could earn **at least 300 EUR net in
at least two thirds of the 30-day months**, in a market where **one bot does not take more
than half**? Same kill criterion as the liquidation census (SPEC.md, owner-approved
2026-09-19). Read-only: no keys, no transactions, no contracts.

Two measurements, because they answer different questions:

- **2A — what bots actually earned.** Reconstruct past atomic arbitrage transactions from
  swap events. This is the money that exists, and who takes it.
- **2B — what was left on the table.** At sampled past blocks, check whether an executable
  round trip between two pools was profitable after fees, price impact and gas. Anything
  found here survived the block, so a slower bot could have taken it. This also tests the
  owner's observation that price gaps are often visible by hand.

Out of scope: CEX–DEX arbitrage (needs inventory on an exchange), cross-chain arbitrage
(not atomic), sandwiching and front-running (will not be built), Sonic (next, after its DEXes
are probed).

## Venues (probed live 2026-09-19 on Arbitrum)

All three are Uniswap-V3-style. Each QuoterV2 answered `factory()` with the factory below.

| DEX | Factory | QuoterV2 | Fee tiers | Swap topic0 |
|---|---|---|---|---|
| Uniswap V3 | `0x1f98431c8ad98523631ae4a59f267346ea31f984` | `0x61ffe014ba17989e743c5f6cb21bf9697530b21e` | 100, 500, 3000, 10000 | `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67` |
| SushiSwap V3 | `0x1af415a1eba07a4986a52b6f2e7de7003d82231e` | `0x0524e833ccd057e4d7a296e3aaab9f7675964ce1` | 100, 500, 3000, 10000 | same as Uniswap |
| PancakeSwap V3 | `0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865` | `0xb048bbc1ee6b733fffcfb9e9cef7375518e25997` | 100, 500, 2500, 10000 | **`0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83`** |

PancakeSwap's `Swap` has two extra fields (`protocolFeesToken0`, `protocolFeesToken1`), so its
topic0 differs; filtering on the Uniswap topic silently drops every PancakeSwap swap
(verified: 314 of 338 PancakeSwap pool logs in a sample used the other topic).

Selectors (keccak computed locally): `getPool(address,address,uint24)` `0x1698ee82`,
`factory()` `0xc45a0155`, `quoteExactInputSingle((address,address,uint256,uint24,uint160))`
`0xc6a5026a` (returns `amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate`),
`slot0()` `0x3850c7bd`, `liquidity()` `0x1a686502`, `token0()` `0x0dfe1681`.

Tokens (Arbitrum): WETH `0x82af49447d8a07e3bd95bd0d56f35241523fbab1`, USDC
`0xaf88d065e77c8cc2239327c5edb3a432268e5831`, USDT `0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9`,
WBTC `0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f`, ARB `0x912ce59144191c1204e64559fe8253a0e49e6548`.
Pairs: WETH/USDC, WETH/USDT, WBTC/WETH, ARB/WETH, ARB/USDC.

Pool discovery at run start: `factory.getPool(tokenA, tokenB, fee)` for every DEX, pair and
fee tier; the zero address means no pool. Verify `token0()`/`fee()` of each pool found. Store
the pool list; the report prints it.

## Live facts the code must respect

- **Shallow pools return absurd quotes without an error.** Sushi 0.01% WETH/USDC quoted
  1 WETH → 9.14 USDC while the deep pools quoted ~2,648. A mid-price comparison would call this
  a huge opportunity. Only **executable round trips at real sizes** count; mid prices are used
  only as a prefilter (below), never as a result.
- Non-existent fee tier or pool → `eth_call` error code 3 "execution reverted" →
  `ContractCallError` → that quote is skipped and counted, never treated as 0.
- The public Arbitrum RPC keeps no history ("missing trie node"). Historical `eth_call` uses
  the Alchemy archive endpoint (`MEVSCOUT_RPC_42161`). Latency ~0.12 s per call.
- Etherscan V2 free tier serves Arbitrum logs; the WETH/USDC 0.05% Uniswap pool emits ~800
  swaps per 20,000 blocks (~430k per 30 days), so a 30-day pull is ~430 pages for that pool.
- On Arbitrum, Etherscan log `gasPrice × gasUsed` was ~2× the receipt's actual fee in the
  liquidation census (17 of 20 samples). Use receipts for gas in 2A (one per detected arbitrage
  transaction, batched), not the log fields.
- Alchemy free tier: batches of at most 25 calls, exponential back-off, and **never run two
  heavy jobs on the same key**.

## 2A — Arbitrage census from swap events

1. Fetch all `Swap` logs of every discovered pool for the window (default 90 days) with the
   existing Etherscan log source (per-DEX topic0; all existing response rules and coverage
   guard apply; store per pool).
2. Decode: Uniswap/Sushi `Swap(sender, recipient, int256 amount0, int256 amount1, uint160
   sqrtPriceX96, uint128 liquidity, int24 tick)`; PancakeSwap adds `uint128, uint128`. Positive
   amount = token paid into the pool; negative = token paid out.
3. Group swaps by transaction. A transaction is an **atomic arbitrage** if it touches at least
   two tracked pools and the net flow per token across its tracked swaps is ≥ 0 for every token
   and > 0 for at least one (tokens out of pools minus tokens into pools). This is a lower
   bound: routes through untracked pools are missed; the report says so.
4. Value the profit in USD at that block using the swap's own `sqrtPriceX96` against the
   USDC or USDT pool of the same DEX at the nearest earlier block (USDC/USDT = 1 USD by
   assumption, printed); gas from the receipt (`gasUsed × effectiveGasPrice`), native priced
   the same way. Net = profit − gas. Negative-net arbitrages are kept and counted.
5. Attribute each arbitrage to the transaction's `from` (from the receipt) and to the
   contract it called (`to`).
6. Report per month and overall: count, gross, gas, net (USD and EUR), number of distinct
   bots, top-1 / top-3 share and HHI of net, distribution of net per arbitrage (median, p90,
   max), and the verdict with the Phase 1 rule. Size buckets by profit: `<10`, `10–100`,
   `100–1k`, `≥1k` USD.
7. Validation: for a fixed-seed sample of 20 detected arbitrages, recompute the net token flow
   from the receipt's ERC-20 `Transfer` logs to and from `to`/`from`; report agreement.

## 2B — Leftover opportunities at sampled blocks

1. Sample blocks: one every 10 minutes over the window (default 30 days), plus every block
   listed with `--dense FROM:TO` (e.g. crash days). Store the sampled block numbers.
2. Prefilter per pair at each sampled block: read `slot0()` of every pool (batched), convert
   to a mid price, and skip any ordered pool pair whose mid-price gap is not larger than the
   two pools' fees combined — a round trip cannot be profitable then, so no quote is needed.
   Count skipped pairs.
3. For pool pairs that pass: quote the round trip with QuoterV2 at that block, for sizes of
   1,000, 10,000 and 50,000 USD of the quote token: buy on A (`quoteExactInputSingle`), sell
   the output on B, profit = final amount − start amount. Gas estimate: sum of the two
   quotes' `gasEstimate` plus 100,000 overhead, priced at that block's base fee
   (`eth_getBlockByNumber`), native via the WETH/USDC pool.
4. An **opportunity** is a (block, pool pair, size) with net > 0. Track persistence: when an
   opportunity is found, re-quote the same pool pair at the next 1, 2, 5 and 20 blocks and
   record when it disappears.
5. Report per pair: share of sampled blocks with any opportunity, net at the best size
   (median, p90, max), persistence distribution, and how many were on shallow pools
   (liquidity below a threshold printed in the report). Verdict with the same rule, treating
   the sum of best-size net across samples, scaled to the month, as an **upper bound** of what
   a slow bot could earn (the report says it is an upper bound).

## CLI

```
mev-scout arb-pools   --chain 42161
mev-scout arb-fetch   --chain 42161 --days 90
mev-scout arb-census  --chain 42161 --days 90 --eurusd 1.1460 [--json] [--csv FILE]
mev-scout arb-sample  --chain 42161 --days 30 [--every-min 10] [--dense FROM:TO ...]
mev-scout arb-report  --chain 42161 --eurusd 1.1460 [--json]
```

Exit codes 0 / 2 as in Phase 1. Every monetary value `Decimal`; raw amounts `int`.

## Quality bar

Same as SPEC.md. Tests offline with `httpx.MockTransport`, using the real fixtures in
`tests/fixtures/arb/` (Uniswap, Sushi and PancakeSwap WETH/USDC 0.05% swaps from Arbitrum
blocks 506,700,000–506,705,000). One test per rule above, including: PancakeSwap topic decoded;
a Uniswap-topic filter on a PancakeSwap pool is a test failure; a non-cycle transaction (plain
swap through two pools that ends in a different token) is not an arbitrage; a reverted quote is
skipped and counted; prefilter skips a pair exactly at the fee boundary; persistence stops at
the first block without profit.

## Non-goals

No execution, no contracts, no private keys, no mempool, no Sonic (next), no CEX data,
no cross-chain.
