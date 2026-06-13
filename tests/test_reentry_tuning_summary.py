from __future__ import annotations

from pathlib import Path

from moe_mantle_bot.analytics import Analytics


def _resolved_reentry(
    analytics: Analytics,
    *,
    exit_direction: str,
    recovered_mnt: float,
    recovered_usdt: float,
    recovered_value_usdt: float,
    current_total_value_usdt: float,
    current_mnt_price: float,
    lp_mode: str,
    fill_pct_mnt: float | None,
    fill_pct_usdt: float | None,
    entry_value_usdt: float,
    turnover_usdt: float,
    current_in_range: bool,
) -> None:
    event_id = analytics.start_reentry_event(
        previous_strategy="wide",
        exit_direction=exit_direction,
        exit_active_bin=100,
        exit_min_bin=90,
        exit_max_bin=110,
        exit_mnt_price=2.0,
        recovered_mnt=recovered_mnt,
        recovered_usdt=recovered_usdt,
        recovered_value_usdt=recovered_value_usdt,
        hodl_value_usdt=recovered_value_usdt,
        fees_vs_hodl_usdt=0.0,
    )
    analytics.complete_reentry_entry(
        event_id,
        selected_strategy="wide",
        entry_bin_count=20,
        entry_value_usdt=entry_value_usdt,
        lp_mode=lp_mode,
        expected_refund_mnt=0.0,
        expected_refund_usdt=0.0,
        fill_pct_mnt=fill_pct_mnt,
        fill_pct_usdt=fill_pct_usdt,
        turnover_usdt=turnover_usdt,
    )
    analytics.finalize_pending_reentries(
        current_total_value_usdt=current_total_value_usdt,
        current_mnt_price=current_mnt_price,
        current_in_range=current_in_range,
    )


def test_reentry_tuning_summary_relaxes_thresholds_for_supportive_down_samples(tmp_path: Path):
    analytics = Analytics(tmp_path / "analytics.db")
    try:
        _resolved_reentry(
            analytics,
            exit_direction="down",
            recovered_mnt=10.0,
            recovered_usdt=80.0,
            recovered_value_usdt=100.0,
            current_total_value_usdt=107.0,
            current_mnt_price=2.4,
            lp_mode="y_only",
            fill_pct_mnt=0.0,
            fill_pct_usdt=92.0,
            entry_value_usdt=95.0,
            turnover_usdt=195.0,
            current_in_range=True,
        )
        _resolved_reentry(
            analytics,
            exit_direction="down",
            recovered_mnt=8.0,
            recovered_usdt=82.0,
            recovered_value_usdt=98.0,
            current_total_value_usdt=105.0,
            current_mnt_price=2.6,
            lp_mode="y_only",
            fill_pct_mnt=0.0,
            fill_pct_usdt=88.0,
            entry_value_usdt=93.0,
            turnover_usdt=191.0,
            current_in_range=True,
        )

        summary = analytics.get_reentry_tuning_summary(
            exit_direction="down",
            limit=5,
            min_samples=2,
        )

        assert summary["sample_count"] == 2
        assert summary["lp_mode_counts"] == {"y_only": 2}
        assert summary["calibration_signal"] == "supportive_recent_outcomes"
        assert summary["calibration_action"] == "relax"
        assert summary["thresholds"]["recommended"]["min_confidence"] == 0.75
        assert summary["thresholds"]["recommended"]["max_swap_pct"] == 0.4
        assert summary["thresholds"]["recommended"]["min_swap_usdt"] == 7.5
    finally:
        analytics.close()


def test_reentry_tuning_summary_tightens_thresholds_for_weak_up_samples(tmp_path: Path):
    analytics = Analytics(tmp_path / "analytics.db")
    try:
        _resolved_reentry(
            analytics,
            exit_direction="up",
            recovered_mnt=30.0,
            recovered_usdt=5.0,
            recovered_value_usdt=77.0,
            current_total_value_usdt=72.0,
            current_mnt_price=2.2,
            lp_mode="x_only",
            fill_pct_mnt=45.0,
            fill_pct_usdt=0.0,
            entry_value_usdt=70.0,
            turnover_usdt=147.0,
            current_in_range=False,
        )
        _resolved_reentry(
            analytics,
            exit_direction="up",
            recovered_mnt=28.0,
            recovered_usdt=6.0,
            recovered_value_usdt=74.0,
            current_total_value_usdt=70.0,
            current_mnt_price=2.1,
            lp_mode="x_only",
            fill_pct_mnt=50.0,
            fill_pct_usdt=0.0,
            entry_value_usdt=69.0,
            turnover_usdt=143.0,
            current_in_range=False,
        )

        summary = analytics.get_reentry_tuning_summary(
            exit_direction="up",
            limit=5,
            min_samples=2,
        )

        assert summary["sample_count"] == 2
        assert summary["lp_mode_counts"] == {"x_only": 2}
        assert summary["calibration_signal"] == "weak_recent_outcomes"
        assert summary["calibration_action"] == "tighten"
        assert summary["thresholds"]["recommended"]["min_confidence"] == 0.85
        assert summary["thresholds"]["recommended"]["max_swap_pct"] == 0.3
        assert summary["thresholds"]["recommended"]["min_swap_usdt"] == 12.5
    finally:
        analytics.close()
