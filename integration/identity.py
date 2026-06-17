#!/usr/bin/env python3
"""
On-chain agent identity via the BNB AI Agent SDK (ERC-8004).

We depend on the published `bnbagent` PyPI package (`pip install bnbagent`); the SDK
is NOT vendored into this repository. Registering an ERC-8004 identity (gas-free on
BSC testnet via the MegaFuel paymaster) gives the agent a discoverable on-chain
`agentId` and targets the "Best Use of BNB AI Agent SDK" special prize.

Scope: identity only. Trade execution stays on Trust Wallet Agent Kit (see
execution/twak.py). This module is optional and imported lazily so the rest of the
bot runs without `bnbagent` installed.

Env:
  WALLET_PASSWORD   encrypts/decrypts the keystore (~/.bnbagent/wallets/)
  AGENT_PRIVATE_KEY agent wallet private key (first run only; then removable)
  BNB_NETWORK       "bsc-testnet" (default, gas-free) or "bsc-mainnet"
  AGENT_ENDPOINT    public status URL advertised in the agent profile
"""
import os

try:
    from bnbagent import ERC8004Agent, AgentEndpoint, EVMWalletProvider
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def is_available() -> bool:
    return _AVAILABLE


def register_identity(name="fidukat-trading-agent",
                      description="Fidukat — disciplined self-custody trading agent on BNB Chain",
                      network=None, endpoint=None):
    """Register the agent's ERC-8004 identity on BNB Chain. Returns the SDK result
    dict ({'agentId', 'transactionHash', ...}). Raises if `bnbagent` is missing."""
    if not _AVAILABLE:
        raise RuntimeError("bnbagent not installed — run: pip install bnbagent")
    network = network or os.environ.get("BNB_NETWORK", "bsc-testnet")
    endpoint = endpoint or os.environ.get("AGENT_ENDPOINT", "https://example.com/erc8183/status")

    wallet = EVMWalletProvider(
        password=os.environ["WALLET_PASSWORD"],
        private_key=os.environ.get("AGENT_PRIVATE_KEY"),  # only needed on first run
    )
    sdk = ERC8004Agent(network=network, wallet_provider=wallet)
    agent_uri = sdk.generate_agent_uri(
        name=name,
        description=description,
        endpoints=[AgentEndpoint(name="fidukat", endpoint=endpoint, version="0.1.0")],
    )
    return sdk.register_agent(agent_uri=agent_uri)


if __name__ == "__main__":
    if not _AVAILABLE:
        print("bnbagent not installed. Install with: pip install bnbagent")
    elif not os.environ.get("WALLET_PASSWORD"):
        print("Set WALLET_PASSWORD (and AGENT_PRIVATE_KEY on first run) to register.")
    else:
        res = register_identity()
        print(f"Registered. agentId={res.get('agentId')} tx={res.get('transactionHash')}")
