from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
from moe_mantle_bot.orchestration import SinglePositionCyclePreparer


def _make_preparer():
    from moe_mantle_bot.farm_bot import FarmBot
    from moe_mantle_bot.quant.bias_calculator import BiasCalculator

    return SinglePositionCyclePreparer(
        settings=MagicMock(),
        lp=MagicMock(),
        balance=MagicMock(),
        analytics=MagicMock(),
        keltner_analyzer=MagicMock(),
        bias_calculator=BiasCalculator(),
        strategy_override=None,
        safe_float=FarmBot._safe_float,
        calculate_rsi=FarmBot._calculate_rsi,
    )


def _candles(count: int = 80) -> pd.DataFrame:
    closes = [1.0 + (i * 0.01) for i in range(count)]
    return pd.DataFrame({"close": closes})


def test_build_market_indicator_snapshot_includes_bias_and_averages():
    bot = _make_preparer()

    snapshot = bot.build_market_indicator_snapshot(
        _candles(),
        keltner={
            "confidence": 0.87,
            "is_ranging": True,
            "bounds": {"width_pct": 3.4},
        },
    )

    assert snapshot["price"] == 1.79
    assert snapshot["orderflow_status"] == "unavailable_no_live_stream"
    assert snapshot["bias_direction"] in {"BULL", "BEAR", "NEUTRAL"}
    assert snapshot["bias_confidence"] >= 0
    assert 0 <= snapshot["rsi_14"] <= 100
    assert snapshot["rsi_14"] > 50
    assert snapshot["sma_20"] < snapshot["price"]
    assert snapshot["sma_50"] < snapshot["price"]
    assert snapshot["ema_20"] < snapshot["price"]
    assert snapshot["ema_50"] < snapshot["price"]
    assert snapshot["keltner_width_pct"] == 3.4
    assert snapshot["keltner_confidence"] == 0.87
    assert snapshot["keltner_is_ranging"] is True


def test_log_market_indicators_emits_cycle_monitor_line():
    bot = _make_preparer()
    bot.keltner_analyzer = MagicMock()
    bot.keltner_analyzer.candle_fetcher.get_candles.return_value = _candles()

    with patch("moe_mantle_bot.orchestration.cycle_preparer.logger.info") as info_log:
        bot.log_market_indicators(
            keltner={
                "confidence": 0.91,
                "is_ranging": False,
                "bounds": {"width_pct": 4.2},
            }
        )

    message = info_log.call_args[0][0]
    assert "Market indicators:" in message
    assert "rsi14=" in message
    assert "sma20=" in message
    assert "ema20=" in message
    assert "orderflow=unavailable_no_live_stream" in message
    assert "keltner_width=4.20%" in message
