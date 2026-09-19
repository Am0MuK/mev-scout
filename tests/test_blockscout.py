import httpx
import pytest

from mev_scout.chains import CHAINS, ChainConfig, ConfigError
from mev_scout.explorer import (
    BlockscoutClient,
    EtherscanClient,
    ExplorerError,
    LogSource,
    create_log_source,
)


def test_log_source_subclasses():
    assert issubclass(EtherscanClient, LogSource)
    assert issubclass(BlockscoutClient, LogSource)


def test_blockscout_no_chainid_no_apikey_and_has_user_agent():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://base.blockscout.com/api", http=http, sleep=lambda s: None)

    # get_logs call
    client.get_logs(chain_id=8453, address="0xpool", topic0="0xtopic", from_block=100, to_block=200)

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert "chainid" not in req.url.params
    assert "apikey" not in req.url.params
    assert "user-agent" in req.headers
    assert req.headers["user-agent"]  # Non-empty User-Agent header


def test_blockscout_block_by_time_no_chainid_no_apikey():
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": "12345"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://base.blockscout.com/api", http=http, sleep=lambda s: None)

    blk = client.block_by_time(chain_id=8453, timestamp=1700000000)
    assert blk == 12345
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert "chainid" not in req.url.params
    assert "apikey" not in req.url.params
    assert "user-agent" in req.headers


def test_blockscout_repeated_page_detection_in_single_block():
    calls = []

    # Blockscout returns the same 1,000 rows on page 2 as page 1
    page1 = [{"blockNumber": "0x64", "transactionHash": f"0x{i:04x}", "logIndex": "0x0"} for i in range(1000)]
    page2 = list(page1)  # Exact same set of (transactionHash, logIndex)

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("fromBlock") == "100" and params.get("toBlock") == "105":
            # Initial call for range returns 1000 rows all at block 100
            return httpx.Response(200, json={"status": "1", "message": "OK", "result": page1})
        elif params.get("fromBlock") == "100" and params.get("toBlock") == "100":
            # Single-block pagination call
            page_no = params.get("page")
            if page_no == "1":
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": page1})
            elif page_no == "2":
                return httpx.Response(200, json={"status": "1", "message": "OK", "result": page2})
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://gnosis.blockscout.com/api", http=http, sleep=lambda s: None)

    with pytest.raises(ExplorerError, match="[Rr]epeated page"):
        client.get_logs(chain_id=100, address="0xpool", topic0="0xtopic", from_block=100, to_block=105)


def test_blockscout_5xx_halving_down_to_floor_succeeds():
    requested_ranges = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        from_b = int(params["fromBlock"])
        to_b = int(params["toBlock"])
        requested_ranges.append((from_b, to_b))

        span = to_b - from_b + 1
        if span > 1000:
            # Server errors on ranges larger than 1000 blocks
            return httpx.Response(500, text="Internal Server Error")

        # Returns 200 for 1000-block range
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": [
            {"blockNumber": hex(from_b), "transactionHash": f"0x{from_b}", "logIndex": "0x0"}
        ]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://base.blockscout.com/api", http=http, sleep=lambda s: None)

    # Request 4,000 blocks: [1000, 4999]
    # Halving:
    # 1. [1000, 4999] (span 4000) -> 500
    # 2. [1000, 2999] (span 2000) -> 500
    # 3. [1000, 1999] (span 1000) -> 200 (floor reached and succeeds)
    # 4. Continues to cover remaining [2000, 4999]...
    logs = client.get_logs(chain_id=8453, address="0xpool", topic0="0xtopic", from_block=1000, to_block=4999)

    assert len(logs) > 0
    # Verified it started at 4000, halved to 2000, halved to 1000
    assert (1000, 4999) in requested_ranges
    assert (1000, 2999) in requested_ranges
    assert (1000, 1999) in requested_ranges


