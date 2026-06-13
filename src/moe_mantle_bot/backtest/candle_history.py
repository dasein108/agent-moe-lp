"""Historical candle fetching (paginated Bybit) + a replay fetcher.

``fetch_history`` pages through the Bybit kline endpoint to assemble months of
candles (the live ``CandleFetcher`` only pulls the most recent ~200) and caches
them to ``data/candles/``. ``ReplayCandleFetcher`` is a drop-in for
``CandleFetcher`` that returns the historical slice ending at a movable cursor,
so the live ``KeltnerAnalyzer`` / ``MTFAnalyzer`` run unchanged over history.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from pybit.unified_trading import HTTP
except ImportError:  # pragma: no cover
    HTTP = None

logger = logging.getLogger(__name__)

# Human interval -> (bybit code, minutes per candle)
_INTERVAL = {
    "1m": ("1", 1), "5m": ("5", 5), "15m": ("15", 15),
    "1h": ("60", 60), "4h": ("240", 240), "1d": ("D", 1440),
}
_BYBIT_TO_HUMAN = {code: human for human, (code, _) in _INTERVAL.items()}


def normalize_interval(interval: str) -> str:
    """Accept '5m' or '5' and return the human form ('5m')."""
    if interval in _INTERVAL:
        return interval
    return _BYBIT_TO_HUMAN.get(interval, interval)


def interval_minutes(interval: str) -> int:
    return _INTERVAL[normalize_interval(interval)][1]


def _preprocess(df: pd.DataFrame) -> pd.DataFrame:
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col])
    df["timestamp"] = pd.to_datetime(df["start_time"].astype("int64"), unit="ms", utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["returns"] = df["close"].pct_change()
    df["log_returns"] = np.log(df["close"] / df["close"].shift(1))
    df["range_ratio"] = (df["high"] - df["low"]) / df["close"]
    df["body_ratio"] = (df["close"] - df["open"]).abs() / (df["high"] - df["low"] + 1e-9)
    return df.dropna().reset_index(drop=True)


def fetch_history(
    symbol: str,
    interval: str,
    days: int,
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch ``days`` of candles for ``symbol``/``interval``, paginating Bybit.

    Cached to ``cache_dir`` (default ``data/candles/``). Cache is reused when it
    already covers the requested window and is < 1 day stale.
    """
    human = normalize_interval(interval)
    cache_dir = cache_dir or Path("data/candles")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{symbol}_{human}.csv"

    if cache.exists() and not refresh:
        df = pd.read_csv(cache, parse_dates=["timestamp"])
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            span_days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400
            fresh = (pd.Timestamp.utcnow() - df["timestamp"].iloc[-1]).total_seconds() < 86400
            if span_days >= days * 0.95 and fresh:
                logger.info("Using cached %s %s candles (%.0fd)", symbol, human, span_days)
                return df

    if HTTP is None:
        raise ImportError("pybit not installed. Install with: pip install pybit")

    code, minutes = _INTERVAL[human]
    needed = int(days * 1440 / minutes) + 300  # warmup buffer
    session = HTTP(testnet=False)
    rows: list[list] = []
    end_ms: int | None = None
    seen: set[int] = set()

    while len(rows) < needed:
        params = dict(category="linear", symbol=symbol, interval=code, limit=1000)
        if end_ms is not None:
            params["end"] = end_ms
        resp = session.get_kline(**params)
        if resp.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {resp.get('retMsg')}")
        batch = resp["result"]["list"]
        if not batch:
            break
        new = [r for r in batch if int(r[0]) not in seen]
        if not new:
            break
        for r in new:
            seen.add(int(r[0]))
        rows.extend(new)
        end_ms = min(int(r[0]) for r in batch) - 1  # page backwards
        logger.info("Fetched %d %s %s candles (target %d)", len(rows), symbol, human, needed)

    df = pd.DataFrame(rows, columns=["start_time", "open", "high", "low", "close", "volume", "turnover"])
    df = _preprocess(df)
    df.to_csv(cache, index=False)
    logger.info("Cached %d %s %s candles to %s", len(df), symbol, human, cache)
    return df


class ReplayCandleFetcher:
    """Drop-in for ``CandleFetcher`` that serves history up to a cursor time.

    Set the cursor with ``set_cursor(ts)``; ``get_candles`` then returns the
    last ``limit`` candles at or before the cursor — so analyzers see only the
    past, never the future.
    """

    def __init__(self, histories: dict[str, pd.DataFrame]):
        # keys normalized to human interval form
        self._hist = {normalize_interval(k): v for k, v in histories.items()}
        self._cursor: pd.Timestamp | None = None

    def set_cursor(self, ts: pd.Timestamp) -> None:
        self._cursor = ts

    def get_candles(self, symbol: str = "MNTUSDT", interval: str = "5", limit: int = 100) -> pd.DataFrame:
        human = normalize_interval(interval)
        df = self._hist.get(human)
        if df is None or df.empty:
            raise RuntimeError(f"No replay history for interval {interval}")
        if self._cursor is not None:
            df = df[df["timestamp"] <= self._cursor]
        return df.tail(limit).reset_index(drop=True).copy()
