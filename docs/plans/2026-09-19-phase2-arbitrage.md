# mev-scout Phase 2 (2A + 2B) Implementation Plan

**Goal:** implement `docs/SPEC-phase2-arbitrage.md` sections 2A and 2B for Arbitrum (42161).
Read it completely, plus `SPEC.md` and the existing code in `src/mev_scout` (log sources,
RPC client with batching and back-off, store, report and verdict helpers). Reuse them; do
not duplicate them.

## Rules

- Test first for every task; run the whole suite after each task; commit after each task
  with conventional messages. Keep all existing tests passing.
- Offline tests only (`httpx.MockTransport` / fakes). Use the real fixtures in
  `tests/fixtures/arb/`. **No network calls, no `.env`, no files outside
  /data/projects/mev-scout-2.** Do not push, do not touch other branches.
- `int` for raw amounts, `Decimal` for money, never `float` for money.
- Anything ambiguous: choose the option that fails loudly and list it in the final summary.
- Never treat an error, revert, missing data or empty result as zero. Count it and report it.

## Tasks

1. `src/mev_scout/dex.py`: DEX table from the spec (factory, quoter, fee tiers, swap topic0),
   token table, pool discovery via `getPool` (zero address = no pool; verify `token0`/`fee`),
   stored in a new `pools` table. Tests incl. PancakeSwap tier 2500 and zero-address.
2. `src/mev_scout/swaps.py`: decode Uniswap/Sushi and PancakeSwap `Swap` logs (signed int256,
   uint160, uint128, int24; Pancake's two extra uint128). Tests with all three fixture files;
   a test proving a Uniswap-topic filter returns nothing on the PancakeSwap fixture.
3. Swap fetch per pool with the existing Etherscan log source and coverage guard; new
   `swaps` table (primary key chain, tx_hash, log_index). CLI `arb-pools`, `arb-fetch`.
4. `src/mev_scout/arb_census.py` (2A): group by tx, cycle detection exactly as the spec
   (net per token ≥ 0 for all, > 0 for one, ≥ 2 tracked pools); USD valuation from the swap's
   sqrtPriceX96 vs the stable pool of the same DEX at the nearest earlier block; gas from
   receipts (batched); attribution to tx `from`/`to`; monthly report and verdict reusing the
   Phase 1 verdict function; profit buckets; fixed-seed 20-sample Transfer validation.
   Tests: two-pool cycle, three-pool cycle, non-cycle multi-hop, negative-net arbitrage kept.
5. `src/mev_scout/arb_sample.py` (2B): sampled blocks every N minutes plus `--dense` ranges;
   `slot0` prefilter (mid-price gap vs combined fees, boundary exact: equal gap = skip);
   QuoterV2 round trips at 1k/10k/50k USD (`quoteExactInputSingle` tuple selector
   `0xc6a5026a`, decode amountOut and gasEstimate); gas via base fee from
   `eth_getBlockByNumber`; persistence at +1, +2, +5, +20 blocks; reverted quote skipped and
   counted; shallow-pool flag. CLI `arb-sample`, `arb-report`.
6. README section for phase 2; final summary with commits, pytest result, deviations.
