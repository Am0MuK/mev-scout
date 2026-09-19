import httpx
import pytest

from mev_scout.rpc import ContractCallError, RpcClient, RpcError, _redact_url


def test_redact_url():
    url = "https://arb-mainnet.g.alchemy.com/v2/secret-api-key-123"
    redacted = _redact_url(url)
    assert "secret-api-key-123" not in redacted
    assert redacted == "https://arb-mainnet.g.alchemy.com/***"


def test_call_success():
    def handler(request: httpx.Request) -> httpx.Response:
        data = request.read()
        assert b"eth_call" in data
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x0000000000000000000000000000000000000000000000000000000000000020"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    res = client.call("0xpool", "0xd1946dbc", 50060028)
    assert res == "0x0000000000000000000000000000000000000000000000000000000000000020"


def test_call_revert_raises_contract_call_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    with pytest.raises(ContractCallError, match="execution reverted"):
        client.call("0xpool", "0xd1946dbc", 100)


def test_call_empty_result_raises_contract_call_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    with pytest.raises(ContractCallError, match="empty"):
        client.call("0xpool", "0xd1946dbc", 100)


def test_receipt_success():
    expected = {"transactionHash": "0xabc", "gasUsed": "0x5208", "effectiveGasPrice": "0x3b9aca00", "logs": []}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": expected})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    rcpt = client.receipt("0xabc")
    assert rcpt == expected


def test_receipt_none_raises_rpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": None})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    with pytest.raises(RpcError, match="not found"):
        client.receipt("0xabc")


def test_block_number_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x2fbdafc"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    assert client.block_number() == 50060028


def test_block_number_invalid_raises_rpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": ""})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    with pytest.raises(RpcError, match="invalid eth_blockNumber"):
        client.block_number()


def test_retry_on_status_429():
    attempts = 0
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=sleeps.append)
    res = client.call("0xto", "0x00", 1)
    assert res == "0x123"
    assert attempts == 3
    assert len(sleeps) == 2


def test_retry_on_status_503():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    res = client.call("0xto", "0x00", 1)
    assert res == "0x123"
    assert attempts == 2


def test_retry_on_transport_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    res = client.call("0xto", "0x00", 1)
    assert res == "0x123"
    assert attempts == 2


def test_retry_on_rate_limit_json_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "rate limit exceeded"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    res = client.call("0xto", "0x00", 1)
    assert res == "0x123"
    assert attempts == 2


def test_retry_on_non_json_response():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return httpx.Response(200, text="<!DOCTYPE html><html>Gateway timeout</html>")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x123"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    res = client.call("0xto", "0x00", 1)
    assert res == "0x123"
    assert attempts == 2


def test_exhausted_retries_raises_rpc_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Server Error")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret-key", http, sleep=lambda s: None)
    with pytest.raises(RpcError) as exc_info:
        client.call("0xto", "0x00", 1)
    assert "secret-key" not in str(exc_info.value)
    assert "https://rpc.example.com/***" in str(exc_info.value)


def test_non_retryable_400_raises_immediately():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, text="Bad Request")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    with pytest.raises(RpcError, match="HTTP 400"):
        client.call("0xto", "0x00", 1)
    assert attempts == 1
