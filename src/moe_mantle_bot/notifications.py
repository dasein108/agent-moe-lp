"""
Notification system for Merchant Moe (Mantle) farming bot operations.
Provides comprehensive tracking and alerting for all major bot activities.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import Settings
from .logging_config import get_logger
from .utils import serialize_decimal, utc_now_iso
from .telegram import (
    configure_telegram_from_env,
    send_alert,
    send_lp_alert,
    send_swap_alert,
    send_farm_alert,
    send_error_alert
)


@dataclass(frozen=True)
class BalanceSnapshot:
    """Wallet balance snapshot at a point in time."""
    mnt: Decimal
    wmnt: Decimal
    usdt: Decimal
    total_value_usdt: Decimal | None = None
    
    def to_dict(self) -> dict[str, str]:
        return {
            "mnt": serialize_decimal(self.mnt),
            "wmnt": serialize_decimal(self.wmnt),
            "usdt": serialize_decimal(self.usdt),
            "total_value_usdt": serialize_decimal(self.total_value_usdt) if self.total_value_usdt else None,
        }
    
    def calculate_changes(self, other: BalanceSnapshot) -> dict[str, str]:
        """Calculate balance changes from this snapshot to another."""
        return {
            "mnt": serialize_decimal(other.mnt - self.mnt),
            "wmnt": serialize_decimal(other.wmnt - self.wmnt), 
            "usdt": serialize_decimal(other.usdt - self.usdt),
            "total_value_usdt": serialize_decimal(
                (other.total_value_usdt or Decimal(0)) - (self.total_value_usdt or Decimal(0))
            ) if other.total_value_usdt is not None and self.total_value_usdt is not None else None,
        }


@dataclass(frozen=True)
class OperationInfo:
    """Core operation execution details."""
    action: str
    status: str  # "success" | "failed"
    transaction_hash: str | None = None
    gas_used: int | None = None
    gas_cost_mnt: Decimal | None = None
    error_message: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "transaction_hash": self.transaction_hash,
            "gas_used": self.gas_used,
            "gas_cost_mnt": serialize_decimal(self.gas_cost_mnt) if self.gas_cost_mnt else None,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class PoolState:
    """Pool state information."""
    active_bin: int
    mnt_price_usdt: Decimal
    bin_step: int
    pool_address: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "active_bin": self.active_bin,
            "mnt_price_usdt": serialize_decimal(self.mnt_price_usdt),
            "bin_step": self.bin_step,
            "pool_address": self.pool_address,
        }


@dataclass(frozen=True)
class LPPositionInfo:
    """LP position details."""
    exists: bool
    in_range: bool | None = None
    min_bin: int | None = None
    max_bin: int | None = None
    bin_count: int | None = None
    estimated_mnt: Decimal | None = None
    estimated_usdt: Decimal | None = None
    estimated_value_usdt: Decimal | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "in_range": self.in_range,
            "min_bin": self.min_bin,
            "max_bin": self.max_bin,
            "bin_count": self.bin_count,
            "estimated_mnt": serialize_decimal(self.estimated_mnt) if self.estimated_mnt else None,
            "estimated_usdt": serialize_decimal(self.estimated_usdt) if self.estimated_usdt else None,
            "estimated_value_usdt": serialize_decimal(self.estimated_value_usdt) if self.estimated_value_usdt else None,
        }


class NotificationService:
    """Main notification service for bot operations."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = self._check_notification_config()
        self.telegram_enabled = self._check_telegram_config()
        self.notification_file = settings.data_dir / "notifications.jsonl"
        
        # Initialize Telegram if enabled
        if self.telegram_enabled:
            configure_telegram_from_env()
        
    def _check_notification_config(self) -> bool:
        """Check if notifications are enabled via environment variable."""
        return os.getenv("NOTIFICATIONS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    
    def _check_telegram_config(self) -> bool:
        """Check if Telegram notifications are configured and enabled."""
        if not self.enabled:
            return False
        telegram_enabled = os.getenv("TELEGRAM_NOTIFICATIONS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
        has_token = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
        has_channel = bool(os.getenv("TELEGRAM_CHANNEL_ID"))
        return telegram_enabled and has_token and has_channel
    
    def _ensure_data_dir(self) -> None:
        """Ensure the data directory exists for notification storage."""
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
    
    def send_notification(self, data: dict[str, Any]) -> None:
        """
        Send notification with operation details.
        
        Currently implements file-based notifications.
        Can be extended to support webhooks, Discord, Telegram, etc.
        """
        if not self.enabled:
            return
            
        # Add standard metadata
        notification = {
            "timestamp": utc_now_iso(),
            "bot_version": "moe-mantle-farming-0.1.0",
            **data
        }
        
        # File-based notification storage
        self._write_to_file(notification)
        
        # Console notification (for immediate feedback)
        self._print_to_console(notification)
        
        # Telegram notification (if enabled)
        if self.telegram_enabled:
            self._send_to_telegram(notification)
        
    def _write_to_file(self, notification: dict[str, Any]) -> None:
        """Write notification to JSONL file."""
        try:
            self._ensure_data_dir()
            with self.notification_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(notification, sort_keys=True) + "\n")
        except Exception as exc:
            logger = get_logger("notifications")
            logger.error("Failed to write notification to file", extra={"error": str(exc)})
    
    def _print_to_console(self, notification: dict[str, Any]) -> None:
        """Print notification summary to console."""
        try:
            event_type = notification.get("event_type", "unknown")
            operation = notification.get("operation", {})
            status = operation.get("status", "unknown")
            wallet = notification.get("wallet_address", "unknown")[:10] + "..."
            
            logger = get_logger("notifications")
            extra_data = {
                "event_type": event_type.upper(),
                "status": status.upper(),
                "wallet": wallet
            }
            
            # Show key details based on event type
            if event_type == "lp_created":
                details = notification.get("operation_details", {}).get("lp_create", {})
                amounts = details.get("amounts_added", {})
                extra_data.update({
                    "lp_created_wmnt": amounts.get('wmnt', '0'),
                    "lp_created_usdt": amounts.get('usdt', '0')
                })
                
            elif event_type == "lp_removed":
                details = notification.get("operation_details", {}).get("lp_remove", {})
                amounts = details.get("amounts_received", {})
                extra_data.update({
                    "lp_removed_wmnt": amounts.get('wmnt', '0'),
                    "lp_removed_usdt": amounts.get('usdt', '0')
                })
                
            elif event_type == "swap_executed":
                details = notification.get("operation_details", {}).get("swap", {})
                extra_data.update({
                    "swap_amount_in": details.get('amount_in', '0'),
                    "swap_from_token": details.get('from_token', ''),
                    "swap_amount_out": details.get('amount_out', '0'),
                    "swap_to_token": details.get('to_token', '')
                })
                
            # Show transaction hash if available
            tx_hash = operation.get("transaction_hash")
            if tx_hash:
                extra_data["transaction_hash"] = tx_hash
            
            logger.info(f"[{event_type.upper()}] {status.upper()}", extra=extra_data)
                
        except Exception as exc:
            logger = get_logger("notifications")
            logger.error("Failed to print notification", extra={"error": str(exc)})
    
    def _send_to_telegram(self, notification: dict[str, Any]) -> None:
        """Send human-readable notification to Telegram."""
        try:
            event_type = notification.get("event_type", "unknown")
            operation = notification.get("operation", {})
            status = operation.get("status", "unknown")
            
            # Handle farm cycle errors specially
            if event_type == "farm_cycle_error":
                error_details = notification.get("error_details", {})
                error_type = error_details.get("error_type", "UnknownError")
                error_msg = error_details.get("error_message", "Unknown error")
                cycle = error_details.get("cycle", "?")
                consecutive = error_details.get("consecutive_errors", 0)
                
                msg = (
                    f"❌ <b>Farm Cycle {cycle} Failed</b>\n"
                    f"Error: {error_type}\n"
                    f"Details: {error_msg}\n"
                    f"Consecutive errors: {consecutive}"
                )
                send_error_alert(msg, urgent=False)
                return
            
            # Skip failed operations for Telegram (reduce noise)
            if status == "failed":
                # Get error message with more context
                error_msg = operation.get("error_message")
                action = operation.get("action", "unknown action")
                
                # Build more informative error message
                if error_msg:
                    if error_msg == "Unknown error during LP creation":
                        error_msg = f"LP creation failed"
                    elif error_msg == "Unknown error during LP removal":
                        error_msg = f"LP removal failed"
                    elif error_msg == "Unknown error during rebalance":
                        error_msg = f"Rebalance failed"
                    full_msg = f"{action}: {error_msg}"
                else:
                    full_msg = f"{action}: Operation failed (no details)"
                
                send_error_alert(f"❌ {full_msg}", urgent=False)
                return
            
            # Format message based on event type
            if event_type == "lp_created":
                self._send_lp_created_telegram(notification)
            elif event_type == "lp_removed":
                self._send_lp_removed_telegram(notification)
            elif event_type == "swap_executed":
                self._send_swap_telegram(notification)
            elif event_type == "farm_cycle_complete":
                self._send_farm_cycle_telegram(notification)
            elif event_type == "rebalance_complete":
                self._send_rebalance_telegram(notification)
                
        except Exception as exc:
            logger = get_logger("notifications")
            logger.warning("Failed to send Telegram notification", extra={"error": str(exc)})
    
    def _send_lp_created_telegram(self, notification: dict[str, Any]) -> None:
        """Send LP creation notification to Telegram."""
        details = notification.get("operation_details", {}).get("lp_create", {})
        amounts = details.get("amounts_added", {})
        position = details.get("position_after", {})
        
        wmnt_amount = amounts.get("wmnt", "0")
        usdt_amount = amounts.get("usdt", "0")
        bin_range = f"{position.get('min_bin', 'N/A')}-{position.get('max_bin', 'N/A')}"
        
        message = (
            f"<b>LP Position Created</b>\n"
            f"💰 Added: {wmnt_amount} WMNT + {usdt_amount} USDT\n"
            f"📊 Bin Range: {bin_range}\n"
            f"🎯 Bins: {position.get('bin_count', 0)} active"
        )
        
        # Add transaction hash if available
        tx_hash = notification.get("operation", {}).get("transaction_hash")
        if tx_hash:
            message += f"\n📋 TX: <code>{tx_hash[:16]}...</code>"
        
        send_lp_alert(message, "LP_CREATE")
    
    def _send_lp_removed_telegram(self, notification: dict[str, Any]) -> None:
        """Send LP removal notification to Telegram."""
        details = notification.get("operation_details", {}).get("lp_remove", {})
        amounts = details.get("amounts_received", {})
        
        wmnt_amount = amounts.get("wmnt", "0")
        usdt_amount = amounts.get("usdt", "0")
        
        message = (
            f"<b>LP Position Removed</b>\n"
            f"💸 Received: {wmnt_amount} WMNT + {usdt_amount} USDT"
        )
        
        # Add transaction hash if available
        tx_hash = notification.get("operation", {}).get("transaction_hash")
        if tx_hash:
            message += f"\n📋 TX: <code>{tx_hash[:16]}...</code>"
        
        send_lp_alert(message, "LP_REMOVE")
    
    def _send_swap_telegram(self, notification: dict[str, Any]) -> None:
        """Send swap notification to Telegram."""
        details = notification.get("operation_details", {}).get("swap", {})
        
        from_token = details.get("from_token", "")
        to_token = details.get("to_token", "")
        amount_in = details.get("amount_in", "0")
        amount_out = details.get("amount_out", "0")
        
        message = (
            f"<b>Swap Executed</b>\n"
            f"🔄 {amount_in} {from_token} → {amount_out} {to_token}"
        )
        
        # Add price impact if available
        price_impact = details.get("price_impact_bps")
        if price_impact:
            impact_pct = float(price_impact) / 100
            message += f"\n📉 Price Impact: {impact_pct:.2f}%"
        
        # Add transaction hash if available
        tx_hash = notification.get("operation", {}).get("transaction_hash")
        if tx_hash:
            message += f"\n📋 TX: <code>{tx_hash[:16]}...</code>"
        
        send_swap_alert(message)
    
    def _send_farm_cycle_telegram(self, notification: dict[str, Any]) -> None:
        """Send farm cycle completion notification to Telegram."""
        details = notification.get("farm_details", {})
        cycle_number = details.get("cycle_number", "N/A")
        duration_minutes = details.get("cycle_duration_minutes")
        
        message = f"<b>Farm Cycle #{cycle_number} Complete</b>\n🚜 "
        
        if duration_minutes:
            message += f"Duration: {duration_minutes:.1f}min"
        else:
            message += "Cycle completed successfully"
        
        # Add balance changes if available
        balance_changes = notification.get("balance_changes")
        if balance_changes and any(float(v or 0) != 0 for v in balance_changes.values() if v):
            message += "\n\n<b>Balance Changes:</b>"
            for token, change in balance_changes.items():
                if change and float(change) != 0:
                    change_val = float(change)
                    emoji = "📈" if change_val > 0 else "📉"
                    message += f"\n{emoji} {token.upper()}: {change}"
        
        send_farm_alert(message)
    
    def _send_rebalance_telegram(self, notification: dict[str, Any]) -> None:
        """Send rebalance completion notification to Telegram."""
        details = notification.get("rebalance_details", {})
        operations = details.get("operations_performed", [])
        
        message = "<b>Portfolio Rebalanced</b>\n⚖️ "
        
        if operations:
            message += f"Operations: {', '.join(operations)}"
        else:
            message += "Rebalancing completed"
        
        # Add balance changes summary
        balance_changes = notification.get("balance_changes")
        if balance_changes:
            total_changes = sum(float(v or 0) for v in balance_changes.values() if v)
            if total_changes != 0:
                emoji = "📈" if total_changes > 0 else "📉"
                message += f"\n{emoji} Net change detected"
        
        send_alert("REBALANCE", message)


def create_balance_snapshot(
    mnt: Decimal, 
    wmnt: Decimal, 
    usdt: Decimal, 
    mnt_price_usdt: Decimal | None = None
) -> BalanceSnapshot:
    """Create a balance snapshot with optional total value calculation."""
    total_value_usdt = None
    if mnt_price_usdt is not None:
        mnt_total = mnt + wmnt
        total_value_usdt = (mnt_total * mnt_price_usdt) + usdt
        
    return BalanceSnapshot(
        mnt=mnt,
        wmnt=wmnt, 
        usdt=usdt,
        total_value_usdt=total_value_usdt
    )


def create_pool_state_from_snapshot(pool_snapshot: dict[str, Any]) -> PoolState:
    """Create PoolState from pool snapshot data."""
    # Handle both field names: "pair_address" from executor.get_pool_state() and "pool_address" from other sources
    pool_address = pool_snapshot.get("pool_address") or pool_snapshot.get("pair_address")
    if not pool_address:
        raise ValueError("Pool snapshot must contain either 'pool_address' or 'pair_address' field")
        
    return PoolState(
        active_bin=pool_snapshot["active_bin_id"],
        mnt_price_usdt=Decimal(pool_snapshot["mnt_price_usdt"]),
        bin_step=pool_snapshot["bin_step"],
        pool_address=pool_address,
    )


def create_lp_position_from_snapshot(position_snapshot: dict[str, Any]) -> LPPositionInfo:
    """Create LPPositionInfo from position snapshot data."""
    if not position_snapshot or not position_snapshot.get("position_exists"):
        return LPPositionInfo(exists=False)
        
    return LPPositionInfo(
        exists=True,
        in_range=position_snapshot.get("in_range"),
        min_bin=position_snapshot.get("min_bin_id"),
        max_bin=position_snapshot.get("max_bin_id"),
        bin_count=len(position_snapshot.get("active_bins", [])),
        estimated_mnt=Decimal(position_snapshot["estimated_token_x"]) if position_snapshot.get("estimated_token_x") else None,
        estimated_usdt=Decimal(position_snapshot["estimated_token_y"]) if position_snapshot.get("estimated_token_y") else None,
        estimated_value_usdt=None,  # Can be calculated if needed
    )


def calculate_gas_cost_mnt(gas_used: int, gas_price: int) -> Decimal:
    """Calculate gas cost in MNT from wei amounts."""
    gas_cost_wei = gas_used * gas_price
    return Decimal(gas_cost_wei) / Decimal(10**18)