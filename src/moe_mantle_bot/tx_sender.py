"""Transaction builder, signer, and broadcaster.

Handles: gas estimation, signing, broadcasting, receipt waiting, approvals.
No business logic — just tx mechanics.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from time import time
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError
from .config import Settings
from .models import ExecutionResult, TransactionGasReport
from .rpc_client import RpcClient
from .wallet_store import WalletRecord


TRANSIENT_RPC_MESSAGE_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
    "503",
    "502",
    "connection reset",
    "connection aborted",
    "header not found",
    "block not found",
    "resource unavailable",
)


class TransactionExecutionError(RuntimeError):
    def __init__(
        self,
        *,
        action: str,
        stage: str,
        message: str,
        retryable: bool,
        tx: dict[str, Any] | None = None,
        tx_hash: str | None = None,
        gas: TransactionGasReport | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.stage = stage
        self.retryable = retryable
        self.tx = tx
        self.tx_hash = tx_hash
        self.gas = gas or TransactionGasReport()
        self.context = context or {}

        payload = {
            "action": self.action,
            "context": _sanitize_failure_context(self.context),
            "message": str(self),
            "stage": self.stage,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.failure_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "context": self.context,
            "error_type": type(self).__name__,
            "failure_fingerprint": self.failure_fingerprint,
            "gas_expected": self.gas.gas_expected,
            "gas_limit": self.gas.gas_limit,
            "gas_note": self.gas.note,
            "gas_used": self.gas.gas_used,
            "message": str(self),
            "retryable": self.retryable,
            "stage": self.stage,
            "tx": self.tx,
            "tx_hash": self.tx_hash,
        }


class PreviewValidationError(RuntimeError):
    def __init__(
        self,
        *,
        action: str,
        message: str,
        preview: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.preview = preview or {}
        self.context = context or {}
        payload = {
            "action": self.action,
            "context": _sanitize_failure_context(self.context),
            "message": str(self),
            "preview": _sanitize_failure_context(self.preview),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        self.failure_fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "context": self.context,
            "error_type": type(self).__name__,
            "failure_fingerprint": self.failure_fingerprint,
            "message": str(self),
            "preview": self.preview,
        }


def serialize_execution_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TransactionExecutionError):
        return exc.to_dict()
    if isinstance(exc, PreviewValidationError):
        return exc.to_dict()
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }


def _format_chain_error(exc: Exception) -> str:
    if isinstance(exc, ContractLogicError):
        data = getattr(exc, "data", None)
        if data not in (None, "", "0x", {}):
            return f"{exc.message or 'execution reverted'} (data={data})"
        return exc.message or "execution reverted"
    if isinstance(exc, Web3RPCError):
        return exc.message
    return str(exc)


def _is_retryable_web3_error(exc: Exception) -> bool:
    if isinstance(exc, ContractLogicError):
        return False
    if isinstance(exc, Web3RPCError):
        message = exc.message.lower()
        return any(marker in message for marker in TRANSIENT_RPC_MESSAGE_MARKERS)
    return False


def _revert_hint(exc: Exception) -> str:
    if isinstance(exc, ContractLogicError):
        return (
            " The node simulated a contract revert, which usually means the call "
            "is invalid for the current on-chain state and should not be retried unchanged."
        )
    return ""


def _sanitize_failure_context(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"deadline", "gas", "gasPrice", "nonce", "tx_hash"}:
                continue
            if key == "data" and isinstance(item, str):
                result["data_selector"] = item[:10]
                continue
            result[key] = _sanitize_failure_context(item)
        return result
    if isinstance(value, list):
        return [_sanitize_failure_context(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_failure_context(item) for item in value]
    return value


class TxSender:
    """Transaction builder, signer, and broadcaster."""

    def __init__(self, rpc: RpcClient, wallet: WalletRecord, settings: Settings) -> None:
        self.rpc = rpc
        self.settings = settings
        self.account = rpc.w3.eth.account.from_key(wallet.private_key)
        self.wallet_address = self.account.address

    @property
    def w3(self) -> Web3:
        return self.rpc.w3

    def deadline(self) -> int:
        return int(time()) + self.settings.tx_deadline_seconds

    def gas_price_params(self) -> dict[str, int]:
        return {"gasPrice": int(self.w3.eth.gas_price)}

    def _ensure_native_balance_headroom(
        self,
        *,
        action: str,
        tx: dict[str, Any],
        gas_expected: int | None,
        context: dict[str, Any] | None = None,
    ) -> None:
        gas_limit = int(tx.get("gas") or 0)
        gas_price = int(tx.get("gasPrice") or 0)
        value = int(tx.get("value") or 0)
        required_wei = value + gas_limit * gas_price
        balance_wei = int(self.w3.eth.get_balance(self.wallet_address, block_identifier="pending"))
        if required_wei <= 0 or balance_wei >= required_wei:
            return

        shortfall_wei = required_wei - balance_wei
        balance_mnt = Decimal(balance_wei) / Decimal(10**18)
        required_mnt = Decimal(required_wei) / Decimal(10**18)
        shortfall_mnt = Decimal(shortfall_wei) / Decimal(10**18)
        value_mnt = Decimal(value) / Decimal(10**18)
        gas_needed_mnt = Decimal(gas_limit * gas_price) / Decimal(10**18)

        raise TransactionExecutionError(
            action=action,
            stage="native_balance_precheck",
            message=(
                f"{action} skipped before broadcast: insufficient native MNT for tx value + gas "
                f"(balance={balance_mnt:.6f} MNT required={required_mnt:.6f} MNT "
                f"shortfall={shortfall_mnt:.6f} MNT)"
            ),
            retryable=False,
            tx=tx,
            gas=TransactionGasReport(
                gas_expected=gas_expected,
                gas_limit=gas_limit,
                note="Blocked before broadcast because wallet native balance cannot cover tx value + gas.",
            ),
            context={
                **(context or {}),
                "native_balance_mnt": f"{balance_mnt:.6f}",
                "required_native_mnt": f"{required_mnt:.6f}",
                "shortfall_mnt": f"{shortfall_mnt:.6f}",
                "tx_value_mnt": f"{value_mnt:.6f}",
                "gas_needed_mnt": f"{gas_needed_mnt:.6f}",
                "gas_price_wei": gas_price,
            },
        )

    def build_tx(
        self,
        function: Any,
        *,
        action: str,
        value: int = 0,
        estimate_gas: bool = True,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int | None]:
        nonce = self.w3.eth.get_transaction_count(self.wallet_address, block_identifier="pending")
        base_tx = {
            "from": self.wallet_address,
            "chainId": self.settings.chain_id,
            "nonce": nonce,
            "value": value,
            **self.gas_price_params(),
        }
        base_tx["gas"] = 0
        try:
            tx = function.build_transaction(base_tx)
        except (ContractLogicError, Web3RPCError) as exc:
            raise TransactionExecutionError(
                action=action,
                stage="build_transaction",
                message=f"{action} failed while building the transaction: {_format_chain_error(exc)}{_revert_hint(exc)}",
                retryable=_is_retryable_web3_error(exc),
                tx=base_tx,
                gas=TransactionGasReport(note="transaction build failed before gas estimation"),
                context=context,
            ) from exc
        if not estimate_gas:
            return tx, None
        try:
            estimate = self.w3.eth.estimate_gas({k: v for k, v in tx.items() if k != "gas"})
        except (ContractLogicError, Web3RPCError) as exc:
            raise TransactionExecutionError(
                action=action,
                stage="estimate_gas",
                message=f"{action} failed during gas estimation: {_format_chain_error(exc)}{_revert_hint(exc)}",
                retryable=_is_retryable_web3_error(exc),
                tx=tx,
                gas=TransactionGasReport(
                    gas_used=None,
                    gas_expected=None,
                    gas_limit=None,
                    note="Gas estimation reverted before the node returned a gas value.",
                ),
                context=context,
            ) from exc
        tx["gas"] = int(estimate * 1.2)
        return tx, estimate

    def send(
        self,
        action: str,
        function: Any,
        *,
        value: int = 0,
        dry_run: bool = False,
        details: dict[str, Any] | None = None,
        gas_limit: int | None = None,
    ) -> ExecutionResult:
        payload = dict(details or {})
        if gas_limit is not None and not dry_run:
            # Skip gas estimation — use fixed gas limit.
            # Useful when gas estimation's static call fails due to stale state
            # (e.g., active bin moved between getActiveId and estimate_gas).
            tx, gas_expected = self.build_tx(
                function, action=action, value=value,
                estimate_gas=False, context=payload,
            )
            tx["gas"] = gas_limit
            gas_expected = gas_limit
        else:
            tx, gas_expected = self.build_tx(
                function, action=action, value=value,
                estimate_gas=not dry_run, context=payload,
            )
        payload["tx"] = tx
        if dry_run:
            return ExecutionResult(action=action, tx_hash=None, dry_run=True, details=payload)

        self._ensure_native_balance_headroom(
            action=action,
            tx=tx,
            gas_expected=gas_expected,
            context=payload,
        )

        signed = self.account.sign_transaction(tx)
        try:
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        except (ContractLogicError, Web3RPCError) as exc:
            raise TransactionExecutionError(
                action=action,
                stage="send_raw_transaction",
                message=f"{action} failed while broadcasting: {_format_chain_error(exc)}{_revert_hint(exc)}",
                retryable=_is_retryable_web3_error(exc),
                tx=tx,
                gas=TransactionGasReport(
                    gas_expected=gas_expected,
                    gas_limit=tx.get("gas"),
                    note="Broadcast failed after gas estimation succeeded.",
                ),
                context=payload,
            ) from exc
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        except Web3RPCError as exc:
            raise TransactionExecutionError(
                action=action,
                stage="wait_for_receipt",
                message=f"{action} failed while waiting for the receipt: {_format_chain_error(exc)}",
                retryable=_is_retryable_web3_error(exc),
                tx=tx,
                tx_hash=tx_hash.hex(),
                gas=TransactionGasReport(
                    gas_expected=gas_expected,
                    gas_limit=tx.get("gas"),
                    note="Receipt wait failed after the transaction was broadcast.",
                ),
                context=payload,
            ) from exc
        payload["receipt"] = {
            "status": receipt.status,
            "block_number": receipt.blockNumber,
            "gas_used": receipt.gasUsed,
        }
        if receipt.status != 1:
            raise TransactionExecutionError(
                action=action,
                stage="receipt_status",
                message=f"{action} transaction {receipt.transactionHash.hex()} reverted on-chain with status=0",
                retryable=False,
                tx=tx,
                tx_hash=receipt.transactionHash.hex(),
                gas=TransactionGasReport(
                    gas_used=receipt.gasUsed,
                    gas_expected=gas_expected,
                    gas_limit=tx.get("gas"),
                    note="Transaction was mined but reverted on-chain.",
                ),
                context=payload,
            )
        return ExecutionResult(
            action=action,
            tx_hash=receipt.transactionHash.hex(),
            dry_run=False,
            details=payload,
        )

    _MAX_UINT256 = (1 << 256) - 1

    def ensure_erc20_approval(
        self, token_address: str, spender: str, required_amount: int, *, dry_run: bool
    ) -> ExecutionResult | None:
        token = self.rpc.get_erc20_contract(token_address)
        allowance = int(token.functions.allowance(self.wallet_address, spender).call())
        if allowance >= required_amount:
            return None
        # Approve max uint256 to avoid re-approval on every position creation
        function = token.functions.approve(spender, self._MAX_UINT256)
        return self.send(
            "approve",
            function,
            dry_run=dry_run,
            details={"token": token_address, "required_amount": str(required_amount),
                     "approved_amount": "MAX_UINT256"},
        )

    def ensure_pair_approval(
        self, pair_contract: Any, spender: str, *, dry_run: bool
    ) -> ExecutionResult | None:
        approved = bool(pair_contract.functions.isApprovedForAll(self.wallet_address, spender).call())
        if approved:
            return None
        function = pair_contract.functions.approveForAll(spender, True)
        return self.send(
            "approve_pair",
            function,
            dry_run=dry_run,
            details={"pair": pair_contract.address},
        )
