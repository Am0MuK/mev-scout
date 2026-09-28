import json
import httpx
import pytest

from mev_scout.explorer import EtherscanClient, ExplorerError, redact


def test_redact_apikey():
    url = "https://api.etherscan.io/v2/api?chainid=1&apikey=SECRET123&module=logs"
    assert "SECRET123" not in redact(url)
    assert "apikey=***" in redact(url)


def test_status_1_with_list_returns_rows():
    rows = [{"blockNumber": "0x1", "transactionHash": "0xabc", "logIndex": "0x0"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    result = client._request({"module": "logs", "action": "getLogs"})
    assert result == rows


def test_status_0_no_records_found_returns_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "message": "No records found", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    result = client._request({"module": "logs", "action": "getLogs"})
    assert result == []


def test_rate_limit_retry_eventually_fails():
    sleep_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=sleep_calls.append)
    with pytest.raises(ExplorerError, match="rate limit"):
        client._request({"module": "logs", "action": "getLogs"})
    assert len(sleep_calls) == 5


def test_rate_limit_retry_succeeds():
    calls = 0
    sleep_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Max rate limit reached"})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": [{"foo": "bar"}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=sleep_calls.append)
    result = client._request({"module": "logs", "action": "getLogs"})
    assert result == [{"foo": "bar"}]
    assert len(sleep_calls) == 2


def test_status_1_non_list_raises_explorer_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": "some string"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError, match="not a list"):
        client._request({"module": "logs", "action": "getLogs"})


def test_status_0_other_error_raises_explorer_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Query Timeout"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError, match="Query Timeout"):
        client._request({"module": "logs", "action": "getLogs"})


def test_http_error_raises_explorer_error_with_redaction():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error with apikey=SUPERSECRET")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="SUPERSECRET", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError) as exc_info:
        client._request({"module": "logs", "action": "getLogs"})
    assert "SUPERSECRET" not in str(exc_info.value)
    assert "apikey=***" in str(exc_info.value) or "***" in str(exc_info.value)


def test_non_json_raises_explorer_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError):
        client._request({"module": "logs", "action": "getLogs"})


def test_get_logs_pagination_page_under_1000_ends():
    rows = [
        {"blockNumber": "0xa", "transactionHash": "0x1", "logIndex": "0x0"},
        {"blockNumber": "0xb", "transactionHash": "0x2", "logIndex": "0x0"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    res = client.get_logs(chain_id=42161, address="0xpool", topic0="0xtopic", from_block=10, to_block=20)
    assert res == rows


def test_get_logs_pagination_full_page_drops_last_block_and_refetches():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        from_b = int(params["fromBlock"])
        if from_b == 100:
            # First page: 1000 rows. 800 at block 100, 200 at block 101.
            res = [{"blockNumber": "0x64", "transactionHash": f"0x{i}", "logIndex": "0x0"} for i in range(800)]
            res += [{"blockNumber": "0x65", "transactionHash": f"0x{800+i}", "logIndex": "0x0"} for i in range(200)]
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": res})
        elif from_b == 101:
            # Second page starting at block 101: 250 rows (all block 101). Ends range.
            res = [{"blockNumber": "0x65", "transactionHash": f"0x{1000+i}", "logIndex": "0x0"} for i in range(250)]
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": res})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    res = client.get_logs(chain_id=42161, address="0xpool", topic0="0xtopic", from_block=100, to_block=105)

    assert len(res) == 800 + 250
    # First call had fromBlock=100, second had fromBlock=101
    assert calls[0]["fromBlock"] == "100"
    assert calls[1]["fromBlock"] == "101"


def test_get_logs_pagination_full_page_inside_one_block():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("page") == "1" and params.get("fromBlock") == "100" and params.get("toBlock") == "110":
            # Initial query returned 1000 rows all at block 100
            res = [{"blockNumber": "0x64", "transactionHash": f"0x{i}", "logIndex": "0x0"} for i in range(1000)]
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": res})
        elif params.get("fromBlock") == "100" and params.get("toBlock") == "100":
            # Single block queries
            page = int(params["page"])
            if page == 1:
                res = [{"blockNumber": "0x64", "transactionHash": f"0x{i}", "logIndex": "0x0"} for i in range(1000)]
            elif page == 2:
                res = [{"blockNumber": "0x64", "transactionHash": f"0x{1000+i}", "logIndex": "0x0"} for i in range(200)]
            else:
                res = []
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": res})
        elif params.get("fromBlock") == "101":
            # Range continuation after block 100
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    res = client.get_logs(chain_id=42161, address="0xpool", topic0="0xtopic", from_block=100, to_block=110)

    # 1200 rows in block 100
    assert len(res) == 1200


def test_get_logs_single_block_exceeds_10000_rows_raises_explorer_error():
    def handler(request: httpx.Request) -> httpx.Response:
        # Always returns 1000 rows
        res = [{"blockNumber": "0x64", "transactionHash": f"0x{i}", "logIndex": "0x0"} for i in range(1000)]
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": res})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError, match="cannot paginate"):
        client.get_logs(chain_id=42161, address="0xpool", topic0="0xtopic", from_block=100, to_block=110)


def test_get_logs_duplicate_txhash_logindex_raises():
    rows = [
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x1"},
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x1"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": rows})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError, match="[Dd]uplicate"):
        client.get_logs(chain_id=42161, address="0xpool", topic0="0xtopic", from_block=10, to_block=20)


