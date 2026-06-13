from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

from moe_mantle_bot.orchestration import SinglePositionCyclePreparer

def _make_preparer():
    from moe_mantle_bot.farm_bot import FarmBot

    settings = MagicMock()
    settings.min_position_size_usdt = 10.0
    return SinglePositionCyclePreparer(
        settings=settings,
        lp=MagicMock(),
        balance=MagicMock(),
        analytics=MagicMock(),
        keltner_analyzer=MagicMock(),
        bias_calculator=MagicMock(),
        strategy_override=None,
        safe_float=FarmBot._safe_float,
        calculate_rsi=FarmBot._calculate_rsi,
    )


def test_normalize_position_for_strategy_ignores_dust_lp():
    bot = _make_preparer()

    position = SimpleNamespace(
        position_exists=True,
        in_range=True,
        bin_count=23,
        min_bin_id=100,
        max_bin_id=122,
    )
    budget = SimpleNamespace(deployed_value_usdt=Decimal("0"), free_value_usdt=Decimal("25"))

    normalized = bot.normalize_position_for_strategy(position, budget)

    assert normalized.position_exists is False
    assert normalized.in_range is False
    assert normalized.bin_count == 0


def test_normalize_position_for_strategy_ignores_small_lp_when_free_capital_can_redeploy():
    bot = _make_preparer()

    position = SimpleNamespace(
        position_exists=True,
        in_range=True,
        bin_count=23,
        min_bin_id=100,
        max_bin_id=122,
    )
    budget = SimpleNamespace(deployed_value_usdt=Decimal("5"), free_value_usdt=Decimal("25"))

    normalized = bot.normalize_position_for_strategy(position, budget)

    assert normalized.position_exists is False
    assert normalized.in_range is False
    assert normalized.bin_count == 0


def test_normalize_position_for_strategy_keeps_small_lp_when_free_capital_too_low():
    bot = _make_preparer()

    position = SimpleNamespace(
        position_exists=True,
        in_range=True,
        bin_count=23,
        min_bin_id=100,
        max_bin_id=122,
    )
    budget = SimpleNamespace(deployed_value_usdt=Decimal("5"), free_value_usdt=Decimal("4"))

    normalized = bot.normalize_position_for_strategy(position, budget)

    assert normalized is position


def test_normalize_position_for_strategy_keeps_real_lp():
    bot = _make_preparer()

    position = SimpleNamespace(
        position_exists=True,
        in_range=True,
        bin_count=23,
        min_bin_id=100,
        max_bin_id=122,
    )
    budget = SimpleNamespace(deployed_value_usdt=Decimal("12"), free_value_usdt=Decimal("25"))

    normalized = bot.normalize_position_for_strategy(position, budget)

    assert normalized is position
