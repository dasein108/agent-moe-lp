# Merchant Moe Mantle Bot

Automated liquidity farming bot for the Merchant Moe `WMNT/USDT` Liquidity Book pool on the Mantle blockchain. Earns LB swap fees from a single managed position with automatic rebalancing and re-entry.

## Features

- **Read-only monitoring**: Snapshot tool for pool and wallet state analysis
- **Full execution engine**: CLI for wallet management, wrapping, swapping, LP operations
- **Automated farming loop**: Single-position LP rotation with re-entry policy, fill guards, and auto-recovery
- **Auto strategy selection**: narrow vs. wide range chosen from Keltner channel width + ATR floor
- **Telegram notifications**: emitted by the farm loop (`moe-farm`) and portfolio tool (`moe-portfolio`) — see [Telegram Notifications](#telegram-notifications) for the scope caveat
- **Analytics**: SQLite-backed operation history, snapshots, and re-entry telemetry
- **LLM-agent operable**: a runtime-agnostic skill (`skills/`) lets coding agents drive the bot safely — see [LLM Agent Integration](#llm-agent-integration)

Fee-farming only: the bot collects LB swap fees. There is no MOE-emission / MasterChef staking.

> **Yield note.** All backtested figures are **swap fees only**. On pools with active **MOE-emission / MasterChef reward incentives**, total yield (swap fees **+ farm rewards**) can be roughly **3–5× the fee-only numbers** shown here. Reward capture is future work. See [`BACKTESTING_RESULTS.md`](BACKTESTING_RESULTS.md).

> **No LLM at runtime.** The bot's decisions are fully deterministic — Keltner channel width, ATR, multi-timeframe RSI/EMA regime — not model inference. The "AI" layer is the **agent-operability** surface in `skills/`: it lets an LLM coding agent operate and analyze the bot. The trading engine never calls an LLM.

> **Backtesting.** A historical-replay harness (`moe-backtest`) emulates an LB position over Bybit MNTUSDT candles to estimate fee yield, impermanent loss, in-range time, and net PnL — replaying the **live `StrategyEngine`** against static and hold baselines. Fee magnitude is an explicit, calibratable assumption (on-chain historical volume isn't available); IL and in-range % are exact. Full results and findings: **[`BACKTESTING_RESULTS.md`](BACKTESTING_RESULTS.md)**. See also [Backtesting](#backtesting).

## Architecture

The bot is single-position and service-oriented:

- **Cycle preparation**: state loading, normalization, snapshots, and market indicator logging
- **Cycle planning**: strategy selection and normalized `StrategyIntent` creation
- **Execution core**: single-position entry and top-up execution with safety checks
- **Re-entry policy**: signal stack, calibration, adaptive ratio, and pre-entry rebalance rules
- **Re-entry coordination**: exit-only / re-enter result shaping and analytics closeout
- **Keltner analysis**: technical analysis (Bybit MNTUSDT candles) for narrow/wide selection

## LLM Agent Integration

The bot ships with a **runtime-agnostic operating skill** so an LLM coding agent (Claude Code, Codex, Cursor, a CI job) can operate and analyze it safely without re-discovering the codebase each time. This is the project's "AI harness" — orchestration around the bot, not inference inside it.

```
skills/moe-lp-operations/
  SKILL.md                       canonical guide: execution-default safety table,
                                 env setup, grounding rules, task -> reference index
  references/
    wallet.md                    create / select / inspect wallets; key handling
    lp-operations.md             add / remove / preview / rebalance; pool & position analysis
    market-analysis.md           Keltner width, ATR, MTF regime -> narrow-vs-wide
    python-api.md                drive LPService / BalanceManager / quant/ directly
  evals/evals.json               prompt-based eval cases that pin expected agent behavior
```

A thin pointer at `.claude/skills/moe-lp-operations/SKILL.md` makes the same skill discoverable to Claude Code; the canonical copy under `skills/` is the single source of truth shared by every agent runtime.

Why it matters for safe automation:

- **Encoded safety defaults** — the skill states the opposite execution defaults up front: `moe lp/swap/wrap/unwrap` are **LIVE unless `--dry-run`**, while `moe-farm` is **dry-run unless `--live`**. Agents preview before executing.
- **Grounding rules** — never invent prices or bin ids; read them live or stop. Stale-`.env` and wrong-pool checks are called out (e.g. an empty pool can't source a swap).
- **Deterministic, auditable** — the agent runs the same CLI/`LPService` calls a human would; nothing about the trading decision is delegated to a model.

See [`AGENTS.md`](AGENTS.md) for repository contribution guidelines that every agent (and human) follows.

## Documentation

- [`BACKTESTING_RESULTS.md`](BACKTESTING_RESULTS.md) - Backtest results: hold vs static vs strategy, width optimization, hyperopt
- [`docs/README.md`](docs/README.md) - Architecture overview and quick start
- [`docs/strategy-guide.md`](docs/strategy-guide.md) - Strategy selection, re-entry policy, Keltner analysis
- [`docs/configuration.md`](docs/configuration.md) - All environment variables and settings
- [`docs/cli-reference.md`](docs/cli-reference.md) - CLI commands with examples
- [`docs/technical-reference.md`](docs/technical-reference.md) - Module map, data models, LP math
- [`docs/moe-lp-mechanics.md`](docs/moe-lp-mechanics.md) - Bin composition, distribution math, WrongAmounts fixes
- [`docs/deployment.md`](docs/deployment.md) - Docker, systemd, monitoring, Telegram

Current read capabilities:

- Mantle chain and RPC state
- Merchant Moe WMNT/USDT pool metadata
- active bin, bin step, reserves, and fee parameters
- wallet balances for MNT, WMNT, and USDT
- current LP exposure for a wallet by:
  - first probing live `LBToken` balances around the active bin
  - then falling back to `TransferBatch` log scans if needed
  - reading current `balanceOfBatch`
  - estimating underlying token amounts from per-bin reserves and LB token share

Current execution capabilities:

- create a new `wallet.json`
- load a persisted wallet file automatically
- wrap native MNT into WMNT
- unwrap WMNT into native MNT
- swap `WMNT <-> USDT`
- add a `WMNT/USDT` LB position around the current active bin
- remove the current `WMNT/USDT` LB position

## Constants

Defaults are set from the Merchant Moe deployment and the target pool (`src/moe_mantle_bot/constants.py`):

- Mantle mainnet chain id: `5000`
- Mantle public RPC: `https://rpc.mantle.xyz`
- Merchant Moe LBFactory: `0xa6630671775c4EA2743840F9A5016dCf2A104054`
- Merchant Moe LBRouter: `0x013e138EF6008ae5FDFDE29700e3f2Bc61d21E3a`
- Merchant Moe LBQuoter: `0x501b8AFd35df20f531fF45F6f695793AC3316c85`
- default WMNT/USDT LB V2.2 pool (binStep 15): `0xf6c9020c9e915808481757779edb53daceae2415`
- WMNT: `0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8`
- USDT: `0x201EBa5CC46D216Ce6DC03F6a759e8E766e956aE` (6 decimals)
- target `BIN_COUNT`: `10`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

## Docker Deployment

### Quick Start

```bash
# Build the image
docker compose build

# Test configuration (dry run)
docker compose run --rm moe-farm-bot

# Check wallet status
docker compose run --rm moe-farm-bot python3 -m moe_mantle_bot.cli
```

### Production Deployment

1. **Configure environment** - Update `.env` with your settings
2. **Enable live trading** - Edit `docker-compose.yml` and uncomment the production command
3. **Deploy** - Start the farming bot

```bash
docker compose up -d        # continuous farming (after uncommenting production command)
docker compose logs -f      # monitor logs
docker compose ps           # check status
```

**Important**: The default command runs a single dry-run for safety. To enable live trading, edit `docker-compose.yml` and uncomment the production command section. See [`docs/deployment.md`](docs/deployment.md) for the full deployment guide.

### Configuration

Key environment variables (see [`docs/configuration.md`](docs/configuration.md) for the full list):

- `MANTLE_RPC_URL`: Mantle RPC endpoint
- `POOL_ADDRESS`: WMNT/USDT LB pool to manage
- `TELEGRAM_NOTIFICATIONS_ENABLED`: Set to `true` to enable real-time alerts
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHANNEL_ID`: Telegram bot token and target chat
- `NOTIFICATIONS_ENABLED`: General notification toggle (defaults to `true`)

By default, wallet LP scans use active-bin probing first because the public RPC limits `eth_getLogs` to 100-block ranges. If log scanning is still needed, the tool auto-shrinks requests and falls back to scanning recent blocks. Override the scan start explicitly with `LOG_SCAN_START_BLOCK` in `.env`.

### LP Distribution Shape Configuration

The bot supports different liquidity distribution shapes across bins:

| Parameter | Description | Values |
|-----------|-------------|--------|
| `DISTRIBUTION_SHAPE` | How liquidity is distributed across bins | `uniform`, `slope`, `curve`, `custom` |
| `SLOPE_DIRECTION` | Direction for slope distribution | `ascending`, `descending`, `peak`, `valley` |
| `SLOPE_STEEPNESS` | Gradient multiplier for slope (0.1-5.0) | `1.0` |
| `CURVE_TYPE` | Type for curve distribution | `exponential`, `logarithmic`, `bell`, `u_curve` |
| `CURVE_EXPONENT` | Steepness for curve (0.1-10.0) | `2.0` |
| `CUSTOM_DISTRIBUTION` | Comma-separated custom weights | Optional |

- **uniform**: Equal liquidity across all bins (default)
- **slope**: Linear gradient — `ascending`/`descending`/`peak`/`valley`
- **curve**: Non-linear — `exponential`/`logarithmic`/`bell`/`u_curve`
- **custom**: User-defined weights via `CUSTOM_DISTRIBUTION`

### Portfolio Ratio Configuration

| Parameter | Description | Default |
|-----------|-------------|---------|
| `TARGET_MNT_RATIO_BPS` | Target MNT allocation (1000=10%, 5000=50%, 9000=90%) | `5000` (50%) |

Controls the target portfolio ratio between MNT and USDT for rebalancing. The default maintains a 50/50 balance.

## Telegram Notifications

Telegram alerts are emitted by the **automated farm loop (`moe-farm`)** and the **`moe-portfolio`** tool: LP created/removed, swaps, rebalancing, farm cycle completion, and error alerts.

> **Scope caveat:** the manual `moe` CLI (`moe lp add`, `moe swap`, `moe wrap`, …) does **not** send Telegram notifications — only `moe-farm` and `moe-portfolio` do. If you mint or remove a position by hand with `moe lp …`, no alert is sent even with `TELEGRAM_NOTIFICATIONS_ENABLED=true`. Run via `moe-farm` (or check with `moe-portfolio`) if you want notifications.

Example alert (farm loop):
```
LP POSITION CREATED
Added: 20.0 WMNT + 40.0 USDT
Bin Range: 8388603-8388613
Bins: 11 active
TX: 1234567890abcdef...
```

Set `TELEGRAM_NOTIFICATIONS_ENABLED=true` and provide `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHANNEL_ID` in your `.env` to activate.

## CLI Tools

The bot ships five console scripts:

- `moe` — main execution CLI (wallet, wrap/unwrap, swap, `lp add`/`lp remove`, snapshot, balance)
- `moe-readonly` — read-only snapshot tool
- `moe-portfolio` — portfolio review tool
- `moe-farm` — automated farming loop
- `moe-backtest` — historical LP backtester (see [Backtesting](#backtesting))

General flags for `moe`:

- `--wallet-file <path>`: use a wallet file other than `wallet.json`
- `--debug`: print internal debug logs to stderr
- `--pool <address>`: operate on a different WMNT-paired LB pair (overrides `POOL_ADDRESS`)

Execution rule: `moe` subcommands execute live unless `--dry-run` is set; LP operations preview with `--dry-run`.

### Wallet

```bash
moe wallet create
moe wallet create --out my-wallet.json
moe wallet show
moe wallet show --json
moe --wallet-file my-wallet.json wallet show
```

### Snapshots

```bash
moe-readonly                        # pool-only snapshot
moe-readonly --wallet 0xYourWallet  # wallet + LP snapshot
moe snapshot                        # snapshot using wallet.json
moe snapshot --with-lp-inventory    # detailed LP underlying inventory (slower)
moe snapshot --deep-position        # force slower historical LP search
moe-readonly --wallet 0xYourWallet --json
moe-readonly --wallet 0xYourWallet --save wallet_snapshot.json
```

The default output file is `data/latest_snapshot.json`. A snapshot shows current pool state, wallet MNT/WMNT/USDT balances, whether LP exists, the LP bin range and active bin count, and optional estimated underlying LP inventory.

### Wrap / Unwrap

```bash
moe wrap --amount-mnt 1               # wrap native MNT into WMNT
moe wrap --amount-mnt 1 --dry-run --json
moe unwrap --amount-wmnt 1            # unwrap WMNT into native MNT
moe unwrap --amount-wmnt 1 --dry-run --json
```

### Swaps

```bash
moe swap --from-token wmnt --amount 10              # WMNT -> USDT
moe swap --from-token usdt --amount 1               # USDT -> WMNT
moe swap --from-token wmnt --amount 10 --dry-run --json
moe swap --from-token wmnt --amount 10 --slippage-bps 50
```

`--from-token` accepts `wmnt` or `usdt`. There is no direct native `MNT -> USDT` command because Merchant Moe LB uses `WMNT`; wrap first, then swap.

### LP Add

`--amount-wmnt` describes the MNT-side size; the bot sends native `MNT` to the router via the native-liquidity path. If the wallet holds `WMNT` instead of enough native `MNT`, pass `--wrap-mnt` to let the bot auto-convert before LP entry.

LP add flags: `--amount-wmnt`, `--amount-usdt`, `--bin-count`, `--wrap-mnt`, `--auto-rebalance`, `--slippage-bps`, `--dry-run`, `--json`.

```bash
# Preview
moe lp add --amount-wmnt 10 --amount-usdt 1 --bin-count 10 --wrap-mnt --dry-run --json
# Execute
moe lp add --amount-wmnt 10 --amount-usdt 1 --bin-count 10 --wrap-mnt
# Narrower 3-bin position
moe lp add --amount-wmnt 23 --amount-usdt 0.5 --bin-count 3 --wrap-mnt
```

Pass `--auto-rebalance` to let the bot rebalance the portfolio automatically if there is insufficient native MNT for LP creation.

### LP Remove

```bash
moe lp remove                          # remove current LP
moe lp remove --dry-run --json         # preview
moe lp remove --max-bins-per-tx 20     # chunk large positions
moe snapshot --with-lp-inventory       # verify position is gone
```

LP remove flags: `--slippage-bps`, `--max-bins-per-tx`, `--dry-run`, `--json`.

### Automated Farming Loop

`moe-farm` runs the single-position farming loop.

**Cycle Preparation:** monitor LP status, normalize residual/dust state, record snapshots and market indicators, build a normalized cycle context.

**Cycle Planning:** select `narrow`/`wide`/`hold`/`top_up`/`exit_and_reenter`, build one `StrategyIntent`, apply the re-entry policy stack when rotating inventory.

**Execution Core:** run shared LP safety checks, gate sub-min fills, apply wide inventory rebalance guards, execute one single-position path.

Important behavior:

- **Safe by default**: dry-run unless `--live`
- **Robust error handling**: continues despite transaction failures with exponential backoff
- **Telegram alerts**: real-time notifications for operations and cycle completions
- **Logging**: cycle logs plus SQLite analytics for snapshots and operations
- **Gas management**: automatically reserves native MNT for fees
- **Native MNT routing**: uses the router's native-liquidity path

Default settings:
- LP width: `10` bins around active bin (`--bin-count`)
- Strategy: `auto` (Keltner-based narrow/wide; override with `--strategy narrow|wide|auto`)
- Budget cap: 80% of wallet balance (`MAX_BUDGET_PCT`)
- Gas reserve: `GAS_RESERVE_MNT` (default 2 MNT — Mantle L2 gas is cheap)
- Native MNT min balance guard: auto-replenishes native MNT via WMNT unwrap or USDT swap when below `MNT_MIN_BALANCE`
- Runtime log: `data/farm_bot.log`
- Analytics DB: `data/analytics.db`

```bash
moe-farm --once --json                          # single dry-run cycle
moe-farm --live --poll-interval-seconds 300     # continuous live monitoring
moe-farm --once --pool 0x<LB pair>              # manage an arbitrary WMNT-paired pool
```

Farm bot parameters:

- `--strategy <mode>`: `narrow`/`wide`/`auto` (default `auto`)
- `--pool <address>`: override `POOL_ADDRESS` for one run
- `--once`: run a single cycle and exit
- `--dry-run`: dry-run mode (default)
- `--live`: enable live trading (overrides dry-run)
- `--poll-interval-seconds <seconds>`: sleep interval for continuous mode
- `--json`: output detailed JSON results

## Backtesting

`moe-backtest` emulates a Liquidity Book position over historical **Bybit MNTUSDT** candles to estimate fee yield, impermanent loss, in-range time, rebalances, and net PnL. It runs two positions side by side: a **static** buy-and-hold-LP baseline and a **strategy** position that replays the live `StrategyEngine` (Keltner + ATR + MTF) — exiting, re-entering, and resizing exactly as the bot would. USD stablecoins (USDT, USDT0, USD0, USDC) are mapped 1:1 to the MNTUSDT feed.

```bash
# Backtest the current live position (seeds geometry/fees/size/depth from chain)
moe-backtest --seed-from-pool 0x<LBpair> --days 90

# Calibrate fee magnitude to the pool's real daily volume
moe-backtest --seed-from-pool 0x<LBpair> --days 90 --pool-daily-volume-usd 500

# A hypothetical position, JSON + save
moe-backtest --capital 200 --quote-usd 100 --bin-count 20 --bin-step 100 --days 60 --json --save data/backtests/test.json
```

The text report ends with the **initial vs final budget** for both static and strategy, and `--chart PATH` (auto-written next to `--save` unless `--no-chart`) renders a PNG: price with the static/strategy LP-range bands and re-center markers, plus the equity curves vs initial budget.

**Inventory and IL are exact** (LB conversion is deterministic from the price path). **Fees are an explicit assumption**: on-chain historical pool volume isn't available, so pool swap volume is approximated from an assumed daily volume (`--pool-daily-volume-usd`, default `1× pool TVL/day`) distributed across candles by Bybit turnover shape, then split by your share of active-bin liquidity. Always calibrate `--pool-daily-volume-usd` to the pool's observed volume before trusting the APR — IL and in-range % don't depend on it, but fee/APR figures do. Candle history is cached to `data/candles/`; results optionally to `data/backtests/`.

**Headline findings** (full detail + tables in **[`BACKTESTING_RESULTS.md`](BACKTESTING_RESULTS.md)**):

- **Position width is the dominant profit lever** — an inverted-U peaking at **~20 bins (±10% price)**, beating the current 10-bin by ~+1 pt mean net across a walk-forward. Width-in-bins is pool-specific (±10% ≈ 20 bins on binStep-100, ~133 on binStep-15).
- **Auto re-centering is ~net-neutral-to-negative** for MNT/USDT this period; a passive tolerance matches static and removes tail risk. Re-centering earns its keep mainly in sustained trends.
- Both LP baselines beat **HOLD** comfortably — fee income dominates.
- **Swap fees only**: pools with reward incentives can yield **~3–5× more** (fees + farm rewards).

Harness reference: [`skills/moe-lp-operations/references/backtesting.md`](skills/moe-lp-operations/references/backtesting.md).

## Notes

- LP balances are derived from live LB token balances and current bin reserves, so they are estimates of the wallet's current underlying inventory.
- `wallet.json` contains the private key in plain JSON. It is ignored by git and written with restrictive file permissions, but it is still sensitive material.
- `moe` execution commands are live unless `--dry-run` is used; LP operations preview with `--dry-run`.
- `moe snapshot` defaults to a fast position check. Use `--deep-position` for the slower historical fallback and `--with-lp-inventory` for estimated underlying LP token amounts.
- `moe lp add` centers the requested `--bin-count` around the current active bin of the configured WMNT/USDT pool.
- Merchant Moe LB uses wrapped MNT (`WMNT`) inside the pool, but the LP entry path uses the router's native-liquidity method; the bot handles that conversion internally.
- There may be multiple Merchant Moe `WMNT/USDT` pools (different bin steps). Override the default pool with `POOL_ADDRESS` to target a different one.
- The rebalancing target ratio is configurable via `TARGET_MNT_RATIO_BPS` (default 50%).

## Source References

- Mantle network information: https://docs.mantle.xyz/
- Merchant Moe: https://merchantmoe.com/
