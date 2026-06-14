---
name: moe-lp-operations
description: >-
  Canonical, LLM-agnostic operating guide for the Merchant Moe (Mantle) Liquidity
  Book LP farming bot. Use for ALL of: LP management (add / remove / preview /
  rebalance positions), pool analysis (active bin, price, bin step, reserves),
  LP-position analysis (inventory, in-range check, fees, P&L), and market analysis
  (Keltner channel width, ATR, multi-timeframe regime, bias → narrow-vs-wide
  selection). Read this whenever you must inspect or operate the bot against a
  WMNT-paired Merchant Moe pool, regardless of which agent runtime you are.
---

# Merchant Moe (Mantle) LP Operations

This is the shared, runtime-agnostic playbook for operating the bot in
`src/moe_mantle_bot/`. Any agent (Claude, Codex, Cursor, a CI job) can follow it.
It tells you **which command or API to run for each task, how to read the output,
and which mistakes cause loss of funds.**

## What this bot is

- **Chain:** Mantle mainnet (chain id `5000`, RPC `https://rpc.mantle.xyz`).
- **AMM:** Merchant Moe **Liquidity Book V2.2** — a Trader Joe LB fork. Liquidity
  lives in discrete **bins** (price levels); ownership is fungible LBToken balances
  per bin, not NFTs. A position = the set of nonzero bins you hold.
- **Market:** `WMNT/USDT` LB pair. `WMNT` is the wrapped native gas token (MNT);
  `USDT` is the 6-decimal quote.
- **Strategy:** single narrow (≈3–30 bins) or wide (≈40–200 bins) position, auto-
  selected from Keltner channel width + ATR; monitored for range, removed when out
  of range, rebalanced ~50/50, re-entered. Fee-farming only (no MOE staking).

## ⚠️ Read before running anything that spends money

The two CLIs have **opposite execution defaults.** Getting this wrong sends real
on-chain transactions.

| Tool | Default | To preview | To execute |
|------|---------|-----------|-----------|
| `moe lp add/remove`, `moe swap/wrap/unwrap` | **LIVE** | add `--dry-run` | omit `--dry-run` |
| `moe-farm` | **dry-run** | (default) | add `--live` |

So: **always run manual `moe` mutations with `--dry-run` first**, read the preview,
then re-run without it. For the farm loop it is the reverse — it is safe by default
and only trades with `--live`.

Other safety facts:
- The bot reserves native MNT for gas and caps LP allocation at a budget % of the
  wallet. A preview that reverts means the live tx will also revert — never push a
  live tx past a failed preview.
- `--pool 0x<LBpair>` overrides the configured pool on `moe` and `moe-farm`. Tokens
  are auto-discovered on-chain; the pool MUST be WMNT-paired (the bot wraps/unwraps
  MNT for gas).

## Environment

```bash
cd <repo-root>
source .venv/bin/activate          # or call binaries directly: .venv/bin/moe ...
# Config comes from .env (chain, POOL_ADDRESS, wallet, slippage, gas reserve…).
# Read-only inspection needs only an RPC + an address; mutations need the wallet.
```

**⚠️ Stale `.env` gotcha:** the repo's defaults already point at the Mantle/Merchant Moe
constants, but a stale `.env` may carry wrong-chain or old-key values that override the
Mantle defaults — a command would then read the wrong chain/pool. First verify with
`grep -E 'CHAIN_ID|POOL_ADDRESS|MANTLE_RPC_URL|WMNT_ADDRESS|USDT_ADDRESS' .env`. If it
shows the wrong chain/pool, either fix `.env` from `.env.example` (preferred for real
runs) or override per-invocation for a quick read, e.g.:
```bash
CHAIN_ID=5000 MANTLE_RPC_URL=https://rpc.mantle.xyz \
POOL_ADDRESS=0xf6c9020c9e915808481757779edb53daceae2415 \
WMNT_ADDRESS=0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8 \
USDT_ADDRESS=0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE \
MOE_FACTORY_ADDRESS=0xa6630671775c4EA2743840F9A5016dCf2A104054 \
moe snapshot --json
```
Don't guess values — these are the live constants from
`src/moe_mantle_bot/constants.py`.

