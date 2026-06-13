"""Thin web3 wrapper with RPC failover and retry logic.

This is the lowest layer — no business logic, just RPC mechanics.
All domain modules (BalanceManager, LPManager) compose this.
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from requests.exceptions import HTTPError
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import Web3RPCError

from .abi import ERC20_ABI, LB_PAIR_ABI
from .config import Settings
from .constants import MANTLE_RPC_ENDPOINTS
from .logging_config import get_logger

T = TypeVar("T")


class RpcClient:
    """Stateless web3 wrapper with RPC endpoint failover."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)
        self._failed_endpoints: list[str] = []  # Track recently failed endpoints for rotation

        self.w3, self.active_rpc_url = self._connect_with_failover()
        if not self.w3.is_connected():
            raise RuntimeError(f"Unable to connect to any RPC endpoints: {MANTLE_RPC_ENDPOINTS}")

        self.logger.debug(f"RpcClient initialized with RPC: {self.active_rpc_url}")

    # ── Connection ─────────────────────────────────────────

    def _connect_with_failover(self, skip_list: list[str] | None = None) -> tuple[Web3, str]:
        """Try connecting to RPC endpoints in order, skipping any in skip_list."""
        endpoints_to_try: list[str] = []

        if hasattr(self.settings, "rpc_url") and self.settings.rpc_url not in MANTLE_RPC_ENDPOINTS:
            endpoints_to_try.append(self.settings.rpc_url)

        endpoints_to_try.extend(MANTLE_RPC_ENDPOINTS)

        # On rotation, skip all recently failed endpoints
        if skip_list:
            skip_set = set(skip_list)
            endpoints_to_try = [e for e in endpoints_to_try if e not in skip_set]
            if not endpoints_to_try:
                endpoints_to_try = list(MANTLE_RPC_ENDPOINTS)

        last_error = None
        for i, rpc_url in enumerate(endpoints_to_try, 1):
            try:
                self.logger.debug(f"Attempting RPC connection {i}/{len(endpoints_to_try)}: {rpc_url}")
                w3 = Web3(Web3.HTTPProvider(rpc_url))

                chain_id = w3.eth.chain_id
                expected_chain_id = self.settings.chain_id

                if chain_id != expected_chain_id:
                    self.logger.debug(
                        f"RPC {rpc_url} returned wrong chain ID: {chain_id} (expected {expected_chain_id})"
                    )
                    continue

                latest_block = w3.eth.block_number
                if latest_block > 0:
                    self.logger.debug(
                        f"Connected to RPC: {rpc_url} (block #{latest_block}, chain {chain_id})"
                    )
                    return w3, rpc_url

            except Exception as e:
                last_error = e
                self.logger.debug(f"Failed to connect to RPC {rpc_url}: {type(e).__name__}: {e}")
                continue

        if last_error:
            raise RuntimeError(f"All RPC endpoints failed. Last error: {last_error}")
        raise RuntimeError("All RPC endpoints failed with unknown errors")

    def reconnect(self) -> None:
        """Rotate to the next working RPC endpoint, skipping all recently failed ones."""
        old_rpc = self.active_rpc_url
        self._failed_endpoints.append(old_rpc)
        # If all endpoints failed, reset the list and start over
        all_endpoints = set(MANTLE_RPC_ENDPOINTS)
        if hasattr(self.settings, "rpc_url"):
            all_endpoints.add(self.settings.rpc_url)
        if set(self._failed_endpoints) >= all_endpoints:
            self.logger.info(f"All {len(self._failed_endpoints)} endpoints tried — resetting rotation")
            self._failed_endpoints.clear()
        self.w3, self.active_rpc_url = self._connect_with_failover(skip_list=self._failed_endpoints)
        self.logger.info(f"RPC rotated: {old_rpc} -> {self.active_rpc_url} "
                         f"(skipped {len(self._failed_endpoints)} failed endpoints)")

    # ── Retry ──────────────────────────────────────────────

    def call_with_retry(self, operation_name: str, fn: Callable[[], T], max_retries: int = 3) -> T:
        """Execute a callable with RPC rotation on transient failures."""
        for attempt in range(max_retries):
            try:
                self.logger.debug(
                    f"Executing {operation_name} (attempt {attempt + 1}/{max_retries}) "
                    f"on RPC: {self.active_rpc_url}"
                )
                result = fn()
                if attempt > 0:
                    self.logger.debug(f"{operation_name} succeeded after {attempt + 1} attempts")
                return result
            except (HTTPError, Web3RPCError, ConnectionError, TimeoutError) as e:
                self.logger.debug(
                    f"{operation_name} failed on attempt {attempt + 1}/{max_retries}: "
                    f"{type(e).__name__}: {e}"
                )
                if attempt < max_retries - 1:
                    try:
                        self.reconnect()
                    except Exception as reconnect_error:
                        self.logger.debug(
                            f"Failed to reconnect: {type(reconnect_error).__name__}: {reconnect_error}"
                        )
                        if attempt == max_retries - 1:
                            raise e
                else:
                    raise e
        raise RuntimeError(f"{operation_name} failed after {max_retries} retries")  # unreachable

    # ── Utilities ──────────────────────────────────────────

    @staticmethod
    def checksum(address: str) -> str:
        return Web3.to_checksum_address(address)

    def get_contract(self, address: str, abi: list) -> Contract:
        return self.w3.eth.contract(address=self.checksum(address), abi=abi)

    def get_erc20_contract(self, address: str) -> Contract:
        return self.get_contract(address, ERC20_ABI)

    def get_pair_contract(self, address: str) -> Contract:
        return self.get_contract(address, LB_PAIR_ABI)

    @property
    def block_number(self) -> int:
        return self.w3.eth.block_number

    def get_balance(self, address: str) -> int:
        """Get native token balance in wei."""
        return self.w3.eth.get_balance(self.checksum(address))

    def get_code(self, address: str, block: int | None = None) -> bytes:
        kwargs: dict[str, Any] = {}
        if block is not None:
            kwargs["block_identifier"] = block
        return bytes(self.w3.eth.get_code(self.checksum(address), **kwargs))

    def get_logs(self, filter_params: dict[str, Any]) -> list[dict]:
        return self.call_with_retry("get_logs", lambda: self.w3.eth.get_logs(filter_params))

    # ── Chain State ────────────────────────────────────────

    def get_chain_state(self) -> dict[str, Any]:
        def _get():
            chain_id = self.w3.eth.chain_id
            latest_block = self.w3.eth.block_number
            return {
                "chain_id": chain_id,
                "latest_block": latest_block,
                "expected_chain_id": self.settings.chain_id,
                "chain_id_matches_expected": chain_id == self.settings.chain_id,
                "rpc_url": self.active_rpc_url,
                "configured_rpc_url": self.settings.rpc_url,
            }

        return self.call_with_retry("get_chain_state", _get)

    # ── Log Scanning ───────────────────────────────────────

    def scan_transfer_logs(
        self,
        *,
        pair_contract: Contract,
        pool_address: str,
        indexed_topic_position: int,
        wallet_topic: str,
        from_block: int,
        to_block: int,
        chunk_size: int = 100,
    ) -> set[int]:
        """Scan TransferBatch events for bin IDs. Handles chunk size limits."""
        event = pair_contract.events.TransferBatch()
        topic0 = event.topic
        ids: set[int] = set()

        start = from_block
        chunk_size = max(1, chunk_size)
        while start <= to_block:
            end = min(start + chunk_size - 1, to_block)
            topics: list[Any] = [topic0, None, None, None]
            topics[indexed_topic_position] = wallet_topic
            try:
                logs = self.get_logs(
                    {
                        "address": pool_address,
                        "fromBlock": start,
                        "toBlock": end,
                        "topics": topics,
                    }
                )
            except HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 413 and chunk_size > 1:
                    chunk_size = max(1, chunk_size // 2)
                    continue
                raise
            except Web3RPCError as exc:
                message = str(exc)
                if "eth_getLogs is limited to a 100 range" in message and chunk_size > 100:
                    chunk_size = 100
                    continue
                raise
            for log in logs:
                decoded = event.process_log(log)
                ids.update(int(bin_id) for bin_id in decoded["args"]["ids"])
            start = end + 1

        return ids

    @staticmethod
    def wallet_topic(wallet_address: str) -> str:
        """Convert wallet address to padded topic for log filtering."""
        normalized = wallet_address.lower().replace("0x", "")
        return "0x" + normalized.rjust(64, "0")
