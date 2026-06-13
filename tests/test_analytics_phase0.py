from __future__ import annotations

from pathlib import Path

from moe_mantle_bot.analytics import Analytics


def test_reentry_event_lifecycle(tmp_path: Path):
    analytics = Analytics(tmp_path / "analytics.db")
    try:
        event_id = analytics.start_reentry_event(
            previous_strategy="wide",
            exit_direction="down",
            exit_active_bin=100,
            exit_min_bin=101,
            exit_max_bin=180,
            exit_mnt_price=2.0,
            recovered_mnt=10.0,
            recovered_usdt=80.0,
            recovered_value_usdt=100.0,
            hodl_value_usdt=102.0,
            fees_vs_hodl_usdt=-2.0,
        )

        analytics.complete_reentry_entry(
            event_id,
            selected_strategy="wide",
            entry_bin_count=60,
            entry_value_usdt=95.0,
            lp_mode="y_only",
            expected_refund_mnt=0.0,
            expected_refund_usdt=5.0,
            fill_pct_mnt=0.0,
            fill_pct_usdt=95.0,
            turnover_usdt=195.0,
        )

        updated = analytics.finalize_pending_reentries(
            current_total_value_usdt=108.0,
            current_mnt_price=2.5,
            current_in_range=True,
        )

        assert updated == 1
        row = analytics.conn.execute(
            "SELECT * FROM reentry_events WHERE id = ?", (event_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "resolved"
        assert row["selected_strategy"] == "wide"
        assert row["lp_mode"] == "y_only"
        assert row["turnover_usdt"] == 195.0
        assert row["followup_in_range"] == 1
        assert row["followup_total_value_usdt"] == 108.0
        assert row["followup_hodl_value_usdt"] == 105.0
        assert row["followup_pnl_vs_hodl_usdt"] == 3.0
        assert row["observed_time_in_range_seconds"] >= 0
    finally:
        analytics.close()


def test_close_reentry_event_marks_exit_only(tmp_path: Path):
    analytics = Analytics(tmp_path / "analytics.db")
    try:
        event_id = analytics.start_reentry_event(
            previous_strategy="wide",
            exit_direction="up",
            exit_active_bin=200,
            exit_min_bin=120,
            exit_max_bin=199,
            exit_mnt_price=3.0,
            recovered_mnt=30.0,
            recovered_usdt=5.0,
            recovered_value_usdt=95.0,
            hodl_value_usdt=92.0,
            fees_vs_hodl_usdt=3.0,
        )
        analytics.close_reentry_event(
            event_id,
            status="exit_only",
            selected_strategy="hold",
            notes="No favorable re-entry",
        )
        row = analytics.conn.execute(
            "SELECT * FROM reentry_events WHERE id = ?", (event_id,)
        ).fetchone()
        assert row is not None
        assert row["status"] == "exit_only"
        assert row["selected_strategy"] == "hold"
        assert row["notes"] == "No favorable re-entry"
        assert row["resolved_ts"] is not None
    finally:
        analytics.close()