def test_blockscout_5xx_at_floor_raises_explorer_error():
    requested_ranges = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        from_b = int(params["fromBlock"])
        to_b = int(params["toBlock"])
        requested_ranges.append((from_b, to_b))
        # Always returns 500 even at floor (1000 blocks)
        return httpx.Response(500, text="Internal Server Error")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://base.blockscout.com/api", http=http, sleep=lambda s: None)

    with pytest.raises(ExplorerError, match="5xx|500|floor|halv"):
        client.get_logs(chain_id=8453, address="0xpool", topic0="0xtopic", from_block=1000, to_block=4999)

    # Check it halved down to 1000 blocks before giving up
    assert (1000, 1999) in requested_ranges


def test_blockscout_5xx_halving_never_skips_range():
    fetched_blocks = set()

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        from_b = int(params["fromBlock"])
        to_b = int(params["toBlock"])
        span = to_b - from_b + 1

        if span > 2000:
            return httpx.Response(503, text="Service Unavailable")

        # Range <= 2000 succeeds
        fetched_blocks.add((from_b, to_b))
        return httpx.Response(200, json={"status": "1", "message": "OK", "result": []})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://base.blockscout.com/api", http=http, sleep=lambda s: None)

    # Request range of 4000 blocks: [1000, 4999]
    client.get_logs(chain_id=8453, address="0xpool", topic0="0xtopic", from_block=1000, to_block=4999)

    # Ensure the full range [1000, 4999] was covered without gaps:
    # First half: [1000, 2999] (span 2000)
    # Second half: [3000, 4999] (span 2000)
    assert (1000, 2999) in fetched_blocks
    assert (3000, 4999) in fetched_blocks


def test_create_log_source_factory():
    http = httpx.Client()
    # Etherscan chain
    eth_src = create_log_source(CHAINS[1], http=http, api_key="MY_KEY")
    assert isinstance(eth_src, EtherscanClient)
    assert eth_src.api_key == "MY_KEY"

    # Etherscan chain without key raises ConfigError
    with pytest.raises(ConfigError, match="ETHERSCAN_API_KEY"):
        create_log_source(CHAINS[1], http=http, api_key=None)

    # Blockscout chain works without API key
    base_src = create_log_source(CHAINS[8453], http=http, api_key=None)
    assert isinstance(base_src, BlockscoutClient)
    assert base_src.base_url == "https://base.blockscout.com/api"

    # Routescan chain works without API key
    avax_src = create_log_source(CHAINS[43114], http=http, api_key=None)
    assert isinstance(avax_src, BlockscoutClient)
    assert "routescan" in avax_src.base_url


def _bs(payload):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    return BlockscoutClient(base_url="https://gnosis.blockscout.com/api", http=http, sleep=lambda s: None)


def test_blockscout_block_by_time_real_shape():
    # Captured live from gnosis.blockscout.com and base.blockscout.com on 2026-09-19.
    payload = {"message": "OK", "result": {"blockNumber": "46807524"}, "status": "1"}
    assert _bs(payload).block_by_time(100, 1_700_000_000) == 46807524


def test_blockscout_empty_logs_real_shape():
    # Blockscout says "No logs found" where Etherscan says "No records found".
    payload = {"message": "No logs found", "result": [], "status": "0"}
    assert _bs(payload).get_logs(100, "0xpool", "0xtopic", 1, 100) == []


def test_blockscout_other_status_0_is_still_an_error():
    payload = {"message": "Something else", "result": [], "status": "0"}
    with pytest.raises(ExplorerError):
        _bs(payload).get_logs(100, "0xpool", "0xtopic", 1, 100)


def test_blockscout_retries_timeouts():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"message": "No logs found", "result": [], "status": "0"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = BlockscoutClient(base_url="https://x/api", http=http, sleep=lambda s: None)
    assert client.get_logs(100, "0xpool", "0xtopic", 1, 100) == []
    assert calls["n"] == 3


def test_chain_urls_do_not_redirect_to_another_host():
    # gnosis/scroll/optimism.blockscout.com answer 301 to another host (verified 2026-09-19).
    for cid in (10, 100, 534352):
        assert "blockscout.com" not in CHAINS[cid].base_url
