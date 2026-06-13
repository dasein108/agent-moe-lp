"""Tests for StrategyEngine — pure logic, no mocks needed."""

from moe_mantle_bot.strategies.engine import (
    MarketState,
    PositionSnapshot,
    StrategyEngine,
    WalletComposition,
)


def _market(
    regime="RANGING",
    keltner_conf=0.6,
    keltner_ranging=True,
    daily_atr=3.0,
    overbought=False,
    oversold=False,
    rsi_1h=50.0,
    rsi_4h=50.0,
    higher_tf_bias="NEUTRAL",
    keltner_width=2.5,
) -> MarketState:
    return MarketState(
        keltner_confidence=keltner_conf,
        keltner_is_ranging=keltner_ranging,
        keltner_width_pct=keltner_width,
        regime=regime,
        regime_confidence=0.7,
        higher_tf_bias=higher_tf_bias,
        overbought=overbought,
        oversold=oversold,
        daily_atr_pct=daily_atr,
        rsi_1h=rsi_1h,
        rsi_4h=rsi_4h,
    )


def _position(exists=False, in_range=True, bin_count=0, active=8326000, min_bin=None, max_bin=None):
    return PositionSnapshot(
        exists=exists,
        in_range=in_range,
        bin_count=bin_count,
        min_bin_id=min_bin,
        max_bin_id=max_bin,
        active_bin_id=active,
    )


def _wallet(mnt_weight=0.5, free_value=50.0, total_value=100.0):
    return WalletComposition(mnt_weight=mnt_weight, free_value_usdt=free_value, total_value_usdt=total_value)


class TestEntrySelection:
    """Test _select_entry — no position exists, pick narrow/wide/hold."""

    def setup_method(self):
        self.engine = StrategyEngine(wide_confidence_threshold=0.5)

    def test_ranging_high_conf_selects_wide(self):
        d = self.engine._select_entry(_market(regime="RANGING", keltner_conf=0.6), _wallet())
        assert d.action == "wide"

    def test_ranging_low_conf_selects_narrow(self):
        d = self.engine._select_entry(_market(regime="RANGING", keltner_conf=0.2), _wallet())
        assert d.action == "narrow"

    def test_trending_up_overbought_holds(self):
        # Gate uses 1h RSI (not 4h) — 1h must be >90 to block entry
        # (threshold raised 70→90 in ba0302d: bot sat idle 35h at RSI 78)
        d = self.engine._select_entry(
            _market(regime="TRENDING_UP", overbought=True, rsi_1h=92, rsi_4h=92),
            _wallet(),
        )
        assert d.action == "hold"
        assert "overbought" in d.reason

    def test_trending_down_oversold_holds(self):
        # Gate uses 1h RSI (not 4h) — 1h must be <20 to block entry
        # ATR must be < 6% otherwise wide gate takes priority
        d = self.engine._select_entry(
            _market(regime="TRENDING_DOWN", oversold=True, rsi_1h=15, rsi_4h=25, daily_atr=4.0),
            _wallet(),
        )
        assert d.action == "hold"
        assert "oversold" in d.reason

    def test_trending_up_not_overbought_selects_narrow(self):
        d = self.engine._select_entry(
            _market(regime="TRENDING_UP", overbought=False, daily_atr=4.0),
            _wallet(),
        )
        assert d.action == "narrow"

    def test_high_atr_selects_wide(self):
        d = self.engine._select_entry(_market(daily_atr=9.0), _wallet())
        assert d.action == "wide"
        assert "volatility" in d.reason

    def test_extreme_atr_selects_wide(self):
        d = self.engine._select_entry(_market(regime="VOLATILE", daily_atr=15.0), _wallet())
        assert d.action == "wide"

    def test_unknown_regime_defaults_narrow(self):
        d = self.engine._select_entry(_market(regime="UNKNOWN", keltner_conf=0.0), _wallet())
        assert d.action == "narrow"

    def test_ranging_boost_crosses_threshold(self):
        # keltner_conf=0.4 + ranging boost 0.15 = 0.55 > 0.5
        d = self.engine._select_entry(_market(regime="RANGING", keltner_conf=0.4), _wallet())
        assert d.action == "wide"

    def test_ranging_boost_not_enough(self):
        # keltner_conf=0.3 + ranging boost 0.15 = 0.45 < 0.5
        d = self.engine._select_entry(_market(regime="RANGING", keltner_conf=0.3), _wallet())
        assert d.action == "narrow"


class TestSelectStrategy:
    """Test the full select_strategy with positions."""

    def setup_method(self):
        self.engine = StrategyEngine(wide_confidence_threshold=0.5, min_top_up_free_value_usdt=20.0)

    def test_no_position_delegates_to_entry(self):
        d = self.engine.select_strategy(
            _market(), _position(exists=False), _wallet(),
        )
        assert d.action in ("narrow", "wide", "hold")

    def test_in_range_holds(self):
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=20, active=100, min_bin=90, max_bin=110),
            _wallet(free_value=5.0),  # below top-up threshold
        )
        assert d.action == "hold"

    def test_out_of_range_exits(self):
        # Active bin 8326500 is 400 bins outside range [100-200] → extreme drift → force exit
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=False, bin_count=100,
                      active=8326500, min_bin=100, max_bin=200),
            _wallet(),
        )
        assert d.action == "exit_and_reenter"

    def test_at_edge_holds(self):
        # Edge exit disabled (EDGE_MARGIN_BINS=0) — hold until truly OOR
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=20, active=101, min_bin=100, max_bin=120),
            _wallet(free_value=5.0),
        )
        assert d.action == "hold"

    def test_range_too_wide_exits(self):
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=100, active=150, min_bin=100, max_bin=200),
            _wallet(free_value=5.0),
            optimal_bin_count=30,
        )
        assert d.action == "exit_and_reenter"
        assert "too_wide" in d.reason

    def test_range_too_narrow_exits(self):
        # 10 bins, optimal=50 → ratio=0.2 < 0.25 (too narrow).
        # Active bin 109 near edge of [100,110] → headroom=1/10=10% < 15% threshold.
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=10, active=109, min_bin=100, max_bin=110),
            _wallet(free_value=5.0),
            optimal_bin_count=50,
        )
        assert d.action == "exit_and_reenter"
        assert "too_narrow" in d.reason

    def test_no_top_up_disabled(self):
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=20, active=110, min_bin=100, max_bin=120),
            _wallet(free_value=25.0),
            existing_position_strategy="wide",
        )
        assert d.action == "hold"

    def test_no_top_up_when_low_free_capital(self):
        d = self.engine.select_strategy(
            _market(),
            _position(exists=True, in_range=True, bin_count=20, active=110, min_bin=100, max_bin=120),
            _wallet(free_value=15.0),  # below 20.0 threshold
        )
        assert d.action == "hold"


class TestDecisionIsPureData:
    """Verify StrategyDecision is plain data, no side effects."""

    def test_decision_has_action_and_reason(self):
        engine = StrategyEngine()
        d = engine._select_entry(_market(), _wallet())
        assert isinstance(d.action, str)
        assert isinstance(d.reason, str)
        assert isinstance(d.confidence, float)
        assert isinstance(d.details, dict)

    def test_market_state_is_frozen(self):
        m = _market()
        try:
            m.regime = "HACKED"  # type: ignore
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_engine_has_no_rpc_dependencies(self):
        """StrategyEngine constructor takes no RPC/blockchain arguments."""
        engine = StrategyEngine(wide_confidence_threshold=0.5)
        # Can be instantiated without any blockchain service
        assert engine is not None
