from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from moe_mantle_bot.tx_sender import PreviewValidationError


def _make_bot():
    from moe_mantle_bot.farm_bot import FarmBot

    bot = MagicMock(spec=FarmBot)
    bot.lp = MagicMock()
    bot.rpc = MagicMock()
    bot.rpc.reconnect = MagicMock()
    bot._log_error = MagicMock()
    bot._notify = MagicMock()
    bot._gas_cost_mnt = MagicMock(return_value=0.0)
    bot._extract_add_liquidity_metrics = MagicMock(return_value={})
    bot.analytics = MagicMock()
    # Pre-entry wallet prep (rebalance + WMNT unwrap) reads wallet + balance.
    bot.wallet = MagicMock()
    bot.wallet.address = "0xTEST"
    bot.balance = MagicMock()
    bot.balance._get_mnt_price.return_value = None  # skip rebalance branch
    bot.balance.get_erc20_balance.return_value = SimpleNamespace(normalized=Decimal("0"))
    bot._create_position_with_retry = FarmBot._create_position_with_retry.__get__(bot)
    return bot


def test_create_position_retry_stops_on_deterministic_preview_guard():
    bot = _make_bot()
    bot.lp.estimate_position_fill.return_value = {"meets_min_fill": True}
    bot.lp.create_position.side_effect = PreviewValidationError(
        action="add_liquidity",
        message="Skipping live add: native MNT headroom is too tight for gas estimation.",
        preview={"status": "native_gas_headroom_too_low"},
    )

    alloc = SimpleNamespace(amount_wmnt=Decimal("100"), amount_usdt=Decimal("0"))

    with patch("moe_mantle_bot.farm_bot.time.sleep") as sleep_mock:
        result = bot._create_position_with_retry(
            strategy="wide",
            alloc=alloc,
            bin_count=29,
            params={},
            dry_run=False,
            timestamp="2026-03-25T00:00:00+00:00",
            max_attempts=3,
        )

    assert result["action"] == "error"
    assert bot.lp.create_position.call_count == 1
    sleep_mock.assert_not_called()


def test_create_position_retry_skips_on_insufficient_expected_fill():
    bot = _make_bot()
    bot.lp.estimate_position_fill.return_value = {"meets_min_fill": True}
    bot.lp.create_position.side_effect = PreviewValidationError(
        action="add_liquidity",
        message="Rejected LP add because planned fill is below minimum position size.",
        preview={"status": "insufficient_expected_fill"},
    )

    alloc = SimpleNamespace(amount_wmnt=Decimal("79.03"), amount_usdt=Decimal("14.48"))

    with patch("moe_mantle_bot.farm_bot.time.sleep") as sleep_mock:
        result = bot._create_position_with_retry(
            strategy="wide",
            alloc=alloc,
            bin_count=20,
            params={},
            dry_run=False,
            timestamp="2026-03-25T00:00:00+00:00",
            max_attempts=3,
        )

    assert result["action"] == "skip_wide"
    assert result["preview_status"] == "insufficient_expected_fill"
    assert bot.lp.create_position.call_count == 1
    sleep_mock.assert_not_called()
    bot._notify.assert_not_called()


def test_create_position_retry_skips_before_add_when_estimated_fill_is_subminimum():
    bot = _make_bot()
    bot.lp.estimate_position_fill.return_value = {
        "active_mode": "y_only",
        "used_value_usdt": Decimal("3.84"),
        "requested_value_usdt": Decimal("16.31"),
        "min_position_size_usdt": Decimal("10"),
        "meets_min_fill": False,
    }

    alloc = SimpleNamespace(amount_wmnt=Decimal("79.03"), amount_usdt=Decimal("14.48"))

    with patch("moe_mantle_bot.farm_bot.time.sleep") as sleep_mock:
        result = bot._create_position_with_retry(
            strategy="wide",
            alloc=alloc,
            bin_count=21,
            params={},
            dry_run=False,
            timestamp="2026-03-25T00:00:00+00:00",
            max_attempts=3,
        )

    assert result["action"] == "skip_wide"
    assert result["preview_status"] == "insufficient_expected_fill"
    assert result["lp_mode"] == "y_only"
    assert result["expected_fill_value_usdt"] == 3.84
    assert bot.lp.create_position.call_count == 0
    sleep_mock.assert_not_called()
    bot._notify.assert_not_called()


def test_log_error_includes_native_gas_headroom_breakdown(caplog):
    from moe_mantle_bot.farm_bot import FarmBot

    bot = MagicMock(spec=FarmBot)
    bot._LB_ERRORS = FarmBot._LB_ERRORS
    bot._decode_revert = FarmBot._decode_revert.__get__(bot)
    bot._log_error = FarmBot._log_error.__get__(bot)

    err = PreviewValidationError(
        action="add_liquidity",
        message="Skipping live add: native MNT headroom is too tight for gas estimation.",
        preview={
            "status": "native_gas_headroom_too_low",
            "native_balance_mnt": "25",
            "native_needed_mnt": "22",
            "gas_reserve_mnt": "5",
            "total_needed_mnt": "27",
            "shortfall_mnt": "2",
        },
        context={"preflight": {"amount_wmnt": "100", "bin_count": 31}},
    )

    with caplog.at_level("ERROR"):
        bot._log_error(err, "Attempt 1/1 failed")

    assert "Native gas headroom: wallet_native=25 MNT lp_msg_value=22 MNT reserve=5 MNT total_needed=27 MNT shortfall=2 MNT" in caplog.text
