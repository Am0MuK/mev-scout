# mev-scout Phase 1 Implementation Plan

**Goal:** implement `SPEC.md` (liquidation opportunity census for Aave V3 on Arbitrum and Sonic).

**Architecture:** small Python package `mev_scout` under `src/`, SQLite cache, CLI via argparse.
Explorer and RPC clients follow the proven design of `/data/projects/onchain-tieout`
(`src/onchain_tieout/explorer.py`, `rpc.py`): read them first and adapt; do not import from it.

**Tech stack:** Python 3.11+, `httpx`, `pytest`, stdlib `sqlite3`, `decimal`, `argparse`.

## Rules for the implementer

- Test first for every task: write the failing test, run it, implement, run all tests, commit.
- All tests offline with `httpx.MockTransport` or fakes. **No network calls. Do not read any
  `.env` file or any file outside `/data/projects/mev-scout` and `/data/projects/onchain-tieout`.**
- Integer arithmetic for raw token amounts; `Decimal` for USD/EUR; never `float` for money.
- Never print or log API keys or RPC URLs; redact `apikey=` and URL paths in error messages.
- Commit after each task with a conventional message (`feat:`, `test:`, `fix:`), git identity
  already configured in the repo. Do not push.
- If the spec is ambiguous, choose the option that fails loudly, and note it in the final summary.

## Tasks

### Task 0: Scaffold
`pyproject.toml` (name `mev-scout`, script `mev-scout = mev_scout.cli:main`, deps `httpx`,
optional dev `pytest`), `src/mev_scout/__init__.py`, `tests/conftest.py`, `.gitignore`
(`data/`, `.venv/`, `__pycache__/`), `.github/workflows/tests.yml` (pytest on push, Python 3.11 and 3.12).
Create a venv in `.venv`, `pip install -e '.[dev]'`, `pytest` runs (0 tests).

### Task 1: Explorer client — `src/mev_scout/explorer.py`
`EtherscanClient(api_key, http, base_url="https://api.etherscan.io/v2/api", sleep=time.sleep)`.
- `_request(params)` implements every rule in SPEC "Explorer response rules" (status handling,
  "No records found", rate-limit retry, non-list result, HTTP/non-JSON errors → `ExplorerError`).
- `get_logs(chain_id, address, topic0, from_block, to_block) -> list[dict]` with the SPEC
  pagination (boundary-block refetch, single-block page numbers, 10,000-row limit error) and the
  final `(transactionHash, logIndex)` uniqueness assertion.
- `block_by_time(chain_id, timestamp) -> int` (`getblocknobytime`, `closest=after`).
Tests: one per rule, including a full page ending mid-block and a full page inside one block.

### Task 2: RPC client — `src/mev_scout/rpc.py`
Adapt onchain-tieout `rpc.py` (retries on 429/5xx/transport/rate-limit JSON errors, `RpcError`,
`ContractCallError` for reverts/empty results, URL redaction). Methods: `call(to, data, block) -> str`,
`receipt(tx_hash) -> dict`, `block_number() -> int`. Tests for each retry and error path.

### Task 3: Decoding — `src/mev_scout/decode.py`
`Liquidation` frozen dataclass: `chain_id, block, timestamp, tx_hash, log_index, collateral,
debt, user, debt_to_cover, collateral_amount, liquidator, receive_atoken, gas_used, gas_price`.
`decode_log(chain_id, row) -> Liquidation`; wrong topic0 or malformed data → `ValueError`.
Test with the real fixture `tests/fixtures/sonic_liquidation_block_50060028.json` (all 11 events;
assert the first event's fields listed in SPEC).

### Task 4: Store — `src/mev_scout/store.py`
SQLite tables: `liquidations` (primary key chain, tx_hash, log_index), `fetched_ranges`
(chain, from_block, to_block), `call_cache` (chain, to, data, block → result).
`covered(chain, from_block, to_block) -> list[gap]` returns gaps (empty list = fully covered).
Tests: overlapping and adjacent ranges merge; a one-block gap is reported.

### Task 5: Fetch command — `src/mev_scout/fetch.py`
Chain config (SPEC table) in `src/mev_scout/chains.py`. `fetch(chain_id, days, explorer, rpc, store, now)`:
verify the wrapped-native address is in `Pool.getReservesList()` (`0xd1946dbc`, decode the
dynamic address array) else raise `ConfigError`; resolve window with `block_by_time`; fetch logs
in chunks of at most 500,000 blocks; store events and the fetched range only after a chunk
succeeded. Re-running skips ranges already covered. Tests with fakes.

### Task 6: Valuation — `src/mev_scout/value.py`
Implements SPEC "Valuation": oracle resolution at each block (cached), `BASE_CURRENCY_UNIT`,
prices, decimals, gross/gas/swap/flash/net in `Decimal` USD; gas split equally across events
sharing a `tx_hash` (synthetic test with 2 events in one tx). Every eth_call goes through the
store cache. `ContractCallError` on a price read marks that event `unpriced` (never priced at 0);
the report counts unpriced events.

### Task 7: Validation — `src/mev_scout/validate.py`
SPEC "Validation": fixed-seed sample of up to 20 events per chain, receipt gas check and
collateral-Transfer check (underlying or aToken; aToken address from `getReserveData(asset)`,
selector `0x35ea6a75`, aToken is word index 8 of the 15-word tuple (verified live on Sonic: wS → aToken `0x6c5e14a212c1c3e4baf6f871ac9b1a969918c131`); if the
tuple length does not match, report the check as `not_run` with the reason instead of guessing).
Tests: agreeing receipt, gas mismatch, missing Transfer.

### Task 8: Report — `src/mev_scout/report.py`
SPEC "Report" and verdict. Months are consecutive 30-day periods ending at the window end.
Text and JSON renderers; events CSV writer. Coverage gaps → raise `CoverageError` (exit 2).
Tests: verdict PASS/FAIL for both conditions, top-1/top-3/HHI on a small hand-computed set,
bucket boundaries (`99.99`, `100`, `999.99`, `1000`, `10000`).

### Task 9: CLI and README — `src/mev_scout/cli.py`, `README.md`
Subcommands `fetch`, `value`, `report` exactly as SPEC; env `ETHERSCAN_API_KEY`,
`MEVSCOUT_RPC_<CHAINID>`; exit codes 0/2; secrets redacted. README: purpose, the question and
the kill criterion, scope and uncovered chains, usage, how numbers are computed, limitations.

## Done means

All tasks committed, `pytest -q` green, a final summary listing any deviation from the spec and
anything left unimplemented. No live run — that is done by the reviewer.
