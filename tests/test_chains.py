import pytest
from mev_scout.chains import CHAINS, UNCOVERED_CHAINS, ChainConfig


def test_chain_config_fields():
    assert hasattr(ChainConfig, "log_source")
    assert hasattr(ChainConfig, "base_url")


def test_all_phase1b_chains_present():
    expected_ids = {
        1,       # Ethereum
        10,      # Optimism
        100,     # Gnosis
        137,     # Polygon
        146,     # Sonic
        324,     # zkSync Era
        8453,    # Base
        42161,   # Arbitrum One
        42220,   # Celo
        43114,   # Avalanche
        59144,   # Linea
        534352,  # Scroll
    }
    assert set(CHAINS.keys()) == expected_ids


def test_chain_log_sources_and_urls():
    # Etherscan V2 chains
    for cid in [1, 137, 146, 42161, 59144]:
        c = CHAINS[cid]
        assert c.log_source == "etherscan"
        assert c.base_url == "https://api.etherscan.io/v2/api"

    # Blockscout chains
    assert CHAINS[8453].log_source == "blockscout"
    assert CHAINS[8453].base_url == "https://base.blockscout.com/api"

    assert CHAINS[10].log_source == "blockscout"
    assert CHAINS[10].base_url == "https://optimism.blockscout.com/api"

    assert CHAINS[100].log_source == "blockscout"
    assert CHAINS[100].base_url == "https://gnosis.blockscout.com/api"

    assert CHAINS[534352].log_source == "blockscout"
    assert CHAINS[534352].base_url == "https://scroll.blockscout.com/api"

    assert CHAINS[42220].log_source == "blockscout"
    assert CHAINS[42220].base_url == "https://celo.blockscout.com/api"

    assert CHAINS[324].log_source == "blockscout"
    assert CHAINS[324].base_url == "https://zksync.blockscout.com/api"

    # Routescan chain
    assert CHAINS[43114].log_source == "routescan"
    assert CHAINS[43114].base_url == "https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api"


def test_chain_addresses_lowercase_and_valid():
    for cid, c in CHAINS.items():
        assert c.pool.startswith("0x") and len(c.pool) == 42
        assert c.pool == c.pool.lower()
        assert c.wrapped_native.startswith("0x") and len(c.wrapped_native) == 42
        assert c.wrapped_native == c.wrapped_native.lower()
        assert len(c.native_symbol) > 0


def test_uncovered_chains_lists_bnb_chain():
    uncovered_ids = [c["chain_id"] for c in UNCOVERED_CHAINS]
    assert uncovered_ids == [56]
    bnb = UNCOVERED_CHAINS[0]
    assert bnb["name"] == "BNB Chain"
    assert "Etherscan V2 refuses" in bnb["reason"]
