# LP Backtesting Results

Results from the `moe-backtest` harness for the Merchant Moe (Mantle) WMNT/USDT0
Liquidity Book pool. Every position is compared against two baselines:

- **HOLD** — just hold the entry token basket, no LP (no fees, no IL management).
- **STATIC** — enter one LP position and never touch it (buy-and-hold-LP).
- **STRATEGY** — the live `StrategyEngine` replayed over history (auto narrow/wide,
  exit / re-enter / rebalance exactly as the bot would).

> **Fees are an explicit assumption.** On-chain historical pool volume isn't
> available, so swap fees are estimated from an assumed pool daily volume
> (`--pool-daily-volume-usd`) distributed across candles by Bybit MNTUSDT turnover
> shape, then split by the position's share of active-bin liquidity. **Impermanent
> loss and in-range % are exact** (LB inventory conversion is deterministic). All
> runs below use `pool_daily_volume = $2,000/day` unless noted. Stablecoins
> (USDT/USDT0/USD0/USDC) are mapped 1:1 to the MNTUSDT feed.

> **Swap fees only — no farm rewards.** These figures are LB swap fees only. On
> pools with active MOE-emission / MasterChef incentives, **total yield (swap fees
> + farm rewards) can be ~3–5× the fee-only numbers here.** Reward capture is not
> in this build (future work).

---

## TL;DR

1. **Position width is the dominant profit lever — not re-centering.** A width
   sweep is an inverted-U peaking at **~20 bins (±10% price)**: it beats the
   current 10-bin position by **~+1 pt mean net (+3.92% vs +2.97%)** across a
   6-window walk-forward.
2. **Auto re-centering is ~net-neutral-to-negative for MNT/USDT this period.** It
   ties a static position most of the time and loses on the mean via
   value-destroying re-centers at price extremes (buy-the-high / sell-the-low).
3. **A passive tolerance matches static and removes the tail risk.** Raising the
   out-of-range tolerance (hold through drift, re-center only on extreme sustained
   moves) makes the strategy match static 6/6.
4. **Both LP baselines beat HOLD comfortably** — fee income is the whole game.

---

## 1. Single-window deep dive — V-shaped drawdown (76 days)

WMNT/USDT0, current live position seeded from chain. Price **$0.6747 → $0.5460
(−19.1%)** with a dump-and-bounce (V) shape. 10-bin position, RSI/regime re-entry
gate on.

| | net | final $ | fees | IL | in-range | rebal |
|---|---|---|---|---|---|---|
| HOLD | −5.6% | $117.34 | — | — | — | 0 |
| STATIC | +17.0% | $145.52 | $43.05 | $14.87 | 60.1% | 0 |
| **STRATEGY** | **+18.3%** | $147.11 | $48.21 | $1.43 | 68.3% | 1 |

Here the strategy **wins**: one re-center near the bottom (kept MNT via the
regime gate, no 50/50 sell) caught the bounce, holding in range 8 pts longer and
cutting IL. This is the strategy's best case — a clean mean-reverting V.

---

## 2. Walk-forward — the rigorous test (6 rolling 30-day windows)

A single window is anecdotal (a fixed static range can be lucky). Rolling a 30-day
window across ~4 months, 10-bin position:

**STATIC mean net = +2.97%**  (per window: −8.1, +8.6, +13.3, +5.5, −4.9, +3.4)
**HOLD mean net  = −4.44%**

| strategy approach | mean net | vs static | wins | notes |
|---|---|---|---|---|
| baseline (auto re-center) | +0.3% | −2.7 | 5/6 | one −16.8 catastrophe drags the mean |
| trend-confirmation gate | +0.3% | −2.7 | 5/6 | **inert** — round-trip legs read as trends |
| stabilization-hold | −0.5% | −3.5 | 5/6 | **worse** — re-deploys at still-elevated prices |
| **passive tolerance (≥30 bins)** | **+3.0%** | **+0.0** | **6/6** | matches static, removes tail risk |