def test_block_by_time_success():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["module"] == "block"
        assert params["action"] == "getblocknobytime"
        assert params["closest"] == "after"
        assert params["timestamp"] == "1700000000"
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": "50060028"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    block = client.block_by_time(chain_id=146, timestamp=1700000000)
    assert block == 50060028


def test_block_by_time_failure_raises_explorer_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "0", "message": "NOTOK", "result": "Invalid timestamp"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EtherscanClient(api_key="KEY", http=http, sleep=lambda s: None)
    with pytest.raises(ExplorerError, match="Invalid timestamp"):
        client.block_by_time(chain_id=146, timestamp=1700000000)


def _client(pages):
    calls = iter(pages)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": next(calls)})

    return EtherscanClient(api_key="KEY", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_rows_outside_requested_range_are_an_error():
    rows = [{"blockNumber": hex(5), "transactionHash": "0xa", "logIndex": "0x0"}]
    with pytest.raises(ExplorerError, match="outside"):
        _client([rows]).get_logs(1, "0xpool", "0xtopic", 10, 20)


def test_rows_not_in_ascending_block_order_are_an_error():
    rows = [
        {"blockNumber": hex(15), "transactionHash": "0xa", "logIndex": "0x0"},
        {"blockNumber": hex(12), "transactionHash": "0xb", "logIndex": "0x0"},
    ]
    with pytest.raises(ExplorerError, match="order"):
        _client([rows]).get_logs(1, "0xpool", "0xtopic", 10, 20)


def test_dropped_connection_is_retried():
    # Live: "Server disconnected without sending a response" aborted a 90-day fetch.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        return httpx.Response(200, json={"status": "0", "message": "No records found", "result": []})

    client = EtherscanClient(api_key="KEY", http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert client.get_logs(1, "0xpool", "0xtopic", 1, 10) == []
    assert calls["n"] == 3


def test_get_logs_accepts_0x_for_log_index_zero():
    # Etherscan encodes zero as a bare "0x" (seen on Arbitrum Burn logs, 2026-09-28).
    rows = [
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x"},
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x1"},
    ]
    assert _client([rows]).get_logs(1, "0xpool", "0xtopic", 10, 20) == rows


def test_get_logs_0x_log_index_still_detects_duplicates():
    rows = [
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x"},
        {"blockNumber": "0xa", "transactionHash": "0xabc", "logIndex": "0x0"},
    ]
    with pytest.raises(ExplorerError, match="[Dd]uplicate"):
        _client([rows]).get_logs(1, "0xpool", "0xtopic", 10, 20)


def test_parse_int_handles_explorer_encodings():
    from mev_scout.hexint import parse_int

    assert parse_int("0x") == 0
    assert parse_int("0X") == 0
    assert parse_int("0x1f") == 31
    assert parse_int("46807524") == 46807524
    assert parse_int(7) == 7
    for bad in ("", None, "abc"):
        with pytest.raises(ValueError):
            parse_int(bad)
