"""
Analytics module — SQLite-based performance tracking for LP farming.

Records snapshots of balances, positions, and operations.
Computes APR, ROI, fees, gas costs, and HODL comparison.
Handles external LP operations (manual add/remove outside the bot).
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .logging_config import get_logger

logger = get_logger(__name__)

DB_VERSION = 1


class Analytics:
    """SQLite-backed analytics tracker for LP farming performance."""

    def __init__(self, db_path: Path | str = "data/analytics.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        c = self.conn.cursor()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,                     -- unix timestamp
                mnt_price_usdt REAL,
                wallet_mnt REAL,                      -- native + WMNT
                wallet_usdt REAL,
                deployed_mnt REAL,                    -- in LP
                deployed_usdt REAL,
                total_value_usdt REAL,                -- wallet + deployed in USD
                free_value_usdt REAL,
                active_bin_id INTEGER,
                narrow_bins INTEGER,                  -- 0 if no narrow position
                narrow_min_bin INTEGER,
                narrow_max_bin INTEGER,
                wide_bins INTEGER,                    -- 0 if no wide position
                wide_min_bin INTEGER,
                wide_max_bin INTEGER,
                pending_fees_mnt REAL,                -- unclaimed fees (token X)
                pending_fees_usdt REAL,               -- unclaimed fees (token Y)
                pending_rewards_mnt REAL              -- claimable MNT rewards
            );

            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                action TEXT NOT NULL,                  -- 'add', 'remove', 'swap', 'rebalance', 'external_add', 'external_remove'
                strategy TEXT,                        -- 'narrow', 'wide', or null
                bin_count INTEGER,
                amount_mnt REAL,
                amount_usdt REAL,
                value_usdt REAL,                      -- USD value at time of operation
                gas_mnt REAL,
                tx_hash TEXT,
                recovered_mnt REAL,                   -- for removals
                recovered_usdt REAL,
                hodl_value_usdt REAL,                 -- HODL comparison at removal
                fees_vs_hodl_usdt REAL,               -- LP value - HODL value
                details TEXT                          -- JSON extra
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,                 -- YYYY-MM-DD
                start_value_usdt REAL,
                end_value_usdt REAL,
                high_value_usdt REAL,
                low_value_usdt REAL,
                gas_spent_mnt REAL DEFAULT 0,
                operations_count INTEGER DEFAULT 0,
                reverts_count INTEGER DEFAULT 0,
                fees_earned_usdt REAL DEFAULT 0,
                mnt_price_start REAL,
                mnt_price_end REAL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_operations_ts ON operations(ts);

            CREATE TABLE IF NOT EXISTS reentry_events (
                id TEXT PRIMARY KEY,
                exit_ts REAL NOT NULL,
                entry_ts REAL,
                resolved_ts REAL,
                previous_strategy TEXT,
                selected_strategy TEXT,
                status TEXT NOT NULL,                -- exiting, entered, exit_only, error, resolved
                exit_direction TEXT,                -- down, up, unknown
                exit_active_bin INTEGER,
                exit_min_bin INTEGER,
                exit_max_bin INTEGER,
                exit_mnt_price REAL,
                recovered_mnt REAL DEFAULT 0,
                recovered_usdt REAL DEFAULT 0,
                recovered_value_usdt REAL DEFAULT 0,
                hodl_value_usdt REAL DEFAULT 0,
                fees_vs_hodl_usdt REAL DEFAULT 0,
                entry_bin_count INTEGER,
                entry_value_usdt REAL DEFAULT 0,
                lp_mode TEXT,
                expected_refund_mnt REAL DEFAULT 0,
                expected_refund_usdt REAL DEFAULT 0,
                fill_pct_mnt REAL,
                fill_pct_usdt REAL,
                turnover_usdt REAL DEFAULT 0,
                followup_elapsed_seconds REAL,
                observed_time_in_range_seconds REAL,
                followup_in_range INTEGER,
                followup_total_value_usdt REAL,
                followup_hodl_value_usdt REAL,
                followup_pnl_vs_hodl_usdt REAL,
                notes TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_reentry_events_exit_ts ON reentry_events(exit_ts);
            CREATE INDEX IF NOT EXISTS idx_reentry_events_status ON reentry_events(status);
        """)
        self._migrate_schema(c)
        self.conn.commit()

    def _migrate_schema(self, c: sqlite3.Cursor) -> None:
        """Add new columns to existing DBs. CREATE TABLE IF NOT EXISTS skips existing tables."""
        existing = {row["name"] for row in c.execute("PRAGMA table_info(snapshots)").fetchall()}
        for col, sql_type in (
            ("pending_fees_mnt", "REAL"),
            ("pending_fees_usdt", "REAL"),
            ("pending_rewards_mnt", "REAL"),
        ):
            if col not in existing:
                c.execute(f"ALTER TABLE snapshots ADD COLUMN {col} {sql_type}")

    def repair_external_flow_values(self) -> int:
        """Recompute value_usdt on external_add/external_remove rows that were written
        with the old MNT/USDT mixed-unit bug. Uses nearest-snapshot MNT price at the
        operation timestamp. Returns number of rows updated.
        """
        rows = self.conn.execute(
            "SELECT id, ts, amount_mnt, amount_usdt, value_usdt "
            "FROM operations WHERE action IN ('external_add', 'external_remove')"
        ).fetchall()
        updated = 0
        for row in rows:
            price_row = self.conn.execute(
                "SELECT mnt_price_usdt FROM snapshots ORDER BY ABS(ts - ?) LIMIT 1",
                (row["ts"],),
            ).fetchone()
            if price_row is None or price_row["mnt_price_usdt"] is None:
                continue
            price = float(price_row["mnt_price_usdt"])
            correct = (row["amount_mnt"] or 0) * price + (row["amount_usdt"] or 0)
            if abs(correct - (row["value_usdt"] or 0)) < 0.01:
                continue
            self.conn.execute(
                "UPDATE operations SET value_usdt = ? WHERE id = ?",
                (correct, row["id"]),
            )
            updated += 1
        if updated:
            self.conn.commit()
        return updated

    def close(self) -> None:
        self.conn.close()

    # ── Snapshots ──────────────────────────────────────────

    def record_snapshot(
        self,
        *,
        mnt_price: float,
        wallet_mnt: float,
        wallet_usdt: float,
        deployed_mnt: float,
        deployed_usdt: float,
        total_value_usdt: float,
        free_value_usdt: float,
        active_bin_id: int,
        narrow_bins: int = 0,
        narrow_min_bin: int | None = None,
        narrow_max_bin: int | None = None,
        wide_bins: int = 0,
        wide_min_bin: int | None = None,
        wide_max_bin: int | None = None,
        pending_fees_mnt: float | None = None,
        pending_fees_usdt: float | None = None,
        pending_rewards_mnt: float | None = None,
    ) -> None:
        self.conn.execute("""
            INSERT INTO snapshots (ts, mnt_price_usdt, wallet_mnt, wallet_usdt,
                deployed_mnt, deployed_usdt, total_value_usdt, free_value_usdt,
                active_bin_id, narrow_bins, narrow_min_bin, narrow_max_bin,
                wide_bins, wide_min_bin, wide_max_bin,
                pending_fees_mnt, pending_fees_usdt, pending_rewards_mnt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time.time(), mnt_price, wallet_mnt, wallet_usdt,
            deployed_mnt, deployed_usdt, total_value_usdt, free_value_usdt,
            active_bin_id, narrow_bins, narrow_min_bin, narrow_max_bin,
            wide_bins, wide_min_bin, wide_max_bin,
            pending_fees_mnt, pending_fees_usdt, pending_rewards_mnt,
        ))
        self.conn.commit()
        # Compute fee delta from previous snapshot and update daily stats
        fee_delta_usdt = self._compute_fee_delta(pending_fees_mnt, pending_fees_usdt, mnt_price)
        self._update_daily_stats(total_value_usdt, mnt_price, fee_delta_usdt)

    def _compute_fee_delta(
        self,
        pending_fees_mnt: float | None,
        pending_fees_usdt: float | None,
        mnt_price: float,
    ) -> float:
        """Compute fee income since last snapshot. Returns USD value of new fees."""
        if pending_fees_mnt is None and pending_fees_usdt is None:
            return 0.0

        prev = self.conn.execute("""
            SELECT pending_fees_mnt, pending_fees_usdt
            FROM snapshots WHERE pending_fees_mnt IS NOT NULL
            ORDER BY ts DESC LIMIT 1 OFFSET 1
        """).fetchone()

        if prev is None:
            return 0.0  # first snapshot with fees, no delta

        prev_mnt = prev["pending_fees_mnt"] or 0
        prev_usdt = prev["pending_fees_usdt"] or 0
        curr_mnt = pending_fees_mnt or 0
        curr_usdt = pending_fees_usdt or 0

        delta_mnt = curr_mnt - prev_mnt
        delta_usdt = curr_usdt - prev_usdt

        # Negative delta = position was removed and fees collected. Count current as new accrual.
        if delta_mnt < 0:
            delta_mnt = curr_mnt
        if delta_usdt < 0:
            delta_usdt = curr_usdt

        return delta_mnt * mnt_price + delta_usdt

    def _update_daily_stats(self, value_usdt: float, mnt_price: float, fee_delta_usdt: float = 0) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self.conn.execute("SELECT * FROM daily_stats WHERE date = ?", (today,)).fetchone()
        if row is None:
            self.conn.execute("""
                INSERT INTO daily_stats (date, start_value_usdt, end_value_usdt,
                    high_value_usdt, low_value_usdt, mnt_price_start, mnt_price_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today, value_usdt, value_usdt, value_usdt, value_usdt, mnt_price, mnt_price))
        else:
            self.conn.execute("""
                UPDATE daily_stats SET
                    end_value_usdt = ?,
                    high_value_usdt = MAX(high_value_usdt, ?),
                    low_value_usdt = MIN(low_value_usdt, ?),
                    mnt_price_end = ?,
                    fees_earned_usdt = fees_earned_usdt + ?
                WHERE date = ?
            """, (value_usdt, value_usdt, value_usdt, mnt_price, max(0, fee_delta_usdt), today))
        self.conn.commit()

    # ── Operations ─────────────────────────────────────────

    def record_operation(
        self,
        *,
        action: str,
        strategy: str | None = None,
        bin_count: int | None = None,
        amount_mnt: float = 0,
        amount_usdt: float = 0,
        value_usdt: float = 0,
        gas_mnt: float = 0,
        tx_hash: str | None = None,
        recovered_mnt: float = 0,
        recovered_usdt: float = 0,
        hodl_value_usdt: float = 0,
        fees_vs_hodl_usdt: float = 0,
        details: str | None = None,
    ) -> None:
        self.conn.execute("""
            INSERT INTO operations (ts, action, strategy, bin_count,
                amount_mnt, amount_usdt, value_usdt, gas_mnt, tx_hash,
                recovered_mnt, recovered_usdt, hodl_value_usdt, fees_vs_hodl_usdt, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time.time(), action, strategy, bin_count,
            amount_mnt, amount_usdt, value_usdt, gas_mnt, tx_hash,
            recovered_mnt, recovered_usdt, hodl_value_usdt, fees_vs_hodl_usdt, details,
        ))
        # Update daily gas/ops count
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if action == "revert":
            self.conn.execute("""
                UPDATE daily_stats SET reverts_count = reverts_count + 1,
                    gas_spent_mnt = gas_spent_mnt + ? WHERE date = ?
            """, (gas_mnt, today))
        else:
            self.conn.execute("""
                UPDATE daily_stats SET operations_count = operations_count + 1,
                    gas_spent_mnt = gas_spent_mnt + ?,
                    fees_earned_usdt = fees_earned_usdt + MAX(0, ?)
                WHERE date = ?
            """, (gas_mnt, fees_vs_hodl_usdt, today))
        self.conn.commit()

    def detect_external_changes(
        self,
        *,
        prev_snapshot: dict | None,
        current_deployed_mnt: float,
        current_deployed_usdt: float,
        current_bins: int,
        mnt_price: float,
    ) -> None:
        """Detect if LP was added/removed externally (not by the bot).

        Compares current deployed amounts with previous snapshot.
        A significant change without a matching operation = external action.
        """
        if prev_snapshot is None:
            return
        # Value previous deployed LP at the price captured with that snapshot
        # (fall back to current price if the historical snapshot predates the column).
        prev_mnt_price = prev_snapshot.get("mnt_price_usdt") or mnt_price or 0
        prev_deployed = (
            (prev_snapshot.get("deployed_mnt", 0) or 0) * prev_mnt_price
            + (prev_snapshot.get("deployed_usdt", 0) or 0)
        )
        curr_deployed = current_deployed_mnt * mnt_price + current_deployed_usdt
        prev_bins = max(prev_snapshot.get("narrow_bins", 0), prev_snapshot.get("wide_bins", 0))

        # Check for recent bot operations (within last 5 minutes)
        recent = self.conn.execute(
            "SELECT COUNT(*) FROM operations WHERE ts > ? AND action NOT IN ('external_add', 'external_remove')",
            (time.time() - 300,)
        ).fetchone()[0]
        if recent > 0:
            return  # Bot recently operated — changes are expected

        delta_pct = abs(curr_deployed - prev_deployed) / max(prev_deployed, 0.01) * 100
        if delta_pct > 10 and abs(current_bins - prev_bins) > 2:
            action = "external_add" if curr_deployed > prev_deployed else "external_remove"
            logger.info(f"Detected external LP change: {action} "
                        f"(deployed ${prev_deployed:.2f} -> ${curr_deployed:.2f}, bins {prev_bins} -> {current_bins})")
            self.record_operation(
                action=action,
                amount_mnt=abs(current_deployed_mnt - prev_snapshot.get("deployed_mnt", 0)),
                amount_usdt=abs(current_deployed_usdt - prev_snapshot.get("deployed_usdt", 0)),
                value_usdt=abs(curr_deployed - prev_deployed),
            )

    def get_latest_snapshot(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # ── Re-entry Tracking ─────────────────────────────────

    def start_reentry_event(
        self,
        *,
        previous_strategy: str | None,
        exit_direction: str,
        exit_active_bin: int | None,
        exit_min_bin: int | None,
        exit_max_bin: int | None,
        exit_mnt_price: float,
        recovered_mnt: float,
        recovered_usdt: float,
        recovered_value_usdt: float,
        hodl_value_usdt: float,
        fees_vs_hodl_usdt: float,
        notes: str | None = None,
    ) -> str:
        event_id = uuid.uuid4().hex
        self.conn.execute("""
            INSERT INTO reentry_events (
                id, exit_ts, previous_strategy, status, exit_direction,
                exit_active_bin, exit_min_bin, exit_max_bin, exit_mnt_price,
                recovered_mnt, recovered_usdt, recovered_value_usdt,
                hodl_value_usdt, fees_vs_hodl_usdt, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_id, time.time(), previous_strategy, "exiting", exit_direction,
            exit_active_bin, exit_min_bin, exit_max_bin, exit_mnt_price,
            recovered_mnt, recovered_usdt, recovered_value_usdt,
            hodl_value_usdt, fees_vs_hodl_usdt, notes,
        ))
        self.conn.commit()
        return event_id

    def complete_reentry_entry(
        self,
        event_id: str,
        *,
        selected_strategy: str,
        entry_bin_count: int,
        entry_value_usdt: float,
        lp_mode: str | None,
        expected_refund_mnt: float,
        expected_refund_usdt: float,
        fill_pct_mnt: float | None,
        fill_pct_usdt: float | None,
        turnover_usdt: float,
        notes: str | None = None,
    ) -> None:
        self.conn.execute("""
            UPDATE reentry_events
            SET entry_ts = ?, selected_strategy = ?, status = ?,
                entry_bin_count = ?, entry_value_usdt = ?, lp_mode = ?,
                expected_refund_mnt = ?, expected_refund_usdt = ?,
                fill_pct_mnt = ?, fill_pct_usdt = ?, turnover_usdt = ?,
                notes = COALESCE(?, notes)
            WHERE id = ?
        """, (
            time.time(), selected_strategy, "entered",
            entry_bin_count, entry_value_usdt, lp_mode,
            expected_refund_mnt, expected_refund_usdt,
            fill_pct_mnt, fill_pct_usdt, turnover_usdt,
            notes, event_id,
        ))
        self.conn.commit()

    def close_reentry_event(
        self,
        event_id: str,
        *,
        status: str,
        selected_strategy: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.conn.execute("""
            UPDATE reentry_events
            SET resolved_ts = ?, status = ?,
                selected_strategy = COALESCE(?, selected_strategy),
                notes = COALESCE(?, notes)
            WHERE id = ?
        """, (time.time(), status, selected_strategy, notes, event_id))
        self.conn.commit()

    def finalize_pending_reentries(
        self,
        *,
        current_total_value_usdt: float,
        current_mnt_price: float,
        current_in_range: bool,
    ) -> int:
        rows = self.conn.execute("""
            SELECT * FROM reentry_events
            WHERE status = 'entered' AND followup_elapsed_seconds IS NULL
            ORDER BY exit_ts ASC
        """).fetchall()

        now = time.time()
        updated = 0
        for row in rows:
            if row["entry_ts"] is None:
                continue
            elapsed = max(0.0, now - row["entry_ts"])
            hodl_value = (row["recovered_mnt"] or 0) * current_mnt_price + (row["recovered_usdt"] or 0)
            observed_time = elapsed if current_in_range else 0.0
            self.conn.execute("""
                UPDATE reentry_events
                SET resolved_ts = ?, status = ?,
                    followup_elapsed_seconds = ?,
                    observed_time_in_range_seconds = ?,
                    followup_in_range = ?,
                    followup_total_value_usdt = ?,
                    followup_hodl_value_usdt = ?,
                    followup_pnl_vs_hodl_usdt = ?
                WHERE id = ?
            """, (
                now, "resolved",
                elapsed,
                observed_time,
                1 if current_in_range else 0,
                current_total_value_usdt,
                hodl_value,
                current_total_value_usdt - hodl_value,
                row["id"],
            ))
            updated += 1

        if updated:
            self.conn.commit()
        return updated

    # ── Queries ────────────────────────────────────────────

    def get_roi(self, days: int = 0) -> dict[str, Any]:
        """Compute ROI over a period. days=0 means all time."""
        if days > 0:
            since = time.time() - days * 86400
            first = self.conn.execute(
                "SELECT * FROM snapshots WHERE ts >= ? ORDER BY ts ASC LIMIT 1", (since,)
            ).fetchone()
        else:
            first = self.conn.execute("SELECT * FROM snapshots ORDER BY ts ASC LIMIT 1").fetchone()
        last = self.conn.execute("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1").fetchone()

        if not first or not last:
            return {"error": "insufficient data"}

        start_val = first["total_value_usdt"]
        end_val = last["total_value_usdt"]
        elapsed_hours = (last["ts"] - first["ts"]) / 3600
        elapsed_days = elapsed_hours / 24

        # Gas spent in period
        gas_query = "SELECT COALESCE(SUM(gas_mnt), 0) FROM operations"
        fees_query = "SELECT COALESCE(SUM(CASE WHEN fees_vs_hodl_usdt > 0 THEN fees_vs_hodl_usdt ELSE 0 END), 0) FROM operations"
        reverts_query = "SELECT COUNT(*) FROM operations WHERE action = 'revert'"
        ops_query = "SELECT COUNT(*) FROM operations WHERE action != 'revert'"
        # Net external flows (deposits minus withdrawals) over the period
        flows_query = (
            "SELECT "
            "  COALESCE(SUM(CASE WHEN action='external_add' THEN value_usdt ELSE 0 END), 0) AS deposits, "
            "  COALESCE(SUM(CASE WHEN action='external_remove' THEN value_usdt ELSE 0 END), 0) AS withdrawals "
            "FROM operations"
        )

        if days > 0:
            gas_query += f" WHERE ts >= {since}"
            fees_query += f" WHERE ts >= {since}"
            reverts_query += f" AND ts >= {since}"
            ops_query += f" AND ts >= {since}"
            flows_query += f" WHERE ts >= {since}"
        # Exclude flows that occurred before the window's first snapshot
        # so start_val already reflects their effect.
        flows_query += (" AND" if days > 0 else " WHERE") + f" ts >= {first['ts']}"

        gas_spent = self.conn.execute(gas_query).fetchone()[0]
        fees_earned = self.conn.execute(fees_query).fetchone()[0]
        reverts = self.conn.execute(reverts_query).fetchone()[0]
        ops = self.conn.execute(ops_query).fetchone()[0]
        flows_row = self.conn.execute(flows_query).fetchone()
        deposits = flows_row["deposits"] if flows_row else 0.0
        withdrawals = flows_row["withdrawals"] if flows_row else 0.0
        net_flow = deposits - withdrawals

        pnl = end_val - start_val
        strategy_pnl = pnl - net_flow  # P&L excluding external capital moves
        roi_pct = (pnl / start_val * 100) if start_val > 0 else 0
        # Flow-adjusted ROI uses avg capital base (start + net_flow/2) to avoid bias
        adj_base = start_val + (net_flow / 2.0)
        strategy_roi_pct = (strategy_pnl / adj_base * 100) if adj_base > 0 else 0
        apr_pct = (roi_pct / elapsed_days * 365) if elapsed_days > 0 else 0
        strategy_apr_pct = (strategy_roi_pct / elapsed_days * 365) if elapsed_days > 0 else 0
        daily_pct = roi_pct / elapsed_days if elapsed_days > 0 else 0
        strategy_daily_pct = strategy_roi_pct / elapsed_days if elapsed_days > 0 else 0

        # HODL comparison: if we held the initial MNT+USDT
        hodl_val = first["wallet_mnt"] * last["mnt_price_usdt"] + first["wallet_usdt"]
        if first["deployed_mnt"]:
            hodl_val += first["deployed_mnt"] * last["mnt_price_usdt"] + first["deployed_usdt"]

        # Pending fees + rewards from latest snapshot (may be None if API not configured)
        pending_fees_mnt = last["pending_fees_mnt"] if "pending_fees_mnt" in last.keys() else None
        pending_fees_usdt = last["pending_fees_usdt"] if "pending_fees_usdt" in last.keys() else None
        pending_rewards_mnt = last["pending_rewards_mnt"] if "pending_rewards_mnt" in last.keys() else None
        pending_value_usdt = 0.0
        if pending_fees_mnt is not None:
            pending_value_usdt += (pending_fees_mnt or 0) * (last["mnt_price_usdt"] or 0)
        if pending_fees_usdt is not None:
            pending_value_usdt += pending_fees_usdt or 0
        if pending_rewards_mnt is not None:
            pending_value_usdt += (pending_rewards_mnt or 0) * (last["mnt_price_usdt"] or 0)

        return {
            "period_days": round(elapsed_days, 1),
            "start_value": round(start_val, 2),
            "end_value": round(end_val, 2),
            "pnl_usdt": round(pnl, 2),
            "roi_pct": round(roi_pct, 2),
            "apr_pct": round(apr_pct, 1),
            "daily_pct": round(daily_pct, 3),
            # Flow-adjusted: excludes external_add/external_remove value
            "deposits_usdt": round(deposits, 2),
            "withdrawals_usdt": round(withdrawals, 2),
            "net_flow_usdt": round(net_flow, 2),
            "strategy_pnl_usdt": round(strategy_pnl, 2),
            "strategy_roi_pct": round(strategy_roi_pct, 2),
            "strategy_daily_pct": round(strategy_daily_pct, 3),
            "strategy_apr_pct": round(strategy_apr_pct, 1),
            "gas_spent_mnt": round(gas_spent, 4),
            "gas_spent_usdt": round(gas_spent * (last["mnt_price_usdt"] or 0), 2),
            "fees_earned_usdt": round(fees_earned, 2),
            "pending_fees_mnt": pending_fees_mnt,
            "pending_fees_usdt": pending_fees_usdt,
            "pending_rewards_mnt": pending_rewards_mnt,
            "pending_value_usdt": round(pending_value_usdt, 2) if pending_value_usdt else 0.0,
            "hodl_value_usdt": round(hodl_val, 2),
            "vs_hodl_usdt": round(end_val - hodl_val, 2),
            "operations": ops,
            "reverts": reverts,
            "mnt_price_start": round(first["mnt_price_usdt"], 6),
            "mnt_price_end": round(last["mnt_price_usdt"], 6),
        }

    def get_daily_summary(self, days: int = 7) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM daily_stats ORDER BY date DESC LIMIT ?
        """, (days,)).fetchall()
        return [dict(r) for r in rows]

    def get_operations_summary(self, days: int = 1) -> dict[str, Any]:
        since = time.time() - days * 86400
        rows = self.conn.execute("""
            SELECT action, COUNT(*) as count,
                   SUM(gas_mnt) as total_gas,
                   SUM(CASE WHEN fees_vs_hodl_usdt > 0 THEN fees_vs_hodl_usdt ELSE 0 END) as fees,
                   SUM(value_usdt) as volume
            FROM operations WHERE ts >= ?
            GROUP BY action
        """, (since,)).fetchall()
        return {r["action"]: dict(r) for r in rows}

    def get_recent_average_gas_mnt(
        self,
        *,
        action: str = "add",
        strategy: str | None = None,
        limit: int = 5,
    ) -> float:
        if strategy is None:
            row = self.conn.execute("""
                SELECT COALESCE(AVG(gas_mnt), 0) AS avg_gas
                FROM (
                    SELECT gas_mnt
                    FROM operations
                    WHERE action = ? AND gas_mnt > 0
                    ORDER BY ts DESC
                    LIMIT ?
                )
            """, (action, limit)).fetchone()
        else:
            row = self.conn.execute("""
                SELECT COALESCE(AVG(gas_mnt), 0) AS avg_gas
                FROM (
                    SELECT gas_mnt
                    FROM operations
                    WHERE action = ? AND strategy = ? AND gas_mnt > 0
                    ORDER BY ts DESC
                    LIMIT ?
                )
            """, (action, strategy, limit)).fetchone()
        return float(row["avg_gas"] if row is not None else 0.0)

    def get_recent_reentry_performance(
        self,
        *,
        exit_direction: str | None = None,
        limit: int = 5,
    ) -> dict[str, float | int | None]:
        query = """
            SELECT *
            FROM reentry_events
            WHERE status = 'resolved' AND followup_pnl_vs_hodl_usdt IS NOT NULL
        """
        params: list[Any] = []
        if exit_direction is not None:
            query += " AND exit_direction = ?"
            params.append(exit_direction)
        query += " ORDER BY resolved_ts DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, tuple(params)).fetchall()
        if not rows:
            return {
                "sample_count": 0,
                "avg_followup_pnl_vs_hodl_usdt": None,
                "avg_followup_in_range_ratio": None,
                "avg_fill_pct": None,
            }

        pnl_values: list[float] = []
        in_range_values: list[float] = []
        fill_values: list[float] = []
        for row in rows:
            pnl = row["followup_pnl_vs_hodl_usdt"]
            if pnl is not None:
                pnl_values.append(float(pnl))
            in_range = row["followup_in_range"]
            if in_range is not None:
                in_range_values.append(float(in_range))
            fills = [
                float(v)
                for v in (row["fill_pct_mnt"], row["fill_pct_usdt"])
                if v is not None
            ]
            if fills:
                fill_values.append(max(fills))

        def _avg(values: list[float]) -> float | None:
            if not values:
                return None
            return sum(values) / len(values)

        return {
            "sample_count": len(rows),
            "avg_followup_pnl_vs_hodl_usdt": _avg(pnl_values),
            "avg_followup_in_range_ratio": _avg(in_range_values),
            "avg_fill_pct": _avg(fill_values),
        }

    def get_reentry_tuning_summary(
        self,
        *,
        exit_direction: str | None = None,
        limit: int = 20,
        min_samples: int = 3,
        positive_pnl_usdt: float = 1.0,
        negative_pnl_usdt: float = -1.0,
        min_in_range_ratio: float = 0.5,
        low_fill_pct: float = 60.0,
        base_min_confidence: float = 0.8,
        base_max_swap_pct: float = 0.35,
        base_min_swap_usdt: float = 10.0,
        confidence_step: float = 0.05,
        swap_pct_step: float = 0.05,
        min_swap_usdt_step: float = 2.5,
        confidence_floor: float = 0.6,
        confidence_ceiling: float = 0.95,
        max_swap_pct_floor: float = 0.2,
        max_swap_pct_ceiling: float = 0.5,
        min_swap_usdt_floor: float = 5.0,
        min_swap_usdt_ceiling: float = 25.0,
    ) -> dict[str, Any]:
        query = """
            SELECT *
            FROM reentry_events
            WHERE status = 'resolved' AND followup_pnl_vs_hodl_usdt IS NOT NULL
        """
        params: list[Any] = []
        if exit_direction is not None:
            query += " AND exit_direction = ?"
            params.append(exit_direction)
        query += " ORDER BY resolved_ts DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, tuple(params)).fetchall()

        def _avg(values: list[float]) -> float | None:
            if not values:
                return None
            return sum(values) / len(values)

        def _rounded(value: float | None, digits: int = 4) -> float | None:
            if value is None:
                return None
            return round(float(value), digits)

        def _sorted_counts(values: dict[str, int]) -> dict[str, int]:
            return dict(sorted(values.items(), key=lambda item: (-item[1], item[0])))

        base_thresholds = {
            "min_confidence": round(float(base_min_confidence), 10),
            "max_swap_pct": round(float(base_max_swap_pct), 10),
            "min_swap_usdt": round(float(base_min_swap_usdt), 10),
        }
        recommended_thresholds = dict(base_thresholds)
        threshold_delta = {
            "min_confidence": 0.0,
            "max_swap_pct": 0.0,
            "min_swap_usdt": 0.0,
        }
        summary: dict[str, Any] = {
            "exit_direction": exit_direction or "all",
            "limit": limit,
            "min_samples": min_samples,
            "sample_count": len(rows),
            "selected_strategy_counts": {},
            "lp_mode_counts": {},
            "avg_followup_pnl_vs_hodl_usdt": None,
            "avg_followup_in_range_ratio": None,
            "avg_fill_pct": None,
            "avg_entry_value_usdt": None,
            "avg_turnover_usdt": None,
            "avg_recovered_value_usdt": None,
            "signals": [],
            "calibration_signal": "insufficient_recent_samples",
            "calibration_action": "hold",
            "reason": "no_resolved_reentry_events",
            "thresholds": {
                "base": base_thresholds,
                "recommended": recommended_thresholds,
                "delta": threshold_delta,
            },
        }
        if not rows:
            return summary

        pnl_values: list[float] = []
        in_range_values: list[float] = []
        fill_values: list[float] = []
        entry_values: list[float] = []
        turnover_values: list[float] = []
        recovered_values: list[float] = []
        selected_strategy_counts: dict[str, int] = {}
        lp_mode_counts: dict[str, int] = {}

        for row in rows:
            pnl = row["followup_pnl_vs_hodl_usdt"]
            if pnl is not None:
                pnl_values.append(float(pnl))
            in_range = row["followup_in_range"]
            if in_range is not None:
                in_range_values.append(float(in_range))
            fills = [
                float(v)
                for v in (row["fill_pct_mnt"], row["fill_pct_usdt"])
                if v is not None
            ]
            if fills:
                fill_values.append(max(fills))
            if row["entry_value_usdt"] is not None:
                entry_values.append(float(row["entry_value_usdt"]))
            if row["turnover_usdt"] is not None:
                turnover_values.append(float(row["turnover_usdt"]))
            if row["recovered_value_usdt"] is not None:
                recovered_values.append(float(row["recovered_value_usdt"]))
            selected_strategy = row["selected_strategy"]
            if selected_strategy:
                selected_strategy_counts[str(selected_strategy)] = (
                    selected_strategy_counts.get(str(selected_strategy), 0) + 1
                )
            lp_mode = row["lp_mode"]
            if lp_mode:
                lp_mode_counts[str(lp_mode)] = lp_mode_counts.get(str(lp_mode), 0) + 1

        avg_pnl = _avg(pnl_values)
        avg_in_range = _avg(in_range_values)
        avg_fill = _avg(fill_values)
        avg_entry_value = _avg(entry_values)
        avg_turnover = _avg(turnover_values)
        avg_recovered_value = _avg(recovered_values)
        summary.update(
            {
                "selected_strategy_counts": _sorted_counts(selected_strategy_counts),
                "lp_mode_counts": _sorted_counts(lp_mode_counts),
                "avg_followup_pnl_vs_hodl_usdt": _rounded(avg_pnl, 4),
                "avg_followup_in_range_ratio": _rounded(avg_in_range, 4),
                "avg_fill_pct": _rounded(avg_fill, 2),
                "avg_entry_value_usdt": _rounded(avg_entry_value, 4),
                "avg_turnover_usdt": _rounded(avg_turnover, 4),
                "avg_recovered_value_usdt": _rounded(avg_recovered_value, 4),
            }
        )

        calibration_direction = 0
        if len(rows) < min_samples:
            summary["signals"].append("insufficient_recent_samples")
            summary["reason"] = "insufficient_recent_samples"
            return summary

        if (
            avg_pnl is not None
            and avg_pnl >= positive_pnl_usdt
            and avg_in_range is not None
            and avg_in_range >= min_in_range_ratio
            and (avg_fill is None or avg_fill >= low_fill_pct)
        ):
            calibration_direction = 1
            summary["signals"].append("supportive_recent_outcomes")
            summary["calibration_signal"] = "supportive_recent_outcomes"
            summary["calibration_action"] = "relax"
            summary["reason"] = "recent_outcomes_supportive"
        elif (
            (avg_pnl is not None and avg_pnl <= negative_pnl_usdt)
            or (avg_in_range is not None and avg_in_range < min_in_range_ratio)
            or (avg_fill is not None and avg_fill < low_fill_pct)
        ):
            calibration_direction = -1
            summary["signals"].append("weak_recent_outcomes")
            summary["calibration_signal"] = "weak_recent_outcomes"
            summary["calibration_action"] = "tighten"
            summary["reason"] = "recent_outcomes_weak"
        else:
            summary["calibration_signal"] = "no_adjustment"
            summary["reason"] = "mixed_recent_outcomes"

        min_confidence = float(base_min_confidence)
        max_swap_pct = float(base_max_swap_pct)
        min_swap_usdt = float(base_min_swap_usdt)
        if calibration_direction > 0:
            min_confidence -= float(confidence_step)
            max_swap_pct += float(swap_pct_step)
            min_swap_usdt -= float(min_swap_usdt_step)
        elif calibration_direction < 0:
            min_confidence += float(confidence_step)
            max_swap_pct -= float(swap_pct_step)
            min_swap_usdt += float(min_swap_usdt_step)

        min_confidence = min(float(confidence_ceiling), max(float(confidence_floor), min_confidence))
        max_swap_pct = min(float(max_swap_pct_ceiling), max(float(max_swap_pct_floor), max_swap_pct))
        min_swap_usdt = min(float(min_swap_usdt_ceiling), max(float(min_swap_usdt_floor), min_swap_usdt))
        recommended_thresholds.update(
            {
                "min_confidence": round(min_confidence, 10),
                "max_swap_pct": round(max_swap_pct, 10),
                "min_swap_usdt": round(min_swap_usdt, 10),
            }
        )
        threshold_delta.update(
            {
                "min_confidence": round(recommended_thresholds["min_confidence"] - base_thresholds["min_confidence"], 10),
                "max_swap_pct": round(recommended_thresholds["max_swap_pct"] - base_thresholds["max_swap_pct"], 10),
                "min_swap_usdt": round(recommended_thresholds["min_swap_usdt"] - base_thresholds["min_swap_usdt"], 10),
            }
        )
        return summary

    def format_report(self, days: int = 0) -> str:
        """Format analytics report for Telegram."""
        roi = self.get_roi(days)
        if "error" in roi:
            return "Not enough data for analytics yet."

        period = f"Last {roi['period_days']}d" if days > 0 else f"All time ({roi['period_days']}d)"
        lines = [
            f"📊 <b>Analytics — {period}</b>",
            f"",
            f"💰 Start: ${roi['start_value']:.2f} → End: ${roi['end_value']:.2f}",
            f"{'📈' if roi['pnl_usdt'] >= 0 else '📉'} P&L: ${roi['pnl_usdt']:+.2f} ({roi['roi_pct']:+.2f}%)",
            f"📅 Daily: {roi['daily_pct']:+.3f}% | APR: {roi['apr_pct']:+.1f}%",
            f"",
            f"🏆 Fees earned: ${roi['fees_earned_usdt']:.2f}",
            f"⛽ Gas spent: {roi['gas_spent_mnt']:.4f} MNT (${roi['gas_spent_usdt']:.2f})",
            f"🔄 Operations: {roi['operations']} | Reverts: {roi['reverts']}",
            f"",
            f"📊 vs HODL: ${roi['vs_hodl_usdt']:+.2f}",
            f"💵 MNT: ${roi['mnt_price_start']:.4f} → ${roi['mnt_price_end']:.4f}",
        ]
        return "\n".join(lines)
