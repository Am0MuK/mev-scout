# mev-scout — Phase 1 Specification: liquidation opportunity census

## Question this answers

Before writing any bot or contract: on which chain and which size range do Aave V3
liquidations leave at least **300 EUR per month** of estimated net profit, in a market
where **one liquidator does not take almost everything**? If none does, we stop.

This phase is read-only. No keys, no transactions, no contracts.

## Scope

| Chain | chain id | Aave V3 Pool (verified on-chain 2026-09-19) | Wrapped native (gas pricing) |
|---|---|---|---|
| Arbitrum One | 42161 | `0x794a61358d6845594f94dc1db02a252b5b4814ad` | WETH `0x82af49447d8a07e3bd95bd0d56f35241523fbab1` |
| Sonic | 146 | `0x5362dbb1e601abf3a4c14c22ffeda64042e5eaa3` | wS `0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38` |

Base and Optimism are **out of scope**: Etherscan V2 free tier refuses them
(`status 0`, "Free API access is not supported for this chain") and Alchemy free tier
limits `eth_getLogs` to 10 blocks. The report must list covered and uncovered chains.

At run start, verify for each chain that the wrapped-native address is in
`Pool.getReservesList()`; abort (exit 2) if not.

Window: last N days (default 90), resolved to blocks with Etherscan
`module=block&action=getblocknobytime&closest=after`.

## Data sources

- **Logs**: Etherscan V2 `module=logs&action=getLogs`, `address=<pool>`,
  `topic0=0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286`
  (`LiquidationCall(address,address,address,uint256,uint256,address,bool)`, keccak
  computed locally). Each log row also carries `gasPrice`, `gasUsed`, `timeStamp`,
  `transactionHash`, `logIndex` (all hex strings).
- **State at a block**: JSON-RPC `eth_call` with a block tag (Alchemy; archive reads work
  on the free tier). Env: `ETHERSCAN_API_KEY`, `MEVSCOUT_RPC_<CHAINID>` (full URL).

## Explorer response rules (same discipline as onchain-tieout)

- `status "1"` and `result` is a list → rows.
- `status "0"`, `message "No records found"`, `result []` → empty, valid (verified live).
- `status "0"` with a rate-limit message → retry with back-off (5 attempts), then error.
- Anything else, HTTP errors, non-JSON, `status "1"` with a non-list result → `ExplorerError`.
  Never treated as "no liquidations".
- Pagination: `offset=1000`. A page with fewer than 1,000 rows ends the range. A full page:
  drop the rows of its last block and continue from that block (boundary block refetched
  whole). A full page inside one block: read that block alone with `page=1,2,…` while
  `page × 1000 ≤ 10000`, else `ExplorerError`. No key-based de-duplication; instead assert
  `(transactionHash, logIndex)` is unique in the final set and fail if not.

## Coverage guard

Store every fetched block range in SQLite. The report refuses to run (exit 2) if the
stored ranges for a chain do not cover the requested window without gaps.

## Decoding

`topics[1]` collateralAsset, `topics[2]` debtAsset, `topics[3]` user (last 20 bytes);
`data` = `debtToCover (uint256) | liquidatedCollateralAmount (uint256) | liquidator
(address, 32-byte padded) | receiveAToken (bool)`.

## Valuation (all in USD, integer raw amounts, `Decimal` only for money)

For each event at its block `b`:

- Oracle: `Pool.ADDRESSES_PROVIDER()` (`0x0542975c`) → `provider.getPriceOracle()`
  (`0xfca513a8`) at `b`; `oracle.BASE_CURRENCY_UNIT()` (`0x8c89b64f`);
  `oracle.getAssetPrice(asset)` (`0xb3596f07`) at `b`. Token `decimals()` (`0x313ce567`).
- `collateral_usd = liquidatedCollateralAmount / 10^dec_c × price_c / BASE_UNIT`
- `debt_usd = debtToCover / 10^dec_d × price_d / BASE_UNIT`
- `gross_usd = collateral_usd − debt_usd` (the liquidation bonus the liquidator received)
- `gas_usd = gasUsed × gasPrice / 1e18 × price(wrapped native at b) / BASE_UNIT`.
  When one transaction holds several liquidation events, split its gas equally among them.
- Cost assumptions a newcomer would pay (CLI flags, printed in the report):
  swap cost `--swap-cost 0.003` × `collateral_usd`, flash-loan fee `--flash-fee 0.0005` × `debt_usd`.
- `net_usd = gross_usd − gas_usd − swap_cost − flash_fee`.
- Cache every eth_call result in SQLite keyed by `(chain, to, data, block)`.

## Validation (sampled tie-out, printed in the report)

For a random sample of up to 20 events per chain (fixed seed), fetch the receipt via RPC:

1. `receipt.gasUsed × receipt.effectiveGasPrice` must equal the log's `gasUsed × gasPrice`.
2. The receipt must contain an ERC-20 `Transfer` to `liquidator` of either the collateral
   asset or its aToken (aToken from `Pool.getReserveData(asset)`) whose amount equals
   `liquidatedCollateralAmount`. This settles whether the event amount is net of the
   protocol's liquidation fee.

Report the agreement count per check. Any disagreement is printed as a WARNING at the top
of the report, with the transaction hashes.

## Report

Per chain, for the window: covered block range and gap check, event count, number of
distinct `liquidator` addresses, and per 30-day month:

- total `gross_usd`, `gas_usd`, `net_usd`, and net in EUR (`--eurusd` rate, required, printed);
- concentration of `net_usd` by liquidator: top-1 share, top-3 share, HHI;
- the same by size bucket of `debt_usd`: `<100`, `100–1k`, `1k–10k`, `≥10k`.

Verdict per chain and per bucket:

- **PASS** if average monthly `net` ≥ `--threshold-eur` (default 300) **and** top-1 share ≤ 50%;
- otherwise **FAIL**, with the failing condition named.

Top-1 share is a proxy for "the market is still contestable"; the report says so.

Output: text report to stdout, `--json` for machine-readable, events CSV with every column
used in the calculation.

## CLI

```
mev-scout fetch  --chain 42161 --days 90 [--db data/scout.db]
mev-scout value  --chain 42161 [--db ...]
mev-scout report --chain 42161 --chain 146 --eurusd 1.17 [--threshold-eur 300] [--json]
```

Exit codes: `0` report produced, `2` configuration, data-source or coverage error.

## Quality bar

Python ≥ 3.11, dependency `httpx` only (tests: `pytest`). All tests offline via
`httpx.MockTransport`; one test per rule in this document; the real Sonic log below is a
decoding fixture. Integer math for raw amounts. Keys never printed; URLs and `apikey` redacted
from errors. GitHub Actions runs `pytest` on push.

Fixture: `tests/fixtures/sonic_liquidation_block_50060028.json` is the real Etherscan V2
response for Sonic block 50060028 (11 liquidation events). First event: tx
`0x8340c866d4312edb70e4dff0da711b7dbd9b0df08924e4742f706d474c579143`, logIndex `0x77`,
collateral `0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38` (wS), debt
`0x29219dd400f2bf60e5a23d13be72b486d4038894`, liquidator
`0x95654779c314e3390786b98ebcd83f7cbef664ec`.

## Non-goals (Phase 1)

No bot, no contract, no private keys, no mempool, no arbitrage (Phase 2), no chains
beyond the two above, no protocols beyond Aave V3.
