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


CHAINS: dict[int, ChainConfig] = {
    42161: ChainConfig(
        chain_id=42161,
        name="Arbitrum One",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        wrapped_native="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
        native_symbol="WETH",
    ),
    146: ChainConfig(
        chain_id=146,
        name="Sonic",
        pool="0x5362dbb1e601abf3a4c14c22ffeda64042e5eaa3",
        wrapped_native="0x039e2fb66102314ce7b64ce5ce3e5183bc94ad38",
        native_symbol="wS",
    ),
}

UNCOVERED_CHAINS = [
    {"chain_id": 8453, "name": "Base", "reason": "Etherscan V2 free tier refuses it ('Free API access is not supported for this chain') and Alchemy free tier limits eth_getLogs to 10 blocks"},
    {"chain_id": 10, "name": "Optimism", "reason": "Etherscan V2 free tier refuses it ('Free API access is not supported for this chain') and Alchemy free tier limits eth_getLogs to 10 blocks"},
]
