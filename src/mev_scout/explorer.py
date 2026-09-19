"""Explorer clients and LogSource abstraction for Etherscan and Blockscout."""

from abc import ABC, abstractmethod
import re
import time
from typing import Any
import httpx

from mev_scout.chains import ChainConfig, ConfigError

PAGE_SIZE = 1000
MAX_PAGES = 10_000 // PAGE_SIZE  # 10
RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BACKOFF_S = 0.5


class ExplorerError(Exception):
    """Raised when an explorer API call fails or returns an error status."""


class Explorer5xxError(ExplorerError):
    """Raised on HTTP 5xx server error from an explorer."""


def redact(text: str) -> str:
    """Redact sensitive API keys from URLs or error messages."""
    return re.sub(r"(apikey=)[^&\s]+", r"\1***", str(text), flags=re.IGNORECASE)


def _is_rate_limit(status: str, message: str, result: Any) -> bool:
    combined = f"{message} {result}".lower()
    return "rate limit" in combined or "limit reached" in combined


def _check_page(page: list[dict], from_block: int, to_block: int) -> None:
    """Pagination relies on rows in ascending block order inside the range."""
    previous = from_block
    for r in page:
        block = int(str(r["blockNumber"]), 0)
        if not from_block <= block <= to_block:
            raise ExplorerError(f"log in block {block} is outside the requested range {from_block}-{to_block}")
        if block < previous:
            raise ExplorerError(f"logs not in ascending block order (block {block} after {previous})")
        previous = block


class LogSource(ABC):
    @abstractmethod
    def block_by_time(self, chain_id: int, timestamp: int) -> int:
        """Resolve a timestamp to the closest block number after that time."""

    @abstractmethod
    def get_logs(
        self,
        chain_id: int,
        address: str,
        topic0: str,
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        """Fetch logs for an address and topic0 across a block range."""


class EtherscanClient(LogSource):
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
            _check_page(page, start_block, to_block)

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


class BlockscoutClient(LogSource):
    def __init__(
        self,
        base_url: str,
        http: httpx.Client,
        user_agent: str = "mev-scout/0.1.0",
        sleep=time.sleep,
    ):
        self.base_url = base_url
        self.http = http
        self.user_agent = user_agent
        self._sleep = sleep

    def _clean(self, text: Any) -> str:
        return redact(str(text))

    def _request(self, params: dict) -> Any:
        full_params = dict(params)
        headers = {"User-Agent": self.user_agent}

        last_error = None
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            if attempt > 0:
                self._sleep(RATE_LIMIT_BACKOFF_S * attempt)

            try:
                resp = self.http.get(self.base_url, params=full_params, headers=headers)
            except Exception as exc:
                raise ExplorerError(f"Blockscout request failed: {self._clean(exc)}") from exc

            if resp.status_code == 429:
                last_error = ExplorerError(f"HTTP 429 rate limited from {self._clean(self.base_url)}")
                continue

            if 500 <= resp.status_code < 600:
                raise Explorer5xxError(f"HTTP {resp.status_code} from {self._clean(self.base_url)}")

            try:
                resp.raise_for_status()
            except Exception as exc:
                raise ExplorerError(f"Blockscout HTTP error: {self._clean(exc)}") from exc

            try:
                data = resp.json()
            except Exception as exc:
                raise ExplorerError(f"Non-JSON response from Blockscout: {self._clean(resp.text[:200])}") from exc

            if not isinstance(data, dict):
                raise ExplorerError(f"Malformed Blockscout response: expected JSON object, got {type(data)}")

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
            "module": "block",
            "action": "getblocknobytime",
            "timestamp": str(timestamp),
            "closest": "after",
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
            target_to = to_block
            page = None
            while True:
                span = target_to - start_block + 1
                params = {
                    "module": "logs",
                    "action": "getLogs",
                    "address": address,
                    "topic0": topic0,
                    "fromBlock": str(start_block),
                    "toBlock": str(target_to),
                    "page": "1",
                    "offset": str(PAGE_SIZE),
                }
                try:
                    page = self._request(params)
                    break
                except Explorer5xxError as exc:
                    if span <= 1000:
                        raise ExplorerError(
                            f"HTTP 5xx from {self._clean(self.base_url)} and range cannot be halved below 1000 blocks: {exc}"
                        ) from exc
                    new_span = max(1000, span // 2)
                    target_to = start_block + new_span - 1

            if not isinstance(page, list):
                raise ExplorerError(f"Expected list of logs, got {type(page)}")
            _check_page(page, start_block, target_to)

            if len(page) < PAGE_SIZE:
                all_rows.extend(page)
                start_block = target_to + 1
                continue

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
        prev_page_keys: set[tuple[str, int]] | None = None
        for page_no in range(1, MAX_PAGES + 1):
            params = {
                "module": "logs",
                "action": "getLogs",
                "address": address,
                "topic0": topic0,
                "fromBlock": str(block),
                "toBlock": str(block),
                "page": str(page_no),
                "offset": str(PAGE_SIZE),
            }
            page = self._request(params)
            if not isinstance(page, list):
                raise ExplorerError(f"Expected list of logs, got {type(page)}")

            # Detect repeated page
            curr_keys = {(r["transactionHash"].lower(), int(str(r["logIndex"]), 0)) for r in page}
            if prev_page_keys is not None and curr_keys == prev_page_keys:
                raise ExplorerError(
                    f"Repeated page detected in single-block pagination at block {block}: "
                    "explorer ignored page parameter"
                )
            prev_page_keys = curr_keys

            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
        raise ExplorerError(f"more than {MAX_PAGES * PAGE_SIZE} rows in block {block}; cannot paginate")


def create_log_source(
    chain: ChainConfig,
    http: httpx.Client,
    api_key: str | None = None,
) -> LogSource:
    source_type = chain.log_source.lower()
    if source_type == "etherscan":
        if not api_key:
            raise ConfigError("ETHERSCAN_API_KEY environment variable is required")
        return EtherscanClient(api_key=api_key, http=http, base_url=chain.base_url)
    elif source_type in ("blockscout", "routescan"):
        return BlockscoutClient(base_url=chain.base_url, http=http)
    else:
        raise ConfigError(f"Unsupported log source {chain.log_source} for chain {chain.chain_id}")
