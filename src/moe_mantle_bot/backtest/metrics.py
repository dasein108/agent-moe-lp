"""Backtest performance metrics."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class BacktestMetrics:
    label: str
    days: float
    initial_value_usd: float
    final_lp_value_usd: float      # position value at end (mark-to-market)
    total_fees_usd: float
    il_usd: float                  # HODL - LP value at end (positive = loss vs hodl)
    gas_cost_usd: float
    rebalance_cost_usd: float
    net_pnl_usd: float             # final + fees - costs - initial
    net_pnl_pct: float
    fee_apr_pct: float             # annualized fee yield on capital
    net_apr_pct: float
    in_range_pct: float
    rebalances: int
    max_drawdown_pct: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute_metrics(
    *,
    label: str,
    days: float,
    initial_value_usd: float,
    final_lp_value_usd: float,
    hodl_value_usd: float,
    total_fees_usd: float,
    gas_cost_usd: float,
    rebalance_cost_usd: float,
    in_range_steps: int,
    total_steps: int,
    rebalances: int,
    equity_curve: list[float],
) -> BacktestMetrics:
    il_usd = hodl_value_usd - final_lp_value_usd
    costs = gas_cost_usd + rebalance_cost_usd
    net_pnl = final_lp_value_usd + total_fees_usd - costs - initial_value_usd
    net_pnl_pct = (net_pnl / initial_value_usd * 100.0) if initial_value_usd else 0.0
    years = days / 365.0 if days > 0 else 0.0
    fee_apr = (total_fees_usd / initial_value_usd / years * 100.0) if (initial_value_usd and years) else 0.0
    net_apr = (net_pnl / initial_value_usd / years * 100.0) if (initial_value_usd and years) else 0.0
    in_range_pct = (in_range_steps / total_steps * 100.0) if total_steps else 0.0

    peak = -float("inf")
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak * 100.0
            max_dd = max(max_dd, dd)

    return BacktestMetrics(
        label=label,
        days=round(days, 2),
        initial_value_usd=round(initial_value_usd, 2),
        final_lp_value_usd=round(final_lp_value_usd, 2),
        total_fees_usd=round(total_fees_usd, 2),
        il_usd=round(il_usd, 2),
        gas_cost_usd=round(gas_cost_usd, 2),
        rebalance_cost_usd=round(rebalance_cost_usd, 2),
        net_pnl_usd=round(net_pnl, 2),
        net_pnl_pct=round(net_pnl_pct, 2),
        fee_apr_pct=round(fee_apr, 1),
        net_apr_pct=round(net_apr, 1),
        in_range_pct=round(in_range_pct, 1),
        rebalances=rebalances,
        max_drawdown_pct=round(max_dd, 1),
    )