**Why baseline loses:** in 4/6 windows the strategy never re-centered and *tied*
static; in the 2 windows it did re-center, it was a coin-flip — one small win
(+0.7) vs one disaster (−16.8). The disaster: it re-centered at a **$0.81 local
peak** and limit-bought MNT all the way down to $0.67 ("bought the high"). The
mirror failure (sell-the-low) shows up in down-spikes.

**Conclusion:** re-centering adds risk without reliable reward here. The best
*behavioral* outcome is to re-center as little as possible — which converges to
static.

---

## 3. Position-width optimization — the actual win

A hyperparameter search (random search, scored on the walk-forward) found the top
configurations **all share one thing: initial width = 20 bins**. Every
re-centering knob (tolerance, RSI gate, stabilization, cadence, mode) had **zero
effect** — a 20-bin position never went out of range, so re-centering never fired.

Isolating width (static position, no re-centering), mean net across the 6 windows:

| width (bins) | ± range | mean net |
|---|---|---|
| 8 | ±4% | +2.72% |
| 10 (current) | ±5% | +2.97% |
| 15 | ±7.5% | +3.72% |
| **20** | **±10%** | **+3.92%** ★ |
| 25 | ±12.5% | +3.24% |
| 30 | ±15% | +2.84% |
| 40 | ±20% | +1.90% |

Clear inverted-U. **Too narrow** → high fee concentration but frequent
out-of-range (misses fees). **Too wide** → always in range but fee share diluted.
The **~±10% sweet spot** balances both and beats the current 10-bin by ~+1 pt
(~32% relative) over this period.

> **Width is in BINS, optimum is in PRICE.** ±10% is **20 bins on binStep-100**
> but **~133 bins on the default binStep-15 pool** (price-% ≈ bins × bin_step/100).
> Tune `BIN_COUNT` per pool. `BIN_COUNT` is already configurable — no code change.

---

## 4. Recommendations

1. **Tune `BIN_COUNT` to ~±10% in price terms** for the target pool — the single
   highest-impact change.
2. **Run more passively**: raise the out-of-range tolerance
   (`OOR_TOLERANCE_BINS` / `OOR_TOLERANCE_CAP_BINS`, opt-in, default unchanged) so
   the bot only re-centers on extreme sustained moves. This removes the
   value-destroying re-centers at extremes.
3. **Re-centering earns its keep only in sustained trends** (where a static
   position would go permanently out of range and earn $0). In choppy /
   mean-reverting markets it is a drag — keep it rare.
4. **For maximum yield, prefer pools with active reward incentives** — swap fees
   alone (measured here) are a fraction of fees + farm rewards (~3–5× total).

---

## 5. Reproduce

```bash
# Single backtest of the current live position (seeds geometry/fees/depth from chain)
moe-backtest --seed-from-pool 0x03BeafC0d25BB553fCa274301832419C05269987 --days 90 \
  --pool-daily-volume-usd 2000 --save data/backtests/current_lp.json --chart data/backtests/current_lp.png

# Walk-forward (strategy vs static across rolling windows)
.venv/bin/python scripts/backtest_walkforward.py

# Parameter sweep / hyperopt
.venv/bin/python scripts/backtest_compare.py
.venv/bin/python scripts/backtest_hyperopt.py
```

Candle history caches to `data/candles/`; backtest JSON/PNG to `data/backtests/`.
Full harness reference: [`skills/moe-lp-operations/references/backtesting.md`](skills/moe-lp-operations/references/backtesting.md).

---

## 6. Caveats

- **Fee magnitude is an assumption** (`--pool-daily-volume-usd`). IL, in-range %,
  and the relative ranking (width effect, static-vs-strategy) are robust; absolute
  APR scales with the volume assumption — calibrate to real on-chain volume.
- **Validated on one pool (WMNT/USDT0, binStep-100) over ~4 months.** Different
  pools/regimes may differ; the width optimum is pool-specific.
- **Swap fees only** — no MOE-emission / MasterChef rewards (would raise total
  yield ~3–5×).
- Bybit perp price is used as a proxy for the on-chain pool price; the stablecoin
  peg is assumed exactly $1.
