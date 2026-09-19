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


def test_batch_id_matching():
    # 3 calls; mock server responds out-of-order: id 3, id 1, id 2
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": 3, "result": "0x3333"},
                {"jsonrpc": "2.0", "id": 1, "result": "0x1111"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x2222"},
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    calls = [
        ("0xto1", "0xdata1", 100),
        ("0xto2", "0xdata2", 200),
        ("0xto3", "0xdata3", 300),
    ]
    results = client.batch_call(calls)
    assert len(results) == 3
    assert results[0] == "0x1111"
    assert results[1] == "0x2222"
    assert results[2] == "0x3333"


def test_batch_with_one_reverted_call():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": 1, "result": "0x1111"},
                {"jsonrpc": "2.0", "id": 2, "error": {"code": 3, "message": "execution reverted: custom error"}},
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    calls = [
        ("0xto1", "0xdata1", 100),
        ("0xto2", "0xdata2", 200),
    ]
    results = client.batch_call(calls)
    assert len(results) == 2
    assert results[0] == "0x1111"
    assert isinstance(results[1], ContractCallError)
    assert "execution reverted" in str(results[1])


def test_batch_with_missing_id():
    def handler(request: httpx.Request) -> httpx.Response:
        # Server omitted response for id 2
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": 1, "result": "0x1111"},
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    calls = [
        ("0xto1", "0xdata1", 100),
        ("0xto2", "0xdata2", 200),
    ]
    results = client.batch_call(calls)
    assert len(results) == 2
    assert results[0] == "0x1111"
    assert isinstance(results[1], RpcError)
    assert "Missing id 2" in str(results[1])


def test_batch_with_duplicate_id():
    def handler(request: httpx.Request) -> httpx.Response:
        # Server returned duplicate responses for id 1
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": 1, "result": "0x1111a"},
                {"jsonrpc": "2.0", "id": 1, "result": "0x1111b"},
                {"jsonrpc": "2.0", "id": 2, "result": "0x2222"},
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    calls = [
        ("0xto1", "0xdata1", 100),
        ("0xto2", "0xdata2", 200),
    ]
    results = client.batch_call(calls)
    assert len(results) == 2
    assert isinstance(results[0], RpcError)
    assert "Duplicate id 1" in str(results[0])
    assert results[1] == "0x2222"


def test_batch_exceeding_100_calls_chunks():
    import json
    chunks_received = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        chunks_received.append(body)
        return httpx.Response(
            200,
            json=[
                {"jsonrpc": "2.0", "id": item["id"], "result": f"0xres_{item['id']}"}
                for item in body
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)

    # 150 calls
    calls = [("0xto", "0xdata", i) for i in range(150)]
    results = client.batch_call(calls)

    assert len(results) == 150
    from mev_scout.rpc import BATCH_SIZE
    assert [len(c) for c in chunks_received] == [BATCH_SIZE] * (150 // BATCH_SIZE) + ([150 % BATCH_SIZE] if 150 % BATCH_SIZE else [])


def test_batch_transport_retry_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            json=[{"jsonrpc": "2.0", "id": 1, "result": "0xsuccess"}],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=lambda s: None)
    results = client.batch_call([("0xto", "0xdata", 100)])
    assert results == ["0xsuccess"]
    assert attempts == 2


def test_backoff_is_exponential_and_long_enough_for_free_tier():
    # Live: Alchemy free tier kept answering 429 through 7.5 s of linear back-off.
    sleeps = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=sleeps.append)
    with pytest.raises(RpcError):
        client.call("0xto", "0x00", 1)
    assert sleeps == sorted(sleeps) and sleeps[1] == 2 * sleeps[0]
    assert sum(sleeps) >= 60


def test_batch_size_is_small():
    from mev_scout import rpc as rpc_mod
    assert rpc_mod.BATCH_SIZE <= 25


def test_client_side_rate_limit_spaces_requests():
    # Alchemy free tier throttles by compute units per second; 25-call batches
    # triggered endless 429s. The client paces itself instead.
    clock = {"t": 0.0}
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    ok = lambda r: httpx.Response(200, json=[{"jsonrpc": "2.0", "id": i, "result": "0x01"} for i in range(1, 11)])
    http = httpx.Client(transport=httpx.MockTransport(ok))
    client = RpcClient("https://rpc.example.com/secret", http, sleep=sleep,
                       max_calls_per_sec=10.0, clock=lambda: clock["t"])
    client.batch_call([("0xto", "0x00", 1)] * 10)
    client.batch_call([("0xto", "0x00", 1)] * 10)
    # 10 calls at 10/s = 1 s budget per batch: the second batch waits ~1 s.
    assert sum(sleeps) >= 0.99
