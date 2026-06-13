# Market / Technical Analysis (Keltner, ATR, MTF, Bias)

How the bot reads the market to choose between a **narrow** and a **wide** LP position
and whether to gate entries. This is the "why" behind strategy selection. The data
source is **Bybit `MNTUSDT` candles** (perpetual, `category=linear`); if Bybit is
unreachable the analyzers degrade gracefully (regime defaults to RANGING/neutral with
zero confidence) and the bot makes a conservative decision rather than crashing.

The live decision path lives in `farm_bot.select_strategy()` and the `quant/`
analyzers. To compute these yourself, see `python-api.md`.

---

## The core idea: channel width → position width

A Liquidity Book position only earns fees while the price stays inside its bins. So
the bot sizes the position to the expected price travel:

- **Tight, low-volatility, ranging market** → a **narrow** position (few bins) packs
  liquidity where price actually sits, maximizing fee density. Risk: price leaves the
  range and you stop earning (and must rebalance).
- **Volatile / trending / wide-channel market** → a **wide** position (many bins)
  keeps the price in range longer, trading fee density for staying-in-range. Risk:
  diluted fees per bin.

The width signal is the **Keltner channel width** with an **ATR floor**.

## 1. Keltner channel (`quant/keltner_analyzer.py`)

A Keltner channel is an EMA mid-line with bands set a multiple of ATR above/below.
`KeltnerAnalyzer.analyze_channel_conditions(symbol)` returns, per the candle set:
- **channel width %** — band-to-band width relative to price. This is the primary
  narrow-vs-wide driver. Wider channel ⇒ more expected travel ⇒ wider position.
- **channel quality / confidence** — how clean/reliable the channel is. Low
  confidence ⇒ fall back to the safe default (narrow) rather than trusting a wide
  signal.
- bounds (upper/mid/lower) and where price sits within them.

Rule of thumb encoded in the bot: above a width threshold the market is "too volatile
for narrow" → it widens or skips a narrow entry; a tight channel with decent
confidence supports a narrow position.

## 2. ATR floor

ATR (average true range) is a raw volatility measure. It acts as a **floor** on bin
count so that even if the Keltner width looks small, a minimum width is enforced when
absolute volatility is high. This prevents opening a too-tight position right before a
volatility expansion.

## 3. Wide bin sizing (`quant/wide_range_lp_manager.py`)

`calculate_wide_range_params(keltner_analysis, daily_atr_pct, pool_stats=None)`
combines several candidate bin counts and takes a sensible blend:
- `from_keltner` — bins implied by channel width
- `from_sqrt` — a sqrt-scaled width term
- `from_atr` — bins implied by ATR
→ clamped into the wide range (≈40–200 bins).
Note: in this lean build `pool_stats` is `None` (its data source, the off-chain pool-
stats API, was removed), so **fee-rate-based bin tuning is inert** — sizing is driven
by Keltner + ATR only.

## 4. Multi-timeframe regime (`quant/mtf_analyzer.py`)

`MTFAnalyzer.analyze("MNTUSDT")` aggregates several timeframes (5m/1h/4h) into an
`MTFAnalysis`: a **regime** (`TRENDING_UP` / `TRENDING_DOWN` / `RANGING`), a
**confidence**, **bias** (bullish/bearish/neutral), **overbought/oversold** flags
(RSI), and **daily ATR %**. Used to:
- confirm whether to trust a wide deployment (ranging + adequate confidence) vs default
  to narrow,
- feed **entry gates**: e.g. a strong overbought reading in an uptrend can make the
  bot **hold** rather than enter (avoid buying the top); oversold can scale entry in.

## 5. Bias (`quant/bias_calculator.py`)

`BiasCalculator` blends slope, momentum, and order-flow into a combined bias score and
strength. Bias can shift the position's asymmetry (placing more bins above or below the
active bin) when the bot is configured to use bias positioning. Treat it as a tilt on
top of the width decision, not a primary signal.

---

## Putting it together — the selection logic

`farm_bot.select_strategy()` roughly does:
1. Fetch candles → Keltner width + confidence, MTF regime, daily ATR, bias.
2. If Keltner width is below threshold with decent confidence and regime is ranging →
   **narrow** (default fallback is also narrow when signals are weak/unavailable).
3. If width is at/above threshold (volatile) and regime supports it → **wide**, with
   bin count from `calculate_wide_range_params`.
4. Apply gates: overbought-in-uptrend can **hold**; ATR floor enforces minimum width;
   low confidence biases toward the safe narrow default.
5. Emit a `StrategyIntent` (`enter` narrow/wide, `hold`, `exit_and_reenter`, `top_up`).

You can observe the whole decision without trading:
```bash
moe-farm --once --json --strategy auto    # watch what it picks and why (dry-run)
```
The logs print the indicator values: `keltner_width`, `regime`, `keltner_conf`,
`daily_atr`, and the chosen bin count, e.g.
`Wide bin sizing: keltner_width=10.0% atr=0.0% … → 200 bins` and
`StrategyEngine: narrow — default (regime=RANGING keltner_conf=0.00)`.

## Interpreting the indicators quickly

| Reading | Implication |
|--------|-------------|
| Keltner width small + high confidence + RANGING | Narrow position, dense fees |
| Keltner width large / ATR high | Wide position (40–200 bins) to stay in range |
| Low Keltner confidence | Distrust the signal → default narrow |
| Overbought + TRENDING_UP | Likely hold (don't enter at the top) |
| Oversold | May scale entry in |
| Candles unavailable (Bybit down) | Degraded: RANGING/conf 0 → conservative default |

For the theory background and tunables (thresholds, multipliers, capital split, bin
count) see `docs/strategy-guide.md`, `docs/moe-lp-mechanics.md`, and
`docs/configuration.md` in the repo.
