from __future__ import annotations

from decimal import Decimal
from time import perf_counter
from typing import Any

from .balance_manager import BalanceManager
from .config import Settings
from .logging_config import get_logger
from .lp_service import LPService
from .models import PoolState
from .rpc_client import RpcClient
from .utils import save_json, serialize_decimal, utc_now_iso


class SnapshotService:
    def __init__(
        self,
        settings: Settings,
        balance: BalanceManager | None = None,
        lp: LPService | None = None,
    ) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)

        # Use injected services or create standalone (backward compat)
        if balance is not None and lp is not None:
            self._balance = balance
            self._lp = lp
            self._rpc = lp.rpc
        else:
            rpc = RpcClient(settings)
            self._rpc = rpc
            self._balance = BalanceManager(rpc, None, settings)
            self._lp = LPService.read_only(rpc, settings)

    def _debug(self, message: str) -> None:
        self.logger.debug(message)

    def build(
        self,
        wallet_address: str | None = None,
        *,
        deep_position_search: bool = False,
        include_position_inventory: bool = True,
    ) -> dict[str, Any]:
        wallet = wallet_address or self.settings.wallet_address
        started = perf_counter()
        pool_state = self._lp.get_pool_state()
        self._debug(f"pool state loaded in {perf_counter() - started:.2f}s")
        snapshot: dict[str, Any] = {
            "generated_at": utc_now_iso(),
            "config": {
                "bin_count": self.settings.bin_count,
                "pool_address": self.settings.pool_address,
                "wmnt_address": self.settings.wmnt_address,
                "usdt_address": self.settings.usdt_address,
                "moe_router_address": self.settings.moe_router_address,
                "log_scan_start_block": self.settings.log_scan_start_block,
                "log_scan_chunk_size": self.settings.log_scan_chunk_size,
            },
            "chain": self._rpc.get_chain_state(),
            "pool": pool_state.to_dict(),
            "wallet": None,
            "position": None,
        }

        if wallet:
            wallet_started = perf_counter()
            snapshot["wallet"] = self._wallet_state(wallet, pool_state)
            self._debug(f"wallet state loaded in {perf_counter() - wallet_started:.2f}s")
            position_started = perf_counter()
            position = self._lp.get_position(
                wallet,
                pool_state=pool_state,
                deep_search=deep_position_search,
                include_inventory=include_position_inventory,
            )
            snapshot["position"] = position.to_dict()
            self._debug(f"position state loaded in {perf_counter() - position_started:.2f}s")

        self._debug(f"snapshot build completed in {perf_counter() - started:.2f}s")

        return snapshot

    def _wallet_state(self, wallet_address: str, pool_state: PoolState) -> dict[str, Any]:
        # Use BalanceManager if available, otherwise read directly via RPC
        native_balance = self._balance.get_native_balance(wallet_address)
        wmnt_balance = self._balance.get_erc20_balance(wallet_address, self.settings.wmnt_address)
        usdt_balance = self._balance.get_erc20_balance(wallet_address, self.settings.usdt_address)

        mnt_price = pool_state.mnt_price_usdt
        wallet_value = None
        if mnt_price is not None:
            native_mnt = native_balance.normalized
            wmnt = wmnt_balance.normalized
            usdt = usdt_balance.normalized
            wallet_value = usdt + (native_mnt + wmnt) * mnt_price

        return {
            "address": wallet_address,
            "native_mnt": native_balance.to_dict(),
            "wmnt": wmnt_balance.to_dict(),
            "usdt": usdt_balance.to_dict(),
            "estimated_total_value_usdt": (
                serialize_decimal(wallet_value) if wallet_value is not None else None
            ),
        }

    def save(self, snapshot: dict[str, Any], path: str | None = None) -> str:
        out_path = self.settings.data_dir / "latest_snapshot.json" if path is None else self.settings.data_dir / path
        save_json(out_path, snapshot)
        return str(out_path)
