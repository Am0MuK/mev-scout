# mev-scout Phase 1b — more chains (addendum to SPEC.md)

All pools and wrapped-native reserves below were verified on-chain 2026-09-19
(`ADDRESSES_PROVIDER()` answered, wrapped native present in `getReservesList()`).

| Chain | id | Log source | Aave V3 Pool | Wrapped native |
|---|---|---|---|---|
| Ethereum | 1 | Etherscan V2 | `0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2` | `0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2` |
| Polygon | 137 | Etherscan V2 | `0x794a61358d6845594f94dc1db02a252b5b4814ad` | `0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270` |
| Linea | 59144 | Etherscan V2 | `0xc47b8c00b0f69a36fa203ffeac0334874574a8ac` | `0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f` |
| Base | 8453 | Blockscout `https://base.blockscout.com/api` | `0xa238dd80c259a72e81d7e4664a9801593f98d1c5` | `0x4200000000000000000000000000000000000006` |
| Optimism | 10 | Blockscout `https://optimism.blockscout.com/api` | `0x794a61358d6845594f94dc1db02a252b5b4814ad` | `0x4200000000000000000000000000000000000006` |
| Gnosis | 100 | Blockscout `https://gnosis.blockscout.com/api` | `0xb50201558b00496a145fe76f7424749556e326d8` | `0xe91d153e0b41518a2ce8dd3d7944fa863463a97d` |
| Scroll | 534352 | Blockscout `https://scroll.blockscout.com/api` | `0x11fcfe756c05ad438e312a7fd934381537d3cffe` | `0x5300000000000000000000000000000000000004` |
| Celo | 42220 | Blockscout `https://celo.blockscout.com/api` | `0x3e59a31363e2ad014dcbc521c4a0d5757d9f3402` | `0x471ece3750da237f93b8e339c536989b8978a438` |
| zkSync Era | 324 | Blockscout `https://zksync.blockscout.com/api` | `0x78e30497a3c7527d953c6b1e3541b021a98ac43c` | `0x5aea5775959fbc2557cc8789bc1bf90a239d9a91` |
| Avalanche | 43114 | Routescan `https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api` | `0x794a61358d6845594f94dc1db02a252b5b4814ad` | `0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7` |

Not covered: BNB Chain (56) — no free log source found (Etherscan V2 refuses, Routescan
"chain not supported"). The report lists it as uncovered.

## Log-source rules

- Blockscout and Routescan speak the Etherscan `module=logs&action=getLogs` dialect without
  `chainid` and without `apikey`; send a `User-Agent`.
- **Blockscout ignores `page`** (verified: page 2 returned the same 1,000 rows as page 1 on
  Gnosis). Block-cursor pagination is unaffected, but the single-block page-number mode must
  detect a repeated page (same `(transactionHash, logIndex)` set as the previous page) and raise
  `ExplorerError` instead of looping or double-counting.
- Blockscout Base returned HTTP 500 for a whole-history range and answered for 5,000,000-block
  ranges. On HTTP 5xx, halve the requested block range and retry (down to 1,000 blocks), then
  `ExplorerError`. Never skip the range.
- Treat "No records found" / empty list per source exactly as in SPEC; any other shape is an error.

## Gas on rollups

For OP-stack chains (Base, Optimism, Celo) and Scroll the L1 data fee is not in
`gasUsed × gasPrice`. The validation sample reads `receipt.l1Fee` when present and reports the
average L1 fee as a share of the execution fee. If that share exceeds 10%, print a WARNING that
gas is underestimated on that chain.

## Performance (found in the first live run)

Valuation made ~8 separate `eth_call` requests per event; Sonic's 786 events took
minutes and Arbitrum would take hours.

- **JSON-RPC batching**: send the calls for many events as one JSON-RPC batch (array body),
  at most 100 calls per batch. Every response in the batch is matched by `id`; a missing id,
  a duplicate id, or an error object for one call is handled per call exactly as a single
  call would be (retry transport problems for the whole batch; `ContractCallError` only for
  that call). Never assume response order.
- **Decimals** do not depend on the block: cache `decimals()` per `(chain, token)` and read it
  once at the window's last block.
- Everything else stays cached per block as before.

## Implementation notes

- Add a `LogSource` abstraction: `EtherscanClient` (with `chainid` + `apikey`) and a
  `BlockscoutClient` (no key, no `chainid`, custom base URL, `User-Agent` header, 5xx range
  halving, repeated-page detection). Both share the response rules.
- Chain table in `chains.py` gains `log_source` and `base_url`.
- RPC env per chain stays `MEVSCOUT_RPC_<CHAINID>`.
- Tests: one per new rule (repeated page, 5xx halving down to the floor, batch id matching,
  batch with one reverted call, decimals cache ignores block).
