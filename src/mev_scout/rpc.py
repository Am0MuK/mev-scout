"""JSON-RPC client for archive reads and receipts."""

import time
from typing import Any
from urllib.parse import urlsplit
import httpx

RETRIES = 6
BACKOFF_S = 1.0  # doubles each retry: 1+2+4+8+16+32 = 63 s before giving up
BATCH_SIZE = 25  # Alchemy free tier rate-limits by compute units per second
_RETRY_STATUS = {429, 500, 502, 503, 504}


class RpcError(Exception):
    """The RPC endpoint could not answer (transport, rate limit, bad response)."""


class ContractCallError(RpcError):
    """The node answered, but the contract call reverted or returned no usable data."""


def _redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}/***"
    except Exception:
        return "***"


def _is_rate_limit(error: dict) -> bool:
    message = str(error.get("message", "")).lower()
    return error.get("code") in (429, -32005) or "rate" in message or "limit" in message


def _is_revert(error: dict) -> bool:
    return error.get("code") == 3 or "revert" in str(error.get("message", "")).lower()


class RpcClient:
    def __init__(self, url: str, http: httpx.Client, sleep=time.sleep):
        self.url = url
        self.http = http
        self._redacted_url = _redact_url(url)
        self._sleep = sleep

    def _clean(self, text: Any) -> str:
        s = str(text)
        if self.url:
            s = s.replace(self.url, self._redacted_url)
        return s

    def _call(self, method: str, params: list) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_problem = "no attempt made"

        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(BACKOFF_S * 2 ** (attempt - 1))

            try:
                resp = self.http.post(self.url, json=payload)
            except httpx.HTTPError as exc:
                last_problem = f"transport error: {self._clean(exc)}"
                continue

            if resp.status_code in _RETRY_STATUS:
                last_problem = f"HTTP {resp.status_code}"
                continue
            if resp.status_code >= 400:
                raise RpcError(f"RPC HTTP {resp.status_code} from {self._redacted_url}")

            try:
                data = resp.json()
            except (ValueError, TypeError):
                last_problem = "non-JSON response"
                continue

            error = data.get("error") if isinstance(data, dict) else None
            if error:
                if _is_revert(error):
                    raise ContractCallError(f"call reverted: {self._clean(error)}")
                if _is_rate_limit(error):
                    last_problem = f"rate limited: {self._clean(error)}"
                    continue
                raise RpcError(f"RPC error: {self._clean(error)}")

            if not isinstance(data, dict):
                last_problem = "malformed response: expected JSON object"
                continue

            return data.get("result")

        raise RpcError(
            f"RPC unavailable after {RETRIES + 1} attempts ({last_problem}) at {self._redacted_url}"
        )

    def call(self, to: str, data: str, block: int | str) -> str:
        block_tag = hex(block) if isinstance(block, int) else str(block)
        res = self._call("eth_call", [{"to": to, "data": data}, block_tag])
        if not isinstance(res, str) or res in ("", "0x"):
            raise ContractCallError(f"eth_call returned empty or invalid data: {res!r} for {to}")
        return res

    def batch_call(self, calls: list[tuple[str, str, int | str]]) -> list[Any]:
        """Execute a list of eth_calls in JSON-RPC batches of at most 100 calls."""
        if not calls:
            return []

        all_results: list[Any] = []
        batch_size = BATCH_SIZE

        for i in range(0, len(calls), batch_size):
            chunk = calls[i : i + batch_size]
            chunk_results = self._batch_call_chunk(chunk)
            all_results.extend(chunk_results)

        return all_results

    def _batch_call_chunk(self, chunk: list[tuple[str, str, int | str]]) -> list[Any]:
        payload = []
        for req_id, c in enumerate(chunk, start=1):
            to = c[0] if isinstance(c, (list, tuple)) else c["to"]
            data = c[1] if isinstance(c, (list, tuple)) else c["data"]
            raw_b = c[2] if isinstance(c, (list, tuple)) else c["block"]
            block_tag = hex(raw_b) if isinstance(raw_b, int) else str(raw_b)
            payload.append({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "eth_call",
                "params": [{"to": to, "data": data}, block_tag],
            })

        last_problem = "no attempt made"

        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(BACKOFF_S * 2 ** (attempt - 1))

            try:
                resp = self.http.post(self.url, json=payload)
            except httpx.HTTPError as exc:
                last_problem = f"transport error: {self._clean(exc)}"
                continue

            if resp.status_code in _RETRY_STATUS:
                last_problem = f"HTTP {resp.status_code}"
                continue
            if resp.status_code >= 400:
                raise RpcError(f"RPC HTTP {resp.status_code} from {self._redacted_url}")

            try:
                data = resp.json()
            except (ValueError, TypeError):
                last_problem = "non-JSON response"
                continue

            # If node returned a single error object instead of a list
            if isinstance(data, dict):
                error = data.get("error")
                if error and _is_rate_limit(error):
                    last_problem = f"rate limited: {self._clean(error)}"
                    continue
                raise RpcError(f"RPC error: {self._clean(error or data)}")

            if not isinstance(data, list):
                last_problem = f"malformed response: expected JSON array for batch, got {type(data)}"
                continue

            # Check if any call in batch returned a rate limit error requiring whole-batch retry
            has_batch_rate_limit = False
            for item in data:
                if isinstance(item, dict) and "error" in item and item["error"]:
                    if _is_rate_limit(item["error"]):
                        has_batch_rate_limit = True
                        last_problem = f"rate limited: {self._clean(item['error'])}"
                        break
            if has_batch_rate_limit:
                continue

            # Process responses matched by id
            id_to_items: dict[int, list[dict]] = {}
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    item_id = item["id"]
                    if item_id not in id_to_items:
                        id_to_items[item_id] = []
                    id_to_items[item_id].append(item)

            results: list[Any] = []
            for req_id in range(1, len(chunk) + 1):
                if req_id not in id_to_items:
                    results.append(RpcError(f"Missing id {req_id} in RPC batch response"))
                    continue

                items = id_to_items[req_id]
                if len(items) > 1:
                    results.append(RpcError(f"Duplicate id {req_id} in RPC batch response"))
                    continue

                item = items[0]
                error = item.get("error")
                if error:
                    if _is_revert(error):
                        results.append(ContractCallError(f"call reverted: {self._clean(error)}"))
                    else:
                        results.append(RpcError(f"RPC error: {self._clean(error)}"))
                    continue

                res = item.get("result")
                if not isinstance(res, str) or res in ("", "0x"):
                    results.append(ContractCallError(f"eth_call returned empty or invalid data: {res!r}"))
                else:
                    results.append(res)

            return results

        raise RpcError(
            f"RPC unavailable after {RETRIES + 1} attempts ({last_problem}) at {self._redacted_url}"
        )

    def receipt(self, tx_hash: str) -> dict:
        res = self._call("eth_getTransactionReceipt", [tx_hash])
        if not isinstance(res, dict) or not res:
            raise RpcError(f"Transaction receipt not found or invalid for {tx_hash}")
        return res

    def block_number(self) -> int:
        res = self._call("eth_blockNumber", [])
        if not isinstance(res, str) or not res:
            raise RpcError("invalid eth_blockNumber response")
        return int(res, 16)
