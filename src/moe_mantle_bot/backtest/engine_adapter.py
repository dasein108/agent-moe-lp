"""Build StrategyEngine inputs from historical candles (reuses live analyzers)."""

from __future__ import annotations

import logging

from ..quant.keltner_analyzer import KeltnerAnalyzer
from ..quant.mtf_analyzer import MTFAnalyzer
from ..strategies.engine import MarketState
from .candle_history import ReplayCandleFetcher
from .config import BacktestConfig

logger = logging.getLogger(__name__)


class HistoricalMarket:
    """Computes a MarketState + optimal bin count at the fetcher's cursor.

    Wraps the live KeltnerAnalyzer and MTFAnalyzer so the backtest sees exactly
    what the bot would see at that point in time. On data/analyzer failure it
    returns a neutral MarketState (UNKNOWN regime) — the engine then falls back
    to its conservative defaults, matching live graceful degradation.
    """

    def __init__(self, fetcher: ReplayCandleFetcher, cfg: BacktestConfig):
        self.fetcher = fetcher
        self.cfg = cfg
        self.keltner = KeltnerAnalyzer(candle_fetcher=fetcher)
        self.mtf = MTFAnalyzer(fetcher)

    def market_state(self) -> tuple[MarketState, int | None]:
        keltner_dict = None
        optimal_bins: int | None = None
        try:
            analysis = self.keltner.analyze_channel_conditions(symbol=self.cfg.symbol)
            keltner_dict = analysis.to_dict()
            optimal = self.keltner.get_optimal_lp_range(analysis)
            width_pct = optimal.get("range_width_pct")
            if width_pct:
                bin_pct = self.cfg.bin_step / 100.0  # binStep 100 = 1% per bin
                optimal_bins = max(3, min(self.cfg.wide_bin_count, round(width_pct / bin_pct)))
        except (ValueError, RuntimeError, KeyError, ZeroDivisionError) as e:
            logger.debug("Keltner unavailable at cursor: %s", e)

        mtf_analysis = None
        try:
            mtf_analysis = self.mtf.analyze(symbol=self.cfg.symbol)
        except (ValueError, RuntimeError, KeyError) as e:
            logger.debug("MTF unavailable at cursor: %s", e)

        return MarketState.from_keltner_and_mtf(keltner_dict, mtf_analysis), optimal_bins
