from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.orchestration import SinglePositionCyclePreparer


def _budget(deployed: str = "0", free: str = "25"):
    return SimpleNamespace(
        total_value_usdt=Decimal("25"),
        deployed_value_usdt=Decimal(deployed),
        free_value_usdt=Decimal(free),
        mnt_price_usdt=Decimal("2"),
        total_mnt=Decimal("10"),
        total_usdt=Decimal("5"),
        deployed_mnt=Decimal("0"),
        deployed_usdt=Decimal("0"),
    )


def _position():
    return SimpleNamespace(
        position_exists=True,
        in_range=True,
        bin_count=23,
        min_bin_id=100,
        max_bin_id=122,
    )


def test_prepare_normalizes_dust_and_records_live_cycle_state():
    from moe_mantle_bot.farm_bot import FarmBot
    from moe_mantle_bot.quant.bias_calculator import BiasCalculator

    settings = MagicMock()
    settings.min_position_size_usdt = 10.0
    lp = MagicMock()
    balance = MagicMock()
    analytics = MagicMock()
    keltner_analyzer = MagicMock()
    keltner_analyzer.analyze_channel_conditions.return_value.to_dict.return_value = {
        "confidence": 0.9,
        "is_ranging": True,
        "bounds": {"width_pct": 1.2},
    }
    keltner_analyzer.candle_fetcher.get_candles.return_value = MagicMock()

    pool_state = SimpleNamespace(active_bin_id=110)
    lp.get_pool_state.return_value = pool_state
    lp.get_position.return_value = _position()
    lp.get_registry.return_value.get_narrow_positions.return_value = []
    lp.get_registry.return_value.get_wide_positions.return_value = []
    balance.get_capital_budget.return_value = _budget()

    preparer = SinglePositionCyclePreparer(
        settings=settings,
        lp=lp,
        balance=balance,
        analytics=analytics,
        keltner_analyzer=keltner_analyzer,
        bias_calculator=BiasCalculator(),
        strategy_override=None,
        safe_float=FarmBot._safe_float,
        calculate_rsi=FarmBot._calculate_rsi,
    )
    preparer.record_analytics_snapshot = MagicMock()
    preparer.log_market_indicators = MagicMock()

    prepared = preparer.prepare("0xW", dry_run=False)

    assert prepared.position.position_exists is False
    assert prepared.keltner["bounds"]["width_pct"] == 1.2
    analytics.finalize_pending_reentries.assert_called_once()
    preparer.record_analytics_snapshot.assert_called_once_with(
        "0xW",
        pool_state=pool_state,
        budget=balance.get_capital_budget.return_value,
    )
    preparer.log_market_indicators.assert_called_once_with(keltner=prepared.keltner)
