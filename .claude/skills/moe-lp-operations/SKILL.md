---
name: moe-lp-operations
description: >-
  Operate and analyze the Merchant Moe (Mantle) Liquidity Book LP farming bot in this
  repo. Use this skill whenever the user wants to manage LP positions (add / remove /
  preview / rebalance), manage the wallet (create a new wallet, use the current
  `wallet.json`, switch wallets), analyze the pool (active bin, price, bin step,
  reserves), analyze an LP position (inventory, in-range, fees, P&L), or do market /
  technical analysis (Keltner channel width, ATR, multi-timeframe regime,
  narrow-vs-wide selection) — even if they don't name the bot or say "skill". Triggers
  on requests like "add liquidity", "remove the LP", "create a new wallet", "use my
  wallet.json", "is my position in range", "what's the pool price / active bin",
  "should it go narrow or wide", "run the farm loop", "check fees / P&L", "analyze MNT
  volatility / Keltner", or anything about the Mantle WMNT/USDT Merchant Moe pool. Use
  it before running any `moe`/`moe-farm` command.
---

# Merchant Moe (Mantle) LP Operations — Claude entry point

This is a thin pointer. The full, runtime-agnostic operating guide lives in the repo
so every agent shares one source of truth.

**Read the canonical skill now, then follow it:**

- Start: `skills/moe-lp-operations/SKILL.md` — overview, the **execution-default
  safety table** (manual `moe` commands are LIVE by default; `moe-farm` is dry-run by
  default), environment setup, and the task→reference index.
- Then open the reference for the task:
  - Wallet management (create / select / inspect; default `wallet.json`) →
    `skills/moe-lp-operations/references/wallet.md`
  - LP management + pool analysis + LP-position analysis →
    `skills/moe-lp-operations/references/lp-operations.md`
  - Market / technical analysis (Keltner, ATR, MTF, bias → narrow/wide) →
    `skills/moe-lp-operations/references/market-analysis.md`
  - Backtesting (fee yield / IL / in-range / net PnL over Bybit history;
    `moe-backtest`) → `skills/moe-lp-operations/references/backtesting.md`
  - Programmatic (Python) access to `LPService` / `BalanceManager` / `quant/` →
    `skills/moe-lp-operations/references/python-api.md`

**Before doing anything that can spend funds**, internalize from the canonical
SKILL.md:
- `moe lp add/remove`, `moe swap/wrap/unwrap` execute **for real unless you pass
  `--dry-run`** — always preview first.
- `moe-farm` is **safe (dry-run) by default**; it only trades with `--live`.
- Never invent prices or bin ids — read them live; if data is unavailable, stop.

Also honor the repo's `CLAUDE.md` rules (no hardcoded market data; update
`docs/actual_trading_strategy.md` Change Log when you change trading logic).