CLIs: `moe` (manual ops), `moe-farm` (automated loop), `moe-readonly` (snapshot by
address), `moe-portfolio` (positions + balances summary). Every command takes
`--json` for machine-readable output — **prefer `--json` when parsing.**

**Wallet:** by default every read/write resolves to **`wallet.json`** in the working
dir (override with `--wallet-file` or `WALLET_FILE`; falls back to `PRIVATE_KEY`).
`wallet.json` holds a plaintext private key (git-ignored, `0o600`) — never print,
commit, or share it. Create one with `moe wallet create`; inspect with
`moe wallet show` (address only). Full details → `references/wallet.md`.

## Pick the task, then read the matching reference

This file is the index + the safety layer. For the actual step-by-step commands,
flags, output fields, and worked examples, open the reference for your task:

- **Wallet management** — create a new wallet, select/inspect the current one,
  default `wallet.json` resolution, security handling of the private key:
  → `references/wallet.md`

- **LP management** — add / remove / preview a position, rebalance inventory,
  reconcile the position registry, run the automated farm loop:
  → `references/lp-operations.md`

- **Pool analysis** — active bin, current price, bin step, reserves, how bin id ↔
  price works, whether a pool is worth farming:
  → `references/lp-operations.md` (section "Pool analysis")

- **LP-position analysis** — your bin inventory, in-range check, position range,
  estimated underlying WMNT/USDT, accrued fees, flow-adjusted P&L:
  → `references/lp-operations.md` (section "LP-position analysis")

- **Market / technical analysis** — Keltner channel width, ATR floor, multi-
  timeframe regime (RSI/EMA), bias, and exactly how those map to narrow-vs-wide
  bin sizing and entry gates:
  → `references/market-analysis.md`

- **Backtesting** — estimate fee yield, impermanent loss, in-range time, and net
  PnL of a position over historical Bybit candles; replays the live
  `StrategyEngine` vs a static baseline; `moe-backtest --seed-from-pool`:
  → `references/backtesting.md`

- **Programmatic access** — when a CLI flag doesn't expose what you need, drive
  `LPService`, `BalanceManager`, and the `quant/` analyzers directly in Python:
  → `references/python-api.md`

## Standard workflow for a question like "how is the position doing?"

1. **Snapshot the world** (read-only, no spend):
   `moe snapshot --with-lp-inventory --json` → pool state + wallet + position.
2. **Check range:** is `active_bin` within the position's `[min_bin, max_bin]`?
   Out of range ⇒ the position earns no fees and is a rebalance candidate.
3. **Read the market:** compute Keltner width + MTF regime (market-analysis.md) to
   know whether conditions favor narrow (tight, ranging) or wide (volatile).
4. **Decide & preview:** if action is needed, `--dry-run` the `moe lp`/`moe swap`
   command, confirm the preview, then execute. Or let `moe-farm --once --live`
   make and execute the decision for you.
5. **Verify:** re-snapshot; confirm the new range brackets `active_bin` and the
   registry reflects the change (`reconcile` if it doesn't).

## Grounding rules (non-negotiable)

- **Never invent market data.** Prices, bin ids, reserves, balances must come from a
  live RPC/API call. If data is unavailable, say so and stop — do not assume a price
  or bin id. (Bin ids are derived from price and change constantly; there is no
  "default" bin id.)
- **Preview before live.** A reverting preview predicts a reverting live tx.
- **One position.** This is single-position farming; do not open overlapping
  positions or treat it as a multi-pool manager.
- When you change anything that affects trading decisions, follow the repo's change
  rules in `CLAUDE.md` / `docs/actual_trading_strategy.md`.
