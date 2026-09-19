# Phase 3 — DeFi Opportunity Observatory (live) — owner direction, 2026-09-19

Source: plan pasted by the owner on 2026-09-19 ("nu uita de asta"). Recorded here verbatim in
substance; Claude's notes in the second half.

## The plan

Two separate problems:
1. Where are there enough opportunities with less competition?
2. How do we reduce the bot's latency enough to compete?

Not answerable from TVL or transaction counts — measure opportunities and real competition.

**Bot Competition Scanner** — per chain / protocol / strategy collect:
raw opportunities per hour; median gross profit; opportunity lifetime (ms/s); profit after gas;
profit after slippage; opportunity capture rate (how often it disappears before we could
execute); first-seen → execution (our latency); competing txs; failed arb txs (evidence of
competitors); private vs public order flow. The last three matter most. Central metric:
`T_survival = T_disappeared − T_first_observed` (median, p90) per chain — a chain with 10×
fewer opportunities but seconds of survival beats one with 120 ms survival.

**Chains:** avoid Ethereum mainnet at first (mature competition). Compare simultaneously with the
same strategy: Arbitrum, Base, Optimism, Polygon, Avalanche, BNB Chain, possibly Sonic and other
L1/L2 with enough DEXes and liquidity.

**Architecture for speed:** fast RPC/WebSocket → local state in RAM → opportunity engine →
transaction builder → private/public RPC. Never make RPC calls at opportunity time for data that
could already be held: update state for all tracked pools on block N, evaluate from RAM on N+1.
WebSocket subscriptions instead of polling; pending transactions where available. Several RPC
providers per chain behind a latency monitor (p50/p95) that picks the fastest healthy one — RPC
infrastructure is part of the strategy.

**Language:** Python first; profile. Move the hot path to Rust only if Python computation, not
network, dominates.

**First stage:** not the bot — the Observatory: DEX events → opportunity detector → DEX arb /
bridge arb / liquidation → competition metrics → chain/strategy matrix. Then decide with data
where opportunities are large, frequent and slow enough for a new bot.

**Next step proposed:** define exactly which data to collect for DEX arb + liquidation + bridge,
from where, and the monthly infrastructure cost per chain.

## Claude's notes (how it fits phases 1–2)

- **Phases 1–2 already answer part of it from history, cheaply:** liquidation census (who won,
  concentration, crash-only profit), 2A (what arbitrage bots earned, how many bots, top-1
  share), 2B (leftover opportunities and persistence in blocks), 2C (cross-chain gap survival).
  Use their verdicts to choose which chains/strategies deserve a live observatory, instead of
  running live collectors everywhere.
- **Failed arb txs can be measured from history too:** once 2A identifies bot contracts, pull
  their transaction lists and count reverted transactions (`isError=1`) per chain — direct
  evidence of competition without any live infrastructure. Add to 2A.
- **Mempool caveat:** Arbitrum, Base and Optimism have no public mempool (sequencer, first come
  first served / private); "pending transactions" and "private vs public order flow" are only
  observable on chains with a public mempool (BNB, Polygon, Avalanche, Ethereum). On rollups,
  competition shows up as same-block or next-block arbitrage transactions and reverts.
- **Resolution:** history gives block resolution (Arbitrum 0.25 s, Base/OP 2 s). Sub-block
  timing (the 42 ms / 57 ms example) needs the live collector.
- **Costs:** WebSocket and several providers per chain are not free-tier friendly; Alchemy free
  tier already throttled batch reads. The infrastructure-cost estimate per chain belongs in the
  Phase 3 spec.
- **Log sources:** BNB Chain had no free historical log source; a live WebSocket collector does
  not need one.
