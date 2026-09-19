"""Chain configurations and metadata."""

from dataclasses import dataclass


class ConfigError(Exception):
    """Configuration or pre-flight verification error."""


@dataclass(frozen=True)
class ChainConfig:
    chain_id: int
    name: str
    pool: str
    wrapped_native: str
    native_symbol: str
    log_source: str = "etherscan"
    base_url: str = "https://api.etherscan.io/v2/api"


CHAINS: dict[int, ChainConfig] = {
    # Phase 1 chains
    42161: ChainConfig(
        chain_id=42161,
        name="Arbitrum One",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        wrapped_native="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        native_symbol="WETH",
        log_source="etherscan",
        base_url="https://api.etherscan.io/v2/api",
    ),
    146: ChainConfig(
        chain_id=146,
        name="Sonic",
        pool="0x5362dbb1e601abf3a4c14c22ffeda64042e5eaa3",
        wrapped_native="0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38",
        native_symbol="wS",
        log_source="etherscan",
        base_url="https://api.etherscan.io/v2/api",
    ),
    # Phase 1b chains
    1: ChainConfig(
        chain_id=1,
        name="Ethereum",
        pool="0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
        wrapped_native="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        native_symbol="WETH",
        log_source="etherscan",
        base_url="https://api.etherscan.io/v2/api",
    ),
    137: ChainConfig(
        chain_id=137,
        name="Polygon",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        wrapped_native="0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",
        native_symbol="WMATIC",
        log_source="etherscan",
        base_url="https://api.etherscan.io/v2/api",
    ),
    59144: ChainConfig(
        chain_id=59144,
        name="Linea",
        pool="0xc47b8c00b0f69a36fa203ffeac0334874574a8ac",
        wrapped_native="0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f",
        native_symbol="WETH",
        log_source="etherscan",
        base_url="https://api.etherscan.io/v2/api",
    ),
    8453: ChainConfig(
        chain_id=8453,
        name="Base",
        pool="0xa238dd80c259a72e81d7e4664a9801593f98d1c5",
        wrapped_native="0x4200000000000000000000000000000000000006",
        native_symbol="WETH",
        log_source="blockscout",
        base_url="https://base.blockscout.com/api",
    ),
    10: ChainConfig(
        chain_id=10,
        name="Optimism",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        wrapped_native="0x4200000000000000000000000000000000000006",
        native_symbol="WETH",
        log_source="blockscout",
        base_url="https://explorer.optimism.io/api",
    ),
    100: ChainConfig(
        chain_id=100,
        name="Gnosis",
        pool="0xb50201558b00496a145fe76f7424749556e326d8",
        wrapped_native="0xe91d153e0b41518a2ce8dd3d7944fa863463a97d",
        native_symbol="WXDAI",
        log_source="blockscout",
        base_url="https://gnosisscan.io/api",
    ),
    534352: ChainConfig(
        chain_id=534352,
        name="Scroll",
        pool="0x11fcfe756c05ad438e312a7fd934381537d3cffe",
        wrapped_native="0x5300000000000000000000000000000000000004",
        native_symbol="WETH",
        log_source="blockscout",
        base_url="https://scrollscan.com/api",
    ),
    42220: ChainConfig(
        chain_id=42220,
        name="Celo",
        pool="0x3e59a31363e2ad014dcbc521c4a0d5757d9f3402",
        wrapped_native="0x471ece3750da237f93b8e339c536989b8978a438",
        native_symbol="CELO",
        log_source="blockscout",
        base_url="https://celo.blockscout.com/api",
    ),
    324: ChainConfig(
        chain_id=324,
        name="zkSync Era",
        pool="0x78e30497a3c7527d953c6b1e3541b021a98ac43c",
        wrapped_native="0x5aea5775959fbc2557cc8789bc1bf90a239d9a91",
        native_symbol="WETH",
        log_source="blockscout",
        base_url="https://zksync.blockscout.com/api",
    ),
    43114: ChainConfig(
        chain_id=43114,
        name="Avalanche",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        wrapped_native="0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",
        native_symbol="WAVAX",
        log_source="routescan",
        base_url="https://api.routescan.io/v2/network/mainnet/evm/43114/etherscan/api",
    ),
}

UNCOVERED_CHAINS = [
    {
        "chain_id": 56,
        "name": "BNB Chain",
        "reason": "no free log source found (Etherscan V2 refuses, Routescan 'chain not supported')",
    },
]
