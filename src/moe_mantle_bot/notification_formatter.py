"""
NotificationFormatter — Telegram message formatting, decoupled from FarmBot.

Pure formatting: takes data, returns strings. No RPC, no side effects.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def format_lp_created(
    *,
    strategy: str,
    amount_line: str,
    bin_count: int,
    mode: str,
    range_line: str,
    mtf_summary: str,
    gas_cost: float,
) -> str:
    lines = [
        f"✅ <b>{strategy.upper()} position created</b>",
        amount_line,
        f"📊 {bin_count} bins | mode: {mode}",
    ]
    if range_line:
        lines.append(range_line)
    lines.append(f"🌐 {mtf_summary}")
    lines.append(f"⛽ Gas: {gas_cost:.4f} MNT")
    return "\n".join(lines)


def format_lp_removed(
    *,
    strategy: str,
    min_bin: int,
    max_bin: int,
    stats_lines: list[str],
    gas_cost: float,
) -> str:
    return (
        f"🔄 <b>{strategy.capitalize()} removed</b> (bins {min_bin}-{max_bin})\n"
        + "\n".join(stats_lines)
        + f"\n⛽ Gas: {gas_cost:.4f} MNT"
    )


def format_strategy_change(
    *,
    old_state: str,
    new_state: str,
    reason: str,
    mtf_summary: str,
    position_line: str = "",
) -> str:
    lines = [
        f"🔀 <b>Strategy changed: {old_state} → {new_state}</b>",
        f"📝 {reason}",
        f"🌐 {mtf_summary}",
    ]
    if position_line:
        lines.append(position_line)
    return "\n".join(lines)


def format_cycle_error(error: Exception) -> str:
    return f"❌ <b>Cycle failed</b>\n{type(error).__name__}: {str(error)[:200]}"


def format_position_failed(strategy: str, attempts: int, error: str) -> str:
    return (
        f"❌ <b>{strategy.upper()} position FAILED</b>\n"
        f"Attempts: {attempts}\n"
        f"Error: {error[:200]}"
    )


def format_status_report(
    *,
    state: str,
    mtf_summary: str,
    native_mnt: float,
    wmnt: float,
    usdt: float,
    total_value_usdt: float,
    mnt_price: float,
    deployed_value_usdt: float,
    free_value_usdt: float,
    positions: list[dict[str, Any]],
    roi: dict[str, Any] | None = None,
) -> str:
    lines = [
        f"📊 <b>Status Report</b>",
        f"",
        f"🤖 <b>State</b>: {state}",
        f"🌐 {mtf_summary}",
        f"",
        f"💰 <b>Wallet</b>",
        f"  MNT: {native_mnt:.2f} + {wmnt:.2f} WMNT",
        f"  USDT: ${usdt:.2f}",
        f"  Total: ${total_value_usdt:.2f} | MNT: ${mnt_price:.4f}",
        f"",
        f"📈 <b>Deployed</b>: ${deployed_value_usdt:.2f}",
        f"💵 <b>Free</b>: ${free_value_usdt:.2f}",
    ]

    for p in positions:
        lines.append(f"")
        label = p["label"]
        if p.get("exists"):
            status = "✅ in range" if p.get("in_range") else "⚠️ OUT OF RANGE"
            range_str = f" | {p['price_lo']}–{p['price_hi']}" if p.get("price_lo") else ""
            lines.append(f"{label}: {p['bin_count']} bins ({p['min_bin']}–{p['max_bin']})")
            lines.append(f"  {status}{range_str}")
        else:
            lines.append(f"{label}: none")

    if roi and "error" not in roi:
        lines.append(f"")
        lines.append(f"📈 <b>Performance ({roi['period_days']}d)</b>")
        lines.append(f"  ROI: {roi['roi_pct']:+.2f}% | APR: {roi['apr_pct']:+.1f}%")
        lines.append(f"  Gas: {roi['gas_spent_mnt']:.2f} MNT | Ops: {roi['operations']}")
        if roi.get('vs_hodl_usdt', 0) != 0:
            lines.append(f"  vs HODL: ${roi['vs_hodl_usdt']:+.2f}")

    return "\n".join(lines)
