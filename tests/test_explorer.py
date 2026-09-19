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
