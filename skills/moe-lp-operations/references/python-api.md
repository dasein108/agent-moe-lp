# Programmatic API (when the CLI isn't enough)

When a CLI flag doesn't expose what you need — custom analysis, scripting, reading
exact data-model fields — drive the bot's services directly. All cross-module data is
**frozen dataclasses** from `models.py`; access fields by attribute (e.g.
`pool_state.active_bin_id`), and serialize with `.to_dict()` at JSON boundaries. Never
use dict-key access on these models.

## Service wiring (composition pattern)

```python
from moe_mantle_bot.config import Settings
from moe_mantle_bot.rpc_client import RpcClient
from moe_mantle_bot.tx_sender import TxSender
from moe_mantle_bot.balance_manager import BalanceManager
from moe_mantle_bot.lp_service import LPService
from moe_mantle_bot.core.wallet import load_wallet

settings = Settings.from_env()
rpc = RpcClient(settings)

# READ-ONLY (snapshots, analysis — no wallet/tx needed):
lp_ro = LPService.read_only(rpc, settings)

# FULL (mutations — needs wallet + tx sender):
wallet = load_wallet(settings)
tx = TxSender(rpc, wallet, settings)
balance = BalanceManager(rpc, tx, settings)
lp = LPService(rpc, tx, balance, settings)
```
Always use these composable services.

## Pool & position reads (`LPService`)

```python
ps = lp_ro.get_pool_state()
# ps.active_bin_id, ps.bin_step, ps.mnt_price_usdt, token X/Y, reserves

addr = wallet.address            # or any 0x address for read-only
lp_ro.has_active_position(addr)              # bool
lp_ro.get_position_range(addr)               # (min_bin, max_bin)
lp_ro.is_in_range(addr)                      # bool — core health check
lp_ro.get_all_active_bins(addr)              # [bin_id, ...]
lp_ro.get_position(addr, ...)                # PositionState w/ per-bin inventory
lp_ro.get_bin_balances(addr, bin_ids)        # raw LBToken balances
lp_ro.discover_onchain_bins(addr)            # bins from chain (vs registry)
lp_ro.reconcile(addr, dry_run=True)          # registry vs on-chain diff
```

## Mutations (`LPService` + `BalanceManager`) — always preview with dry_run=True first

```python
prev = lp.create_position(..., dry_run=True)     # inspect distribution/amounts
res  = lp.create_position(..., dry_run=False)    # execute
lp.estimate_position_fill(...)                   # expected fill before adding
lp.remove_position(dry_run=True)                 # preview; live aborts if preview reverts
lp.validate_position_size(...)

balance.get_wallet_balances(addr, mnt_price)     # WalletBalances (native+WMNT+USDT)
balance.get_capital_budget(...)                  # spendable, with budget cap + gas reserve
balance.plan_rebalance(...); balance.execute_rebalance(...)
balance.rebalance_if_needed(addr, tolerance_bps=200, dry_run=True)
balance.quote_swap(token_in, token_out, amount_in)  # SwapQuote
balance.wrap_mnt(amt, dry_run=True); balance.unwrap_wmnt(amt, dry_run=True)
balance.ensure_mnt_min_balance(...)              # auto-replenish native MNT for gas
```

## Market analysis (`quant/` analyzers)

```python
from moe_mantle_bot.quant.keltner_analyzer import KeltnerAnalyzer
from moe_mantle_bot.quant.mtf_analyzer import MTFAnalyzer
from moe_mantle_bot.quant.bias_calculator import BiasCalculator
from moe_mantle_bot.quant.candle_fetcher import CandleFetcher
from moe_mantle_bot.quant.wide_range_lp_manager import WideRangeLPManager

candles = CandleFetcher().get_candles(symbol="MNTUSDT", interval="5m", limit=200)

kelt = KeltnerAnalyzer()
channel = kelt.analyze_channel_conditions(symbol="MNTUSDT")   # width %, quality, bounds
kelt.get_optimal_lp_range(...)                                # suggested bin range

mtf = MTFAnalyzer().analyze("MNTUSDT")   # MTFAnalysis: regime, confidence, bias,
                                         # overbought/oversold, daily_atr_pct
                                         # → mtf.to_dict()

bias = BiasCalculator().get_combined_bias(...)   # slope+momentum+orderflow blend

wide = WideRangeLPManager(settings, lp_service=lp_ro)
params = wide.calculate_wide_range_params(keltner_analysis=channel.to_dict(),
                                          daily_atr_pct=mtf.daily_atr_pct,
                                          pool_stats=None)   # → bin count for wide
```
Candles come from Bybit; handle `ConnectionError`/`Timeout` (the live bot degrades to
RANGING/neutral when the feed is down rather than failing the cycle).

## Snapshots & analytics

```python
from moe_mantle_bot.snapshot import SnapshotService
snap = SnapshotService(settings, balance=balance, lp=lp)   # or read-only services
data = snap.capture(...)                                   # full wallet+position+pool
# data.to_dict() → matches `data/latest_snapshot.json`

# Flow-adjusted P&L, daily rollups, re-entry telemetry live in data/analytics.db
# via the `analytics` module (see analytics.py for the query surface).
```

## Exceptions to catch (never bare `except`)

- RPC: `Web3RPCError`, `ContractLogicError`, `ConnectionError`, `TimeoutError`
- Tx: `TransactionExecutionError` (distinguishes retryable vs fatal),
  `PreviewValidationError` (raised when a live removal preview reverts — do not push
  the live tx)
- API/candles: `RequestsConnectionError`, `HTTPError`, `Timeout`

If live data is unavailable, raise/stop — do not substitute fabricated prices or bin
ids.
