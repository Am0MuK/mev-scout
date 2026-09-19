"""JSON-RPC client for archive reads and receipts."""

import time
from typing import Any
from urllib.parse import urlsplit
import httpx

RETRIES = 5
BACKOFF_S = 0.5
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
                self._sleep(BACKOFF_S * attempt)

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
