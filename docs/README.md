# Merchant Moe Mantle Farming Bot

Automated WMNT/USDT liquidity provision on Merchant Moe (Liquidity Book) on Mantle mainnet.

Single-position farming:
- One live LP intent at a time
- Narrow or wide selected per cycle via Keltner (`--strategy auto`), or forced via `--strategy narrow|wide`
- Exit-and-reenter managed through re-entry policy stack
- Budget capped at 80% of wallet (`MAX_BUDGET_PCT`)
- Native MNT min balance guard auto-replenishes gas before each cycle
- Dust bin filter prevents contract reverts during LP removal
- Fee-farming only — no MOE-emission / MasterChef staking

## Architecture

```
Pure Logic (no blockchain)              Blockchain Operations
──────────────────────────              ─────────────────────
strategies/engine.py                    lp_service.py
  StrategyEngine                          create/remove position
  MarketState → StrategyDecision          dust bin filter, bin discovery
                                        balance_manager.py
quant/mtf_analyzer.py                     budget, wrap/swap, MNT guard
  5m + 1h + 4h regime detection         tx_sender.py
  RSI, ATR, overbought/oversold           build/sign/send transactions

notification_formatter.py               Orchestration
  Pure Telegram formatting              ───────────────
                                        farm_bot.py (thin orchestrator)
                                        orchestration/cycle_preparer.py
                                        orchestration/cycle_planner.py
                                        execution/executor.py
```

Service wiring:

```python
rpc = RpcClient(settings)
tx  = TxSender(rpc, wallet, settings)
bal = BalanceManager(rpc, tx, settings)
lp  = LPService(rpc, tx, bal, settings)
engine = StrategyEngine(settings)    # pure logic, no blockchain
mtf = MTFAnalyzer(candle_fetcher)    # reads Bybit (MNTUSDT), no chain
```

## Key Constraint

Merchant Moe LB uses per-bin fungible tokens. Two positions from the same wallet in the same pool merge on-chain. `data/lp_registry.json` is the source of truth for position ownership and partial removal.

## Quick Start

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env    # Edit with your RPC, wallet, Telegram keys

# Create wallet
moe wallet create

# Check state
moe snapshot --with-lp-inventory

# Dry-run a farming cycle
moe-farm --once --json

# Live farming
moe-farm --live --poll-interval-seconds 60
```

## Documentation

- [Configuration](configuration.md) -- Env vars, settings, distribution shapes
- [CLI Reference](cli-reference.md) -- Every command with examples
- [Strategy Guide](strategy-guide.md) -- Single-position strategy, re-entry policy, safety gates
- [Merchant Moe LP Mechanics](moe-lp-mechanics.md) -- Bin composition, distribution math, WrongAmounts fixes, Keltner tuning
- [Deployment](deployment.md) -- Docker, systemd, monitoring, Telegram alerts
- [Technical Reference](technical-reference.md) -- Modules, data models, LP math
