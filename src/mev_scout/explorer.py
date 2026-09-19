"""Etherscan V2 explorer client."""

import re
import time
from typing import Any
import httpx

PAGE_SIZE = 1000
MAX_PAGES = 10_000 // PAGE_SIZE  # 10
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_S = 0.5


class ExplorerError(Exception):
    """Raised when an explorer API call fails or returns an error status."""


def redact(text: str) -> str:
    """Redact sensitive API keys from URLs or error messages."""
    return re.sub(r"(apikey=)[^&\s]+", r"\1***", str(text), flags=re.IGNORECASE)


def _is_rate_limit(status: str, message: str, result: Any) -> bool:
    combined = f"{message} {result}".lower()
    return "rate limit" in combined or "limit reached" in combined


class EtherscanClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        base_url: str = "https://api.etherscan.io/v2/api",
        sleep=time.sleep,
    ):
        self.api_key = api_key
        self.http = http
        self.base_url = base_url
        self._sleep = sleep

    def _clean(self, text: Any) -> str:
        s = redact(str(text))
        if self.api_key and self.api_key in s:
            s = s.replace(self.api_key, "***")
        return s

    def _request(self, params: dict) -> Any:
        full_params = dict(params)
        if "apikey" not in full_params and self.api_key:
            full_params["apikey"] = self.api_key

        last_error = None
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            if attempt > 0:
                self._sleep(RATE_LIMIT_BACKOFF_S * attempt)

            try:
                resp = self.http.get(self.base_url, params=full_params)
            except Exception as exc:
                raise ExplorerError(f"Etherscan request failed: {self._clean(exc)}") from exc

            if resp.status_code == 429:
                last_error = ExplorerError(f"HTTP 429 rate limited from {self._clean(self.base_url)}")
                continue

            try:
                resp.raise_for_status()
            except Exception as exc:
                raise ExplorerError(f"Etherscan HTTP error: {self._clean(exc)}") from exc

            try:
                data = resp.json()
            except Exception as exc:
                raise ExplorerError(f"Non-JSON response from Etherscan: {self._clean(resp.text[:200])}") from exc

            if not isinstance(data, dict):
                raise ExplorerError(f"Malformed Etherscan response: expected JSON object, got {type(data)}")

            status = str(data.get("status", ""))
            message = str(data.get("message", ""))
            result = data.get("result")

            if status == "1":
                if full_params.get("action") == "getblocknobytime":
                    try:
                        return int(str(result), 0)
                    except (ValueError, TypeError) as exc:
                        raise ExplorerError(f"Invalid block number in getblocknobytime: {result!r}") from exc
                if isinstance(result, list):
                    return result
                raise ExplorerError(f"status 1 but result is not a list: {self._clean(result)!r}"[:300])

            if status == "0" and message == "No records found" and result == []:
                return []

            if _is_rate_limit(status, message, result):
                last_error = ExplorerError(self._clean(f"{message}: {result}"))
                continue

            raise ExplorerError(self._clean(f"{message}: {result}"))

        if last_error:
            raise last_error
        raise ExplorerError("Max retries exceeded without a response")

    def block_by_time(self, chain_id: int, timestamp: int) -> int:
        params = {
            "chainid": str(chain_id),
            "module": "block",
            "action": "getblocknobytime",
            "timestamp": str(timestamp),
            "closest": "after",
            "apikey": self.api_key,
        }
        res = self._request(params)
        return int(res)

    def get_logs(
        self,
        chain_id: int,
        address: str,
        topic0: str,
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        all_rows: list[dict] = []
        start_block = from_block

        while start_block <= to_block:
            params = {
                "chainid": str(chain_id),
                "module": "logs",
                "action": "getLogs",
                "address": address,
                "topic0": topic0,
                "fromBlock": str(start_block),
                "toBlock": str(to_block),
                "page": "1",
                "offset": str(PAGE_SIZE),
                "apikey": self.api_key,
            }
            page = self._request(params)
            if not isinstance(page, list):
                raise ExplorerError(f"Expected list of logs, got {type(page)}")

            if len(page) < PAGE_SIZE:
                all_rows.extend(page)
                break

            first_block = int(str(page[0]["blockNumber"]), 0)
            last_block = int(str(page[-1]["blockNumber"]), 0)

            if first_block == last_block:
                single_rows = self._fetch_single_block(chain_id, address, topic0, last_block)
                all_rows.extend(single_rows)
                if last_block >= to_block:
                    break
                start_block = last_block + 1
                continue

            all_rows.extend(r for r in page if int(str(r["blockNumber"]), 0) != last_block)
            start_block = last_block

        seen = set()
        for r in all_rows:
            key = (r["transactionHash"].lower(), int(str(r["logIndex"]), 0))
            if key in seen:
                raise ExplorerError(f"Duplicate log entry detected: tx={r['transactionHash']} logIndex={r['logIndex']}")
            seen.add(key)

        return all_rows

    def _fetch_single_block(self, chain_id: int, address: str, topic0: str, block: int) -> list[dict]:
        rows: list[dict] = []
        for page_no in range(1, MAX_PAGES + 1):
            params = {
                "chainid": str(chain_id),
                "module": "logs",
                "action": "getLogs",
                "address": address,
                "topic0": topic0,
                "fromBlock": str(block),
                "toBlock": str(block),
                "page": str(page_no),
                "offset": str(PAGE_SIZE),
                "apikey": self.api_key,
            }
            page = self._request(params)
            if not isinstance(page, list):
                raise ExplorerError(f"Expected list of logs, got {type(page)}")
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
        raise ExplorerError(f"more than {MAX_PAGES * PAGE_SIZE} rows in block {block}; cannot paginate")
