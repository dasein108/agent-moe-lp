"""``moe-backtest`` — backtest an LB position over historical Bybit candles.

Defaults mirror the live WMNT/USDT0 position. Pass ``--seed-from-pool <addr>``
to ground the geometry, fee params, pool depth, and current position size in a
live on-chain snapshot.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import shutil
import subprocess
import sys

from .config import BacktestConfig
from .simulator import run_backtest


def _seed_from_pool(addr: str, cfg_kwargs: dict) -> None:
    """Best-effort: fill geometry/fee/depth/position from a live snapshot."""
    moe = shutil.which("moe")
    if not moe:
        print("warn: `moe` CLI not found; cannot seed from pool, using flags/defaults", file=sys.stderr)
        return
    try:
        out = subprocess.run(
            [moe, "--pool", addr, "snapshot", "--with-lp-inventory", "--json"],
            capture_output=True, text=True, timeout=120, check=True).stdout
        d = json.loads(out)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"warn: pool seed failed ({e}); using flags/defaults", file=sys.stderr)
        return

    pool = d.get("pool", {})
    price = float(pool.get("mnt_price_usdt") or 0) or None
    if pool.get("bin_step"):
        cfg_kwargs["bin_step"] = int(pool["bin_step"])
    tx, ty = pool.get("token_x", {}), pool.get("token_y", {})
    if tx.get("decimals") is not None:
        cfg_kwargs["decimals_x"] = int(tx["decimals"])
    if ty.get("decimals") is not None:
        cfg_kwargs["decimals_y"] = int(ty["decimals"])
    sfp = pool.get("static_fee_parameters", {})
    if sfp.get("base_factor"):
        cfg_kwargs["base_factor"] = int(sfp["base_factor"])
    if sfp.get("protocol_share"):
        cfg_kwargs["protocol_share_bps"] = int(sfp["protocol_share"])

    pos = d.get("position") or {}
    bins = pos.get("active_bins") or []
    active = pool.get("active_bin_id")
    if bins and price:
        cfg_kwargs["bin_count"] = len(bins)
        mnt = sum(float(b.get("estimated_token_x") or 0) for b in bins)
        quote = sum(float(b.get("estimated_token_y") or 0) for b in bins)
        cfg_kwargs["capital_usd"] = round(mnt * price + quote, 2)
        cfg_kwargs["quote_usd_target"] = round(quote, 2)
        # competing liquidity in the active bin (pool reserve there, minus ours)
        for b in bins:
            if b.get("bin_id") == active:
                pool_x = int(b.get("bin_reserve_x_raw", 0)) / (10 ** cfg_kwargs.get("decimals_x", 18))
                our_x = float(b.get("estimated_token_x") or 0)
                comp = max(0.0, pool_x - our_x) * price
                if comp > 0:
                    cfg_kwargs["pool_active_liquidity_usd"] = round(comp, 2)
                break
    print(f"seeded from pool {addr}: bin_step={cfg_kwargs.get('bin_step')} "
          f"base_factor={cfg_kwargs.get('base_factor')} capital=${cfg_kwargs.get('capital_usd')} "
          f"quote=${cfg_kwargs.get('quote_usd_target')} "
          f"pool_active_liq=${cfg_kwargs.get('pool_active_liquidity_usd')}", file=sys.stderr)


def _fmt(m, baseline=None) -> str:
    return (f"  {m.label:9}  fees=${m.total_fees_usd:>8}  IL=${m.il_usd:>8}  "
            f"net=${m.net_pnl_usd:>8} ({m.net_pnl_pct:>6}%)  feeAPR={m.fee_apr_pct:>6}%  "
            f"netAPR={m.net_apr_pct:>7}%  inRange={m.in_range_pct:>5}%  "
            f"rebal={m.rebalances:>3}  maxDD={m.max_drawdown_pct:>5}%")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="moe-backtest", description="Backtest an LB LP position over Bybit history.")
    p.add_argument("--seed-from-pool", metavar="ADDR", help="seed geometry/fees/position from a live pool snapshot")
    p.add_argument("--symbol", default="MNTUSDT")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--interval", default="5m", help="base marking/fee interval")
    p.add_argument("--capital", type=float, help="total capital USD")
    p.add_argument("--quote-usd", type=float, help="USD on the quote side; rest is MNT")
    p.add_argument("--bin-count", type=int)
    p.add_argument("--bin-step", type=int)
    p.add_argument("--base-factor", type=int)
    p.add_argument("--protocol-share-bps", type=int)
    p.add_argument("--lp-fee-bps", type=float, help="override LP-net fee rate (bps)")
    p.add_argument("--capture-ratio", type=float, help="pool_volume / bybit_turnover")
    p.add_argument("--pool-active-liq-usd", type=float, help="competing USD liquidity in active bin")
    p.add_argument("--strategy", choices=["auto", "narrow", "wide"], default="auto")
    p.add_argument("--decision-period-min", type=int, default=60)
    p.add_argument("--slippage-bps", type=float)
    p.add_argument("--gas-per-tx-mnt", type=float)
    p.add_argument("--refresh", action="store_true", help="ignore candle cache")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save", metavar="PATH")
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING,
                        format="%(message)s", stream=sys.stderr)

    cfg_kwargs: dict = {}
    if args.seed_from_pool:
        _seed_from_pool(args.seed_from_pool, cfg_kwargs)

    # explicit flags override seeded/default values
    flag_map = {
        "symbol": args.symbol, "lookback_days": args.days, "base_interval": args.interval,
        "capital_usd": args.capital, "quote_usd_target": args.quote_usd, "bin_count": args.bin_count,
        "bin_step": args.bin_step, "base_factor": args.base_factor,
        "protocol_share_bps": args.protocol_share_bps, "lp_fee_bps": args.lp_fee_bps,
        "capture_ratio": args.capture_ratio, "pool_active_liquidity_usd": args.pool_active_liq_usd,
        "strategy_mode": args.strategy, "decision_period_min": args.decision_period_min,
        "slippage_bps": args.slippage_bps, "gas_per_tx_mnt": args.gas_per_tx_mnt,
    }
    for k, v in flag_map.items():
        if v is not None:
            cfg_kwargs[k] = v

    valid = {f.name for f in dataclasses.fields(BacktestConfig)}
    cfg = BacktestConfig(**{k: v for k, v in cfg_kwargs.items() if k in valid})

    result = run_backtest(cfg, refresh=args.refresh)

    if args.save:
        from pathlib import Path
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(result.to_dict(), indent=2) + "\n")
        print(f"saved {args.save}", file=sys.stderr)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
        return 0

    r = result
    print(f"\nBacktest {cfg.symbol}  {r.start[:10]} → {r.end[:10]}  "
          f"({r.static.days:.0f}d)   price ${r.start_price:.4f} → ${r.final_price:.4f} "
          f"({(r.final_price/r.start_price-1)*100:+.1f}%)")
    print(f"capital ${cfg.capital_usd}  quote-side ${cfg.quote_usd_target}  "
          f"{cfg.bin_count} bins @ binStep {cfg.bin_step}  "
          f"LP fee {cfg.derived_lp_fee_rate()*100:.3f}%  capture {cfg.capture_ratio}  "
          f"poolActiveLiq ${cfg.pool_active_liquidity_usd}")
    print("-" * 110)
    print(_fmt(r.static))
    print(_fmt(r.strategy))
    print("-" * 110)
    print("assumptions: fees estimated from Bybit turnover × capture_ratio (not on-chain volume); "
          "stablecoins mapped 1:1 to USDT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
