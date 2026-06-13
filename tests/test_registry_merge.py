"""Tests for LP registry merge behavior — top-ups aggregate into existing position."""

from __future__ import annotations

import tempfile
from pathlib import Path

from moe_mantle_bot._lp_registry import LPRegistry


def _make_registry() -> LPRegistry:
    tmp = tempfile.mkdtemp()
    reg = LPRegistry("0xtest", data_dir=Path(tmp))
    return reg


class TestRegistryMerge:
    def test_first_add_creates_new_position(self):
        reg = _make_registry()
        pos = reg.add_position(
            "wide", 100, 120, "tx1",
            initial_mnt=500.0, initial_usdt=10.0,
            bin_amounts={100: 1000, 110: 2000, 120: 3000},
        )
        assert pos.id.startswith("wide_")
        assert pos.min_bin == 100
        assert pos.max_bin == 120
        assert pos.initial_mnt == 500.0
        assert len(reg.get_wide_positions()) == 1

    def test_second_add_merges_into_existing(self):
        reg = _make_registry()
        # First position
        reg.add_position(
            "wide", 100, 120, "tx1",
            initial_mnt=500.0, initial_usdt=10.0,
            bin_amounts={str(100): 1000, str(120): 3000},
        )
        # Top-up — should merge, not create new
        pos = reg.add_position(
            "wide", 110, 130, "tx2",
            initial_mnt=200.0, initial_usdt=5.0,
            bin_amounts={str(110): 500, str(130): 1500},
        )
        # Still only 1 active position
        assert len(reg.get_wide_positions()) == 1
        # Merged values
        assert pos.initial_mnt == 700.0  # 500 + 200
        assert pos.initial_usdt == 15.0  # 10 + 5
        # Expanded range
        assert pos.min_bin == 100  # min of 100, 110
        assert pos.max_bin == 130  # max of 120, 130
        assert pos.bin_count == 31  # 100-130 inclusive
        # Merged bin_amounts
        assert pos.bin_amounts[str(100)] == 1000  # original
        assert pos.bin_amounts[str(110)] == 500   # new
        assert pos.bin_amounts[str(120)] == 3000  # original
        assert pos.bin_amounts[str(130)] == 1500  # new

    def test_overlapping_bins_accumulate(self):
        reg = _make_registry()
        reg.add_position(
            "narrow", 100, 110, "tx1",
            initial_mnt=100.0, initial_usdt=2.0,
            bin_amounts={str(105): 5000},
        )
        pos = reg.add_position(
            "narrow", 100, 110, "tx2",
            initial_mnt=50.0, initial_usdt=1.0,
            bin_amounts={str(105): 3000},
        )
        assert len(reg.get_narrow_positions()) == 1
        # Same bin accumulated
        assert pos.bin_amounts[str(105)] == 8000  # 5000 + 3000

    def test_different_strategy_types_stay_separate(self):
        reg = _make_registry()
        reg.add_position("narrow", 100, 110, "tx1", 100.0, 2.0)
        reg.add_position("wide", 90, 130, "tx2", 500.0, 10.0)
        assert len(reg.get_narrow_positions()) == 1
        assert len(reg.get_wide_positions()) == 1

    def test_exited_position_not_merged(self):
        reg = _make_registry()
        pos1 = reg.add_position("wide", 100, 120, "tx1", 500.0, 10.0)
        reg.remove_position(pos1.id, "tx_exit")
        # New add should create fresh, not merge into exited
        pos2 = reg.add_position("wide", 110, 130, "tx2", 200.0, 5.0)
        assert pos2.id != pos1.id
        assert len(reg.get_wide_positions()) == 1
        assert pos2.initial_mnt == 200.0  # not merged
