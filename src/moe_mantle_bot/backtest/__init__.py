"""LP backtesting harness for the Merchant Moe (Mantle) farming bot.

Emulates a Liquidity Book position over historical Bybit MNTUSDT candles to
estimate fee yield, impermanent loss, in-range time, and net PnL — both for a
static (buy-and-hold-LP) position and for the live ``StrategyEngine`` replayed
over history.

USD stablecoins (USDT, USDT0, USD0, USDC) are mapped 1:1 to the MNTUSDT feed.
The fee model is volume-capture: pool swap volume is approximated from Bybit
turnover, since on-chain historical volume is not available. See
``fee_model.py`` for the exact formula and assumptions.
"""

from .config import BacktestConfig
from .simulator import BacktestResult, run_backtest

__all__ = ["BacktestConfig", "BacktestResult", "run_backtest"]
