# LP Operations, Pool Analysis & LP-Position Analysis

Concrete commands, output fields, and examples for operating and inspecting the bot.
All commands assume the venv is active (`source .venv/bin/activate`) or are prefixed
with `.venv/bin/`. Add `--json` to anything you intend to parse.

Reminder on execution defaults (see SKILL.md): `moe lp/swap/wrap/unwrap` are **LIVE
unless `--dry-run`**; `moe-farm` is **dry-run unless `--live`**.

---

## Table of contents
1. LP management (add / remove / rebalance / farm loop / registry)
2. Pool analysis
3. LP-position analysis (inventory / range / fees / P&L)

---

## 1. LP management

### Inspect before acting
Always start read-only so you know the current position and balances:
```bash
moe snapshot --with-lp-inventory --json
moe balance --json            # aggregate: wallet + LP + fees
```

### Add a position
```bash
# PREVIEW first (no spend): distribution, bins, amounts, expected fill
moe lp add --amount-wmnt 10 --amount-usdt 5 --bin-count 10 --wrap-mnt --dry-run --json

# EXECUTE (omit --dry-run)
moe lp add --amount-wmnt 10 --amount-usdt 5 --bin-count 10 --wrap-mnt
```
Flags:
- `--amount-wmnt` / `--amount-usdt` — token amounts to deploy (required).
- `--bin-count` — width of the position in bins (narrow ≈ 3–30, wide ≈ 40–200).
- `--wrap-mnt` — wrap native MNT → WMNT to fund the WMNT side if needed.
- `--auto-rebalance` — if MNT is insufficient, rebalance the portfolio first
  (only acts when NOT a dry-run).
- `--slippage-bps` — slippage tolerance (default from `SLIPPAGE_BPS`, ~100 = 1%).

Read from the preview: the per-bin distribution, total WMNT/USDT consumed, the bin
range `[min, max]` relative to the active bin, and any preflight warning. If the
preview reverts or warns about amounts, fix inputs — do not run live.

### Remove the position
```bash
moe lp remove --dry-run --json     # preview the withdrawal (min amounts, bins burned)
moe lp remove                      # execute
```
Removal burns LBTokens across the position's nonzero bins. The bot skips zero-value
("dust") bins to avoid `LBPair__InsufficientLiquidityBurned` reverts. A live remove
**aborts immediately if the preview reverts** — never proceeds on unvalidated
amounts.

### Rebalance inventory (≈50/50) and swaps
```bash
moe swap --from-token wmnt --amount 5 --dry-run --json   # preview WMNT→USDT
moe swap --from-token usdt --amount 5                     # execute USDT→WMNT
moe wrap   --amount-mnt 3 --dry-run     # MNT → WMNT
moe unwrap --amount-wmnt 3 --dry-run    # WMNT → MNT
```
Rebalancing to ~50/50 is normally done automatically after an exit by the farm loop
(re-entry policy). Do it manually only when operating outside the loop.

### Automated farm loop (the normal way to run the strategy)
```bash
moe-farm --once --json                 # single planning cycle, DRY-RUN (safe)
moe-farm --once --live                 # single cycle, executes decisions
moe-farm --live --poll-interval-seconds 60   # continuous live loop
moe-farm --once --json --strategy narrow|wide|auto   # force/observe strategy
moe-farm --once --json --pool 0x<LBpair>             # operate an arbitrary WMNT pool
```
One cycle: snapshot → detect range → choose hold / enter / exit-and-reenter / top-up
→ (live) execute. The JSON result has `action`, `strategy`, `reason`, `timestamp`.
`action: skip` with a reason (e.g. "too volatile for narrow") is a normal safe
outcome. The loop self-replenishes native MNT, enforces the budget cap, and has an
exit circuit breaker (holds + alerts after 3 consecutive exit failures).

### Position registry & reconciliation
The bot tracks positions in `data/lp_registry.json`. If on-chain state and the
registry disagree (e.g. after a manual or external change):
```bash
moe snapshot --with-lp-inventory --json    # see on-chain bins
# reconcile is exposed via LPService.reconcile(wallet, dry_run=True) — see python-api.md
```
A `moe-farm` cycle also auto-detects external LP changes and logs them via analytics.

---

## 2. Pool analysis

### Read live pool state
```bash
moe snapshot --json     # includes the pool block
```
Key `PoolState` fields (from `LPService.get_pool_state()`):
- `active_bin_id` — the bin where the current price sits; fees accrue only to bins
  at/around it.
- `bin_step` — basis points of price gap between adjacent bins (the WMNT/USDT pool
  uses `15` = 0.15%). Bin width scales price granularity.
- `mnt_price_usdt` — current MNT price in USDT, derived from the active bin.
- token X / token Y addresses, decimals, reserves.

### Bin id ↔ price
```
price = (1 + bin_step/10000)^(bin_id - 2^23) * 10^(dec_x - dec_y)
```
`2^23 = 8388608` is the LB reference id. Never hardcode a bin id — always derive it
from the live price or read `active_bin_id`.

### Is a pool worth farming?
Look at reserves/liquidity depth and trading activity around the active bin. Deeper
liquidity + steady volume near the active bin ⇒ more fee capture for tighter
(narrow) positions. Thin or one-sided liquidity ⇒ prefer wider ranges or skip.
(For arbitrary pools, pass `--pool` to `moe snapshot`/`moe-farm`; it must be
WMNT-paired so the bot can manage native gas.)

---

## 3. LP-position analysis

### Position existence, range, in-range
```bash
moe snapshot --with-lp-inventory --json
```
From `LPService`:
- `has_active_position(wallet)` → bool
- `get_position_range(wallet)` → `(min_bin, max_bin)`
- `is_in_range(wallet)` → bool — **the key health check**: if `active_bin` is outside
  `[min_bin, max_bin]`, the position is out of range, earns no fees, and is a
  rebalance/exit candidate.
- `get_position(...)` / `get_all_active_bins(wallet)` → the nonzero bins and an
  estimate of underlying inventory per bin.

`--with-lp-inventory` estimates the underlying WMNT/USDT per bin (slower: it reads
reserves for each active bin). Use it when you need dollar values, not just bin ids.
`--deep-position` falls back to historical log scanning if fast near-active discovery
finds nothing (slower; Mantle/L2 log-range limits apply).

### Fees & P&L
```bash
moe balance --json        # aggregate wallet + LP value + accrued fees
moe-portfolio --json      # positions + balances summary (also pushes to Telegram
                          # unless --no-telegram)
```
Fees in this lean build come from **LP swap fees only** (the off-chain rewards API
and MOE-emission staking are intentionally out of scope). Flow-adjusted P&L and daily
rollups are tracked in `data/analytics.db` via the `analytics` module
(see python-api.md to query it).

### Interpreting a position
- In range + deep liquidity around active bin ⇒ healthy, earning fees; hold.
- Drifting toward an edge ⇒ watch; the farm loop has an adaptive out-of-range
  tolerance before it exits.
- Out of range ⇒ no fees; the strategy will exit-and-reenter (rebalance ~50/50, open
  a fresh position around the new active bin) unless a safety gate holds it.
