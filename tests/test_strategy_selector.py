"""Tests for Phase 4: Strategy selector."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest


def _make_bot(strategy_override=None):
    """Create a mock bot with the real select_strategy method."""
    from moe_mantle_bot.farm_bot import FarmBot
    from moe_mantle_bot.quant.mtf_analyzer import MTFAnalysis, TimeframeSignal
    from moe_mantle_bot.strategies.engine import StrategyEngine
    bot = MagicMock(spec=FarmBot)
    bot.strategy_override = strategy_override
    bot.settings = MagicMock()
    bot.settings.min_position_size_usdt = 10.0
    bot.settings.min_top_up_fill_usdt = 5.0
    bot.settings.min_top_up_free_value_usdt = 10.0
    bot.settings.wide_confidence_threshold = 0.5
    bot.lp = MagicMock()
    # Default MTF: neutral ranging market, not overbought/oversold
    bot.mtf_analyzer = MagicMock()
    bot.mtf_analyzer.analyze.return_value = MTFAnalysis(
        tf_5m=TimeframeSignal("5m", 0.025, 50.0, None, None, None, None, "NEUTRAL", 0.5, 0.4),
        tf_1h=TimeframeSignal("1h", 0.025, 50.0, None, None, None, None, "NEUTRAL", 1.5, 0.8),
        tf_4h=TimeframeSignal("4h", 0.025, 50.0, None, None, None, None, "NEUTRAL", 2.5, 1.5),
        regime="RANGING", regime_confidence=0.7,
        higher_tf_bias="NEUTRAL", overbought=False, oversold=False, daily_atr_pct=3.0,
    )
    # StrategyEngine — pure logic, no mocks needed
    bot.strategy_engine = StrategyEngine(wide_confidence_threshold=0.5, min_top_up_free_value_usdt=10.0)
    # Services needed by select_strategy
    bot.balance = MagicMock()
    from moe_mantle_bot.models import CapitalBudget
    bot.balance.get_capital_budget.return_value = CapitalBudget(
        total_mnt=Decimal(100), total_usdt=Decimal(10),
        deployed_mnt=Decimal(0), deployed_usdt=Decimal(0),
        free_mnt=Decimal(50), free_usdt=Decimal(10),
        gas_reserve_mnt=Decimal(30), mnt_price_usdt=Decimal("0.025"),
    )
    bot.wallet = MagicMock()
    bot.wallet.address = "0xTEST"
    bot.wide_range_manager = MagicMock()
    bot.wide_range_manager.calculate_wide_range_params.return_value = {"bin_count": 50}
    reg = MagicMock()
    reg.get_wide_positions.return_value = []
    reg.get_narrow_positions.return_value = []
    bot.lp.get_registry.return_value = reg
    bot.select_strategy = FarmBot.select_strategy.__get__(bot)
    bot._select_entry_strategy_without_position = FarmBot._select_entry_strategy_without_position.__get__(bot)
    bot._build_market_state = FarmBot._build_market_state.__get__(bot)
    bot._build_wallet_composition = FarmBot._build_wallet_composition.__get__(bot)
    bot._build_position_snapshot = FarmBot._build_position_snapshot.__get__(bot)
    bot._resolve_top_up_strategy = FarmBot._resolve_top_up_strategy.__get__(bot)
    bot._top_up_expected_fill_is_viable = FarmBot._top_up_expected_fill_is_viable.__get__(bot)
    bot._should_exit_early = FarmBot._should_exit_early
    return bot


def _position(exists=False, in_range=True, bin_count=0, active_bin_id=None,
              min_bin_id=None, max_bin_id=None):
    pos = MagicMock()
    pos.position_exists = exists
    pos.in_range = in_range
    pos.bin_count = bin_count
    # For OOR testing, use numeric values so drift computation works
    pos.active_bin_id = active_bin_id
    pos.min_bin_id = min_bin_id
    pos.max_bin_id = max_bin_id
    # Prevent dust-detection from reclassifying exists → False
    pos.inventory_included = False
    return pos


class TestSelectStrategy:
    def _select(self, position, keltner_analysis=None, pool_state=None):
        bot = _make_bot()
        return bot.select_strategy(position, keltner_analysis=keltner_analysis, pool_state=pool_state)

    def test_hold_when_in_range(self):
        assert self._select(_position(exists=True, in_range=True)) == "hold"

    def test_exit_and_reenter_when_out_of_range(self):
        # Position drifted 50 bins beyond range with market ready for new entry.
        # active_bin comes from pool_state, not position.
        pos = _position(
            exists=True, in_range=False,
            min_bin_id=8326500, max_bin_id=8326700,
        )
        pool = MagicMock()
        pool.active_bin_id = 8326750  # 50 bins above range
        assert self._select(pos, pool_state=pool) == "exit_and_reenter"

    def test_dormant_oor_small_drift(self):
        # Drift within tolerance → hold dormant
        pos = _position(exists=True, in_range=False, min_bin_id=8326500, max_bin_id=8326700)
        pool = MagicMock()
        pool.active_bin_id = 8326710  # 10 bins above range (< 15 tolerance)
        assert self._select(pos, pool_state=pool) == "hold"

    def test_narrow_default_no_position(self):
        assert self._select(_position(exists=False)) == "narrow"

    def test_narrow_when_no_keltner(self):
        assert self._select(_position(exists=False), keltner_analysis=None) == "narrow"

    def test_narrow_when_keltner_low_confidence(self):
        keltner = {"confidence": 0.3, "is_ranging": True}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "narrow"

    def test_wide_when_keltner_at_threshold_with_ranging_mtf(self):
        # confidence=0.5 + MTF ranging boost (+0.15) = 0.65 > 0.5 threshold → wide
        keltner = {"confidence": 0.5, "is_ranging": True}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "wide"

    def test_narrow_when_keltner_below_threshold(self):
        # confidence=0.3 + MTF ranging boost (+0.15) = 0.45 < 0.5 threshold → narrow
        keltner = {"confidence": 0.3, "is_ranging": True}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "narrow"

    def test_wide_when_keltner_above_threshold_ranging(self):
        keltner = {"confidence": 0.6, "is_ranging": True}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "wide"

    def test_wide_when_keltner_high_confidence_ranging(self):
        keltner = {"confidence": 0.9, "is_ranging": True}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "wide"

    def test_narrow_when_keltner_high_confidence_not_ranging(self):
        keltner = {"confidence": 0.9, "is_ranging": False}
        assert self._select(_position(exists=False), keltner_analysis=keltner) == "narrow"

    def test_hold_when_trending_up_and_overbought(self):
        from moe_mantle_bot.quant.mtf_analyzer import MTFAnalysis, TimeframeSignal
        bot = _make_bot()
        bot.mtf_analyzer.analyze.return_value = MTFAnalysis(
            tf_5m=TimeframeSignal("5m", 0.025, 50.0, None, None, None, None, "BULL", 0.5, 0.4),
            tf_1h=TimeframeSignal("1h", 0.025, 92.0, None, None, None, None, "BULL", 2.0, 0.9),
            tf_4h=TimeframeSignal("4h", 0.025, 94.0, None, None, None, None, "BULL", 3.5, 1.7),
            regime="TRENDING_UP", regime_confidence=1.0,
            higher_tf_bias="BULL", overbought=True, oversold=False, daily_atr_pct=9.0,
        )
        keltner = {"confidence": 0.8, "is_ranging": True}
        assert bot._select_entry_strategy_without_position(keltner) == "hold"

    def test_wide_when_high_daily_atr(self):
        from moe_mantle_bot.quant.mtf_analyzer import MTFAnalysis, TimeframeSignal
        bot = _make_bot()
        bot.mtf_analyzer.analyze.return_value = MTFAnalysis(
            tf_5m=TimeframeSignal("5m", 0.025, 50.0, None, None, None, None, "NEUTRAL", 0.8, 0.5),
            tf_1h=TimeframeSignal("1h", 0.025, 55.0, None, None, None, None, "NEUTRAL", 3.0, 1.2),
            tf_4h=TimeframeSignal("4h", 0.025, 50.0, None, None, None, None, "NEUTRAL", 4.0, 2.0),
            regime="RANGING", regime_confidence=0.6,
            higher_tf_bias="NEUTRAL", overbought=False, oversold=False, daily_atr_pct=9.0,
        )
        keltner = {"confidence": 0.3, "is_ranging": True}
        assert bot._select_entry_strategy_without_position(keltner) == "wide"


class TestCalculateWideRangeParams:
    def test_default_bin_count(self):
        from moe_mantle_bot.quant.wide_range_lp_manager import WideRangeLPManager
        settings = MagicMock()
        settings.get_wide_distribution_params.return_value = {"distribution_shape": "uniform"}
        mgr = WideRangeLPManager(settings)
        params = mgr.calculate_wide_range_params()
        assert params["bin_count"] == 100  # fallback ~5% range
        assert params["target_pct"] == 0.9

    def test_bin_count_from_keltner_width(self):
        from moe_mantle_bot.quant.wide_range_lp_manager import WideRangeLPManager
        settings = MagicMock()
        settings.get_wide_distribution_params.return_value = {"distribution_shape": "uniform"}
        mgr = WideRangeLPManager(settings)
        # 3% width × 2.5 / 0.05 = 150 bins. from_sqrt=30*sqrt(3)=51. from_atr=0.
        params = mgr.calculate_wide_range_params({"width_pct": 3.0})
        assert params["bin_count"] == 150

    def test_bin_count_clamped_min(self):
        from moe_mantle_bot.quant.wide_range_lp_manager import WideRangeLPManager
        settings = MagicMock()
        settings.get_wide_distribution_params.return_value = {}
        mgr = WideRangeLPManager(settings)
        # 0.5% width → from_keltner=25, from_sqrt=21, clamped to min 40
        params = mgr.calculate_wide_range_params({"width_pct": 0.5})
        assert params["bin_count"] == 40

    def test_bin_count_clamped_max(self):
        from moe_mantle_bot.quant.wide_range_lp_manager import WideRangeLPManager
        settings = MagicMock()
        settings.get_wide_distribution_params.return_value = {}
        mgr = WideRangeLPManager(settings)
        # 30% × 2.5 / 0.05 = 1500, clamped to max 200
        params = mgr.calculate_wide_range_params({"width_pct": 30.0})
        assert params["bin_count"] == 200  # capped at 200


class TestResolveTopUpStrategy:
    def test_returns_none_when_free_capital_below_minimum(self):
        bot = _make_bot(strategy_override="wide")
        position = SimpleNamespace(position_exists=True, in_range=True)
        budget = SimpleNamespace(free_value_usdt=Decimal("9.99"))

        assert bot._resolve_top_up_strategy("0xabc", position, budget) is None

    def test_prefers_forced_wide_strategy_for_top_up(self):
        bot = _make_bot(strategy_override="wide")
        position = SimpleNamespace(position_exists=True, in_range=True)
        budget = SimpleNamespace(free_value_usdt=Decimal("25"))
        # Top-ups disabled — returns None
        assert bot._resolve_top_up_strategy("0xabc", position, budget) is None

    def test_uses_registry_strategy_when_override_absent(self):
        bot = _make_bot()
        reg = MagicMock()
        reg.get_narrow_positions.return_value = []
        reg.get_wide_positions.return_value = [SimpleNamespace()]
        bot.lp.get_registry.return_value = reg
        position = SimpleNamespace(position_exists=True, in_range=True)
        budget = SimpleNamespace(free_value_usdt=Decimal("25"))
        # Top-ups disabled — returns None
        assert bot._resolve_top_up_strategy("0xabc", position, budget) is None


class TestTopUpExpectedFillGate:
    def test_blocks_top_up_when_expected_fill_is_below_minimum(self):
        bot = _make_bot()
        bot.lp.estimate_position_fill.return_value = {
            "active_mode": "y_only",
            "used_value_usdt": Decimal("1.50"),
            "requested_value_usdt": Decimal("16.30"),
            "min_position_size_usdt": Decimal("10"),
            "meets_min_fill": False,
        }

        ok, estimate = bot._top_up_expected_fill_is_viable(
            strategy="wide",
            alloc=SimpleNamespace(amount_wmnt=Decimal("79.03"), amount_usdt=Decimal("14.48")),
            bin_count=21,
            params=None,
        )

        assert ok is False  # $1.50 < $2.00 min_top_up_fill_usdt
        assert estimate["active_mode"] == "y_only"

    def test_allows_top_up_when_fill_above_top_up_minimum(self):
        """Top-ups use lower $5 min (min_top_up_fill_usdt) since position already exists."""
        bot = _make_bot()
        bot.lp.estimate_position_fill.return_value = {
            "active_mode": "y_only",
            "used_value_usdt": Decimal("6.50"),
            "requested_value_usdt": Decimal("13.40"),
            "min_position_size_usdt": Decimal("10"),
            "meets_min_fill": False,  # would fail old $10 check
        }

        ok, estimate = bot._top_up_expected_fill_is_viable(
            strategy="wide",
            alloc=SimpleNamespace(amount_wmnt=Decimal("83.49"), amount_usdt=Decimal("11.97")),
            bin_count=79,
            params=None,
        )

        assert ok is True  # $6.50 >= $5.00 min_top_up_fill_usdt
        assert estimate["active_mode"] == "y_only"

    def test_allows_top_up_when_fill_estimate_is_unavailable(self):
        bot = _make_bot()
        bot.lp.estimate_position_fill.side_effect = RuntimeError("rpc timeout")

        ok, estimate = bot._top_up_expected_fill_is_viable(
            strategy="wide",
            alloc=SimpleNamespace(amount_wmnt=Decimal("79.03"), amount_usdt=Decimal("14.48")),
            bin_count=21,
            params=None,
        )

        assert ok is True
        assert estimate is None
