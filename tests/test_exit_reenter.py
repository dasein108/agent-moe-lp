"""Tests for Phase 5: Exit-and-reenter flow."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock
import pytest


def _position(exists=False, in_range=True, min_bin=100, max_bin=110):
    pos = MagicMock()
    pos.position_exists = exists
    pos.in_range = in_range
    pos.min_bin_id = min_bin
    pos.max_bin_id = max_bin
    pos.bin_count = max_bin - min_bin if exists else 0
    pos.inventory_included = False
    return pos


def _pool(active_bin_id=105):
    ps = MagicMock()
    ps.active_bin_id = active_bin_id
    return ps


class TestShouldExitEarly:
    def _should_exit(self, position, pool_state):
        from moe_mantle_bot.farm_bot import EnhancedFarmBotV3
        return EnhancedFarmBotV3._should_exit_early(position, pool_state)

    def test_no_position(self):
        assert self._should_exit(_position(exists=False), _pool()) is False

    def test_already_out_of_range(self):
        assert self._should_exit(_position(exists=True, in_range=False), _pool()) is True

    def test_center_of_range_no_exit(self):
        # Active bin 105, range 100-110 → comfortably in range
        assert self._should_exit(
            _position(exists=True, in_range=True, min_bin=100, max_bin=110),
            _pool(active_bin_id=105),
        ) is False

    def test_at_lower_edge_no_exit(self):
        # Edge exit disabled — hold until truly OOR
        assert self._should_exit(
            _position(exists=True, in_range=True, min_bin=100, max_bin=110),
            _pool(active_bin_id=101),
        ) is False

    def test_at_upper_edge_no_exit(self):
        # Edge exit disabled — hold until truly OOR
        assert self._should_exit(
            _position(exists=True, in_range=True, min_bin=100, max_bin=110),
            _pool(active_bin_id=109),
        ) is False

    def test_one_inside_lower_no_exit(self):
        assert self._should_exit(
            _position(exists=True, in_range=True, min_bin=100, max_bin=110),
            _pool(active_bin_id=102),
        ) is False

    def test_one_inside_upper_no_exit(self):
        assert self._should_exit(
            _position(exists=True, in_range=True, min_bin=100, max_bin=110),
            _pool(active_bin_id=108),
        ) is False


class TestSelectStrategyWithPoolState:
    def _select(self, position, pool_state=None):
        from moe_mantle_bot.farm_bot import FarmBot
        from moe_mantle_bot.strategies.engine import StrategyEngine
        from moe_mantle_bot.quant.mtf_analyzer import MTFAnalysis, TimeframeSignal
        from moe_mantle_bot.models import CapitalBudget
        bot = MagicMock(spec=FarmBot)
        bot.strategy_override = None
        bot.settings = MagicMock()
        bot.settings.min_position_size_usdt = 10.0
        bot.strategy_engine = StrategyEngine(wide_confidence_threshold=0.5)
        bot.mtf_analyzer = MagicMock()
        bot.mtf_analyzer.analyze.return_value = MTFAnalysis(
            tf_5m=TimeframeSignal("5m", 0.025, 50.0, None, None, None, None, "NEUTRAL", 0.5, 0.4),
            tf_1h=TimeframeSignal("1h", 0.025, 50.0, None, None, None, None, "NEUTRAL", 1.5, 0.8),
            tf_4h=TimeframeSignal("4h", 0.025, 50.0, None, None, None, None, "NEUTRAL", 2.5, 1.5),
            regime="RANGING", regime_confidence=0.7,
            higher_tf_bias="NEUTRAL", overbought=False, oversold=False, daily_atr_pct=3.0,
        )
        bot.balance = MagicMock()
        bot.balance.get_capital_budget.return_value = CapitalBudget(
            total_mnt=Decimal(100), total_usdt=Decimal(10),
            deployed_mnt=Decimal(0), deployed_usdt=Decimal(0),
            free_mnt=Decimal(50), free_usdt=Decimal(5),
            gas_reserve_mnt=Decimal(30), mnt_price_usdt=Decimal("0.025"),
        )
        bot.wallet = MagicMock()
        bot.wallet.address = "0xTEST"
        bot.wide_range_manager = MagicMock()
        bot.wide_range_manager.calculate_wide_range_params.return_value = {"bin_count": 50}
        reg = MagicMock()
        reg.get_wide_positions.return_value = []
        reg.get_narrow_positions.return_value = []
        bot.lp = MagicMock()
        bot.lp.get_registry.return_value = reg
        bot.select_strategy = FarmBot.select_strategy.__get__(bot)
        bot._build_market_state = FarmBot._build_market_state.__get__(bot)
        bot._build_wallet_composition = FarmBot._build_wallet_composition.__get__(bot)
        bot._build_position_snapshot = FarmBot._build_position_snapshot.__get__(bot)
        bot._should_exit_early = FarmBot._should_exit_early
        return bot.select_strategy(position, pool_state=pool_state)

    def test_hold_center_of_range(self):
        pos = _position(exists=True, in_range=True, min_bin=100, max_bin=110)
        ps = _pool(active_bin_id=105)
        assert self._select(pos, pool_state=ps) == "hold"

    def test_hold_at_edge(self):
        # Edge exit disabled — hold until truly OOR to reduce churn
        pos = _position(exists=True, in_range=True, min_bin=100, max_bin=110)
        ps = _pool(active_bin_id=101)  # at edge, but still in range
        assert self._select(pos, pool_state=ps) == "hold"

    def test_exit_out_of_range(self):
        # Active bin 250, well outside range [100-110] → drift=140 > OOR_FORCE_EXIT_BINS
        pos = _position(exists=True, in_range=False, min_bin=100, max_bin=110)
        ps = _pool(active_bin_id=250)
        assert self._select(pos, pool_state=ps) == "exit_and_reenter"
