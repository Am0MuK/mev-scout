# mev-scout Phase 2C Implementation Plan

Implement `docs/SPEC-phase2c-crosschain.md` (read it fully, including the verified factory table).
Reuse the existing code: `dex.py` (pools, Uniswap-style quoter encoding), `arb_sample.py`
(slot0 mids, median stable price, round-trip quoting helpers), `rpc.py` (batching, pacing,
transient-error retry), `store.py`, `report.py` verdict helpers.

## Rules
- Test first; run the whole suite after each task; commit after each task (conventional messages).
- Offline tests only. No network, no `.env`, no files outside /data/projects/mev-scout-2c. Do not push.
- `int` raw amounts, `Decimal` money. Never a default price, decimals, gas or base fee: missing
  data is marked unpriced and counted. Only reverts (`ContractCallError`) are skipped and
  counted; transport errors propagate.
- Save results after every sampled moment and resume on restart (skip finished moments);
  print progress to stderr every 10 moments.

## Tasks
1. Multi-chain venue table for Arbitrum, Base, Optimism (tokens + all factories/quoters in
   the spec, Uniswap-style fee adapter and Slipstream tickSpacing adapter; classic Aerodrome
   `0x420dd…` pools via `getPool(a,b,bool)` and `Pool.getAmountOut(uint256,address)`).
2. Pool discovery per chain across all venues; shallow check (10× size within 2% of 1× price).
3. Time grid and block-by-timestamp per chain (binary search on `eth_getBlockByNumber`,
   cached); skew guard (> 5 s older → drop moment, counted).
4. Best executable buy/sell quote per chain and size across all pools; cross-chain gap for
   each ordered chain pair; gas (quote gasEstimate + 100k at base fee; L1 fee via
   GasPriceOracle `getL1Fee(bytes)` on Base/Optimism); rebalance cost params.
5. Persistence (+next blocks, +1 min, +5 min); capital and return on capital; monthly verdict
   (reuse `evaluate_verdict`); text/JSON/CSV report. CLI `xchain-sample`, `xchain-report`
   with `MEVSCOUT_RPC_<CHAINID>` for 42161, 8453, 10.
6. README section; final summary with commits, pytest result, every deviation.
