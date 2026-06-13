from __future__ import annotations

from unittest.mock import MagicMock

from moe_mantle_bot.models import ExecutionResult


def _make_bot():
    from moe_mantle_bot.farm_bot import FarmBot

    bot = MagicMock(spec=FarmBot)
    bot.settings = MagicMock()
    bot.settings.wmnt_address = "0xwmnt"
    bot._safe_float = FarmBot._safe_float
    bot._extract_add_liquidity_metrics = FarmBot._extract_add_liquidity_metrics.__get__(bot)
    return bot


def test_extract_add_liquidity_metrics_prefers_final_distribution_details():
    bot = _make_bot()

    result = ExecutionResult(
        action="add_liquidity",
        tx_hash="0xabc",
        dry_run=False,
        details={
            "amount_wmnt": "3168.67",
            "amount_usdt": "0.46",
            "distribution_details": {
                "active_mode": "y_only",
                "x_used": "12.34",
                "y_used": "0.46",
                "x_refund": "3156.33",
                "y_refund": "0",
            },
            "preflight": {
                "pool": {
                    "spot_price_mnt_usdt": "0.0225",
                    "token_x": "0xwmnt",
                    "token_y": "0xusdt",
                },
                "liquidity_parameters": {
                    "distribution_details": {
                        "active_mode": "x_only_onesided",
                        "x_used": "3168.67",
                        "y_used": "0",
                        "x_refund": "0",
                        "y_refund": "0.46",
                    }
                },
            },
        },
    )

    metrics = bot._extract_add_liquidity_metrics([result])

    assert metrics["lp_mode"] == "y_only"
    assert metrics["used_mnt"] == 12.34
    assert metrics["used_usdt"] == 0.46
    assert metrics["expected_refund_mnt"] == 3156.33
    assert metrics["fill_pct_mnt"] < 1.0
