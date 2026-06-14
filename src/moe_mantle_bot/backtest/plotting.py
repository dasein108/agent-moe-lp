"""Render a backtest chart: price + LP range band, and equity curves.

Uses matplotlib (static PNG, no browser). Imported lazily so the rest of the
backtest works without matplotlib installed.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def render_chart(result, out_path: str | Path) -> Path:
    """Write a two-panel PNG for a BacktestResult and return the path."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["text.parse_math"] = False  # render literal '$' in labels
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    s = result.series
    if not s:
        raise ValueError("result has no series data to plot")

    ts = pd.to_datetime(pd.Series(s["ts"]), utc=True)
    price = s["price"]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [2.2, 1]})

    # ── Panel 1: price + LP range bands ──
    ax1.plot(ts, price, color="#1f77b4", lw=1.1, label="MNT price (USDT)", zorder=3)
    # static range (constant band)
    ax1.axhspan(s["static_lo"], s["static_hi"], color="#999999", alpha=0.15,
                label=f"static LP range  ${s['static_lo']:.3f}–${s['static_hi']:.3f}")
    # strategy range (evolving step band)
    ax1.fill_between(ts, s["strat_lo"], s["strat_hi"], step="post",
                     color="#2ca02c", alpha=0.18, label="strategy LP range", zorder=1)
    ax1.plot(ts, s["strat_lo"], color="#2ca02c", lw=0.6, alpha=0.5, drawstyle="steps-post")
    ax1.plot(ts, s["strat_hi"], color="#2ca02c", lw=0.6, alpha=0.5, drawstyle="steps-post")

    # mark re-center events
    for ev in result.events:
        et = pd.to_datetime(ev["ts"], utc=True)
        ax1.axvline(et, color="#d62728", ls="--", lw=0.8, alpha=0.6)
        ax1.annotate(ev.get("new_strategy", "re-enter"), xy=(et, ev["price"]),
                     fontsize=7, color="#d62728", rotation=90,
                     va="bottom", ha="right")

    sm, gm = result.static, result.strategy
    ax1.set_ylabel("price (USDT / MNT)")
    ax1.set_title(
        f"{result.config['symbol']}  {result.start[:10]} → {result.end[:10]}  "
        f"({sm.days:.0f}d)   "
        f"static net {sm.net_pnl_pct:+.1f}% (IL ${sm.il_usd}) | "
        f"strategy net {gm.net_pnl_pct:+.1f}% (IL ${gm.il_usd}, {gm.rebalances} rebal)",
        fontsize=10)
    ax1.legend(loc="upper right", fontsize=8)
    ax1.grid(True, alpha=0.2)

    # ── Panel 2: equity (value + cumulative fees) ──
    if s.get("hold_equity"):
        ax2.plot(ts, s["hold_equity"], color="#ff7f0e", lw=1.0, ls="--", label="hold (no LP)")
    ax2.plot(ts, s["static_equity"], color="#999999", lw=1.1, label="static equity")
    ax2.plot(ts, s["strategy_equity"], color="#2ca02c", lw=1.1, label="strategy equity")
    ax2.axhline(result.config["capital_usd"], color="#444", ls=":", lw=0.8,
                label=f"initial ${result.config['capital_usd']:.0f}")
    ax2.set_ylabel("portfolio value (USD)")
    ax2.set_xlabel("date (UTC)")
    ax2.legend(loc="upper right", fontsize=8)
    ax2.grid(True, alpha=0.2)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path
