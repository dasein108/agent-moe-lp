# CLI Reference

The bot ships four console scripts: `moe`, `moe-readonly`, `moe-portfolio`, `moe-farm`.

## moe -- Main CLI

Wallet management, snapshots, token operations, LP management.

```
moe [--wallet-file PATH] [--debug] [--pool ADDRESS] COMMAND [OPTIONS]
```

### wallet create

Create a new wallet file with a fresh private key.

```bash
moe wallet create                  # Creates wallet.json
moe wallet create --out my.json    # Custom path
moe wallet create --force          # Overwrite existing
moe wallet create --json           # JSON output
```

### wallet show

Display wallet metadata.

```bash
moe wallet show
moe wallet show --json
```

### snapshot

Capture pool state, wallet balances, LP position.

```bash
moe snapshot                              # Basic snapshot
moe snapshot --with-lp-inventory          # Include LP token estimates
moe snapshot --deep-position              # Fall back to log scanning
moe snapshot --wallet 0xABC...            # Specific wallet
moe snapshot --json                       # Full JSON output
moe snapshot --save my_snapshot.json      # Save to data/my_snapshot.json
```

### wrap / unwrap

Convert between native MNT and WMNT (ERC-20).

```bash
moe wrap --amount-mnt 100 --dry-run       # Preview wrapping 100 MNT
moe wrap --amount-mnt 100                  # Execute
moe unwrap --amount-wmnt 50 --dry-run      # Preview unwrapping 50 WMNT
```

### swap

Swap between WMNT and USDT via the Merchant Moe router.

```bash
moe swap --from-token wmnt --amount 100 --dry-run     # Sell 100 WMNT for USDT
moe swap --from-token usdt --amount 5 --dry-run       # Buy WMNT with 5 USDT
moe swap --from-token wmnt --amount 50 --slippage-bps 200   # 2% slippage
```

### lp add

Create an LP position around the current active bin.

```bash
# Dry-run (default)
moe lp add --amount-wmnt 500 --amount-usdt 10

# With custom bin count
moe lp add --amount-wmnt 500 --amount-usdt 10 --bin-count 20

# Live execution with auto-rebalance
moe lp add --amount-wmnt 500 --amount-usdt 10 --wrap-mnt --auto-rebalance

# JSON output
moe lp add --amount-wmnt 500 --amount-usdt 10 --dry-run --json
```

Options:
- `--amount-wmnt DECIMAL` -- WMNT/MNT-side amount (required)
- `--amount-usdt DECIMAL` -- USDT amount (required)
- `--bin-count INT` -- Number of bins (default: `BIN_COUNT` env var)
- `--wrap-mnt` -- Wrap native MNT to WMNT before adding
- `--auto-rebalance` -- Rebalance portfolio to 50/50 before adding
- `--slippage-bps INT` -- Override slippage tolerance
- `--dry-run` -- Preview only (default)

### lp remove

Remove the current LP position (all bins).

```bash
moe lp remove --dry-run            # Preview removal
moe lp remove                      # Execute removal
moe lp remove --max-bins-per-tx 30 # Smaller removal batches
```

Options:
- `--slippage-bps INT` -- Override slippage tolerance
- `--max-bins-per-tx INT` -- Bins per removal transaction (default: 50)
- `--dry-run` -- Preview only (default)

---

## moe-farm -- Automated Farming

Main automated farming loop. Runs `execute_cycle()` repeatedly.

```bash
# Single dry-run cycle
moe-farm --once --json

# Single live cycle
moe-farm --once --live

# Continuous farming (live, 60s intervals)
moe-farm --live --poll-interval-seconds 60

# Continuous dry-run (default 300s intervals)
moe-farm

# Manage an arbitrary WMNT-paired pool for one run
moe-farm --once --pool 0x<LB pair>
```

Options:
- `--strategy MODE` -- Override: `narrow`, `wide`, `auto` (default: `auto`)
- `--pool ADDRESS` -- Override `POOL_ADDRESS` for this run (tokens auto-discovered on-chain)
- `--once` -- Run one cycle and exit
- `--live` -- Execute real transactions (default is dry-run)
- `--dry-run` -- Preview only (default)
- `--poll-interval-seconds INT` -- Seconds between cycles (default: 300)
- `--json` -- JSON output

---

## moe-readonly -- Read-Only Snapshot

Lightweight snapshot tool; no private key required.

```bash
moe-readonly --wallet 0xABC...
moe-readonly --wallet 0xABC... --json
moe-readonly --wallet 0xABC... --save custom.json
```

---

## moe-portfolio -- Portfolio Review

Display portfolio summary: wallet balances + LP positions.

```bash
moe-portfolio
```
