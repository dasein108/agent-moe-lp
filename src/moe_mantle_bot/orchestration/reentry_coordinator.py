from __future__ import annotations

import json
from typing import Any, Callable


class ReentryExecutionCoordinator:
    """Own re-entry result shaping and analytics event finalization."""

    def __init__(
        self,
        *,
        analytics,
        safe_float: Callable[[Any], float],
    ) -> None:
        self.analytics = analytics
        self._safe_float = safe_float

    def close_exit_only(
        self,
        result: dict[str, Any],
        *,
        reason: str,
        reentry_event_id: str | None,
        reentry_policy_result: dict[str, Any] | None,
        selected_strategy: str | None = None,
    ) -> dict[str, Any]:
        result["action"] = "exit_only"
        if reason:
            result["reason"] = reason
        if reentry_event_id is not None:
            self.analytics.close_reentry_event(
                reentry_event_id,
                status="exit_only",
                selected_strategy=selected_strategy,
                notes=json.dumps(
                    {
                        "reason": reason,
                        "policy": reentry_policy_result,
                    },
                    sort_keys=True,
                ),
            )
        return result

    def finalize_create_result(
        self,
        *,
        result: dict[str, Any],
        strategy: str,
        intent,
        create_result: dict[str, Any],
        reentry_event_id: str | None,
        reentry_policy_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if create_result.get("action", "").startswith("enter_"):
            result["action"] = f"reenter_{strategy}"
            result["strategy"] = strategy
            result["bin_count"] = intent.range_plan.bin_count if intent.range_plan else None
            result["lp_mode"] = create_result.get("lp_mode")
            result["expected_refund_mnt"] = create_result.get("expected_refund_mnt")
            result["expected_refund_usdt"] = create_result.get("expected_refund_usdt")
            result["fill_pct_mnt"] = create_result.get("fill_pct_mnt")
            result["fill_pct_usdt"] = create_result.get("fill_pct_usdt")
            if reentry_event_id is not None:
                turnover = self._safe_float(create_result.get("entry_value_usdt"))
                recovered_value = self._recovered_value_usdt(reentry_event_id)
                self.analytics.complete_reentry_entry(
                    reentry_event_id,
                    selected_strategy=strategy,
                    entry_bin_count=int(intent.range_plan.bin_count or 0),
                    entry_value_usdt=self._safe_float(create_result.get("entry_value_usdt")),
                    lp_mode=create_result.get("lp_mode"),
                    expected_refund_mnt=self._safe_float(create_result.get("expected_refund_mnt")),
                    expected_refund_usdt=self._safe_float(create_result.get("expected_refund_usdt")),
                    fill_pct_mnt=create_result.get("fill_pct_mnt"),
                    fill_pct_usdt=create_result.get("fill_pct_usdt"),
                    turnover_usdt=recovered_value + turnover,
                    notes=json.dumps(
                        {
                            "entry": f"reentered_{strategy}",
                            "policy": reentry_policy_result,
                        },
                        sort_keys=True,
                    ),
                )
            return result

        result["action"] = "exit_only"
        action = create_result.get("action", "")
        if action not in {"hold", "skip", "skip_narrow", "skip_wide"}:
            result["reentry_gate"] = create_result if create_result.get("reason") else None
        if action.startswith("skip_") or action in {"hold", "skip", "skip_narrow", "skip_wide"}:
            result["reason"] = create_result.get("reason", "reentry_entry_skipped")
            result["reenter_skip"] = create_result
        else:
            result["reenter_error"] = create_result.get("error", "unknown")
        if reentry_event_id is not None:
            self.analytics.close_reentry_event(
                reentry_event_id,
                status=(
                    "exit_only"
                    if action.startswith("skip_") or action in {"hold", "skip", "skip_narrow", "skip_wide"}
                    else "error"
                ),
                selected_strategy=strategy,
                notes=json.dumps(
                    (
                        {
                            "reason": result.get("reason", "reentry_entry_skipped"),
                            "skip": create_result,
                            "policy": reentry_policy_result,
                        }
                        if action.startswith("skip_") or action in {"hold", "skip", "skip_narrow", "skip_wide"}
                        else {
                            "error": result["reenter_error"],
                            "policy": reentry_policy_result,
                        }
                    ),
                    sort_keys=True,
                ),
            )
        return result

    def finalize_exception(
        self,
        result: dict[str, Any],
        *,
        error: Exception,
        reentry_event_id: str | None,
        reentry_policy_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result["action"] = "exit_only"
        result["reenter_error"] = str(error)
        if reentry_event_id is not None:
            self.analytics.close_reentry_event(
                reentry_event_id,
                status="error",
                notes=json.dumps(
                    {
                        "error": str(error),
                        "policy": reentry_policy_result,
                    },
                    sort_keys=True,
                ),
            )
        return result

    def _recovered_value_usdt(self, reentry_event_id: str) -> float:
        row = self.analytics.conn.execute(
            "SELECT recovered_value_usdt FROM reentry_events WHERE id = ?",
            (reentry_event_id,),
        ).fetchone()
        if row is None:
            return 0.0
        return float(row["recovered_value_usdt"] or 0)
