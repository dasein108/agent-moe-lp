"""
Candle data fetcher for MNT volatility analysis.

Fetches 1m/5m candles from Bybit for volatility regime detection and
optimal reward sniping window identification.
"""

import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Literal
import time
import logging
from datetime import datetime, timedelta, timezone

try:
    from pybit.unified_trading import HTTP
except ImportError:
    HTTP = None

logger = logging.getLogger(__name__)

class CandleFetcher:
    """Fetches and preprocesses candle data from Bybit for MNT analysis."""
    
    def __init__(self):
        """Initialize Bybit connection."""
        if HTTP is None:
            raise ImportError(
                "pybit not installed. Install with: pip install pybit"
            )
        
        self.session = HTTP(testnet=False)
        self.cache = {}
        self.cache_duration = 30  # seconds
        
    # Normalize common interval formats to Bybit API format
    _INTERVAL_MAP = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}

    def get_candles(
        self,
        symbol: str = "MNTUSDT",
        interval: str = "1",
        limit: int = 100
    ) -> pd.DataFrame:
        """
        Fetch candles from Bybit.

        Args:
            symbol: Trading pair symbol
            interval: Candle interval — Bybit format ("1","5") or common format ("1m","5m")
            limit: Number of candles to fetch (max 200)

        Returns:
            DataFrame with OHLCV data and timestamps
        """
        # Normalize interval (accept "5m" or "5")
        bybit_interval = self._INTERVAL_MAP.get(interval, interval)

        cache_key = f"{symbol}_{bybit_interval}_{limit}"
        now = time.time()

        # Check cache first
        if cache_key in self.cache:
            cached_data, cached_time = self.cache[cache_key]
            if now - cached_time < self.cache_duration:
                logger.debug(f"Using cached data for {cache_key}")
                return cached_data.copy()

        try:
            logger.info(f"Fetching {limit} {bybit_interval}m candles for {symbol}")

            # Fetch from Bybit
            response = self.session.get_kline(
                category="linear",
                symbol=symbol,
                interval=bybit_interval,
                limit=limit
            )

            if response["retCode"] != 0:
                raise Exception(f"Bybit API error: {response['retMsg']}")

            klines = response["result"]["list"]

            if not klines:
                raise Exception(f"No kline data returned for {symbol} interval={bybit_interval} (response: retCode={response['retCode']}, retMsg={response['retMsg']})")
                
            # Convert to DataFrame
            df = pd.DataFrame(klines, columns=[
                "start_time", "open", "high", "low", "close", "volume", "turnover"
            ])
            
            # Preprocess data
            df = self.preprocess_candles(df)
            
            # Cache result
            self.cache[cache_key] = (df, now)
            
            logger.info(f"Successfully fetched {len(df)} candles")
            return df
            
        except Exception as e:
            logger.error(f"Failed to fetch candles: {e}")
            raise
            
    def preprocess_candles(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert raw Bybit data to proper OHLCV format.
        
        Args:
            df: Raw DataFrame from Bybit
            
        Returns:
            Cleaned DataFrame with proper types and timestamps
        """
        # Convert numeric columns
        numeric_cols = ["open", "high", "low", "close", "volume", "turnover"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col])
            
        # Convert timestamp (Bybit returns milliseconds in UTC)
        df["timestamp"] = pd.to_datetime(df["start_time"].astype(int), unit="ms", utc=True)
        
        # Sort by timestamp (Bybit returns newest first)
        df = df.sort_values("timestamp").reset_index(drop=True)
        
        # Calculate additional fields for analysis
        df["returns"] = df["close"].pct_change()
        df["log_returns"] = np.log(df["close"] / df["close"].shift(1))
        df["range_ratio"] = (df["high"] - df["low"]) / df["close"]
        df["body_ratio"] = abs(df["close"] - df["open"]) / (df["high"] - df["low"] + 1e-9)
        
        # Remove first row with NaN values
        df = df.dropna().reset_index(drop=True)
        
        return df
        
    def get_recent_candles(
        self,
        symbol: str = "MNTUSDT",
        minutes: int = 60,
        interval: Literal["1", "5"] = "1"
    ) -> pd.DataFrame:
        """
        Get candles from the last N minutes.
        
        Args:
            symbol: Trading pair symbol
            minutes: Number of minutes back to fetch
            interval: Candle interval
            
        Returns:
            DataFrame with recent candle data
        """
        if interval == "1":
            limit = min(minutes, 200)
        else:  # 5m interval
            limit = min(minutes // 5, 200)
            
        return self.get_candles(symbol, interval, limit)
        
    def validate_data_quality(self, df: pd.DataFrame) -> Dict[str, bool]:
        """
        Validate candle data quality.
        
        Args:
            df: Candle DataFrame
            
        Returns:
            Dictionary of validation results
        """
        checks = {}
        
        # Basic data checks
        checks["has_data"] = len(df) > 0
        checks["no_missing_ohlc"] = not df[["open", "high", "low", "close"]].isna().any().any()
        checks["positive_volume"] = (df["volume"] >= 0).all()
        checks["valid_ohlc"] = (df["low"] <= df["high"]).all() and (df["low"] <= df["close"]).all()
        checks["chronological"] = df["timestamp"].is_monotonic_increasing
        
        # MNT-specific checks (adjusted for real MNT price range ~$0.01-$10)
        checks["reasonable_prices"] = (df["close"] > 0.01).all() and (df["close"] < 10.0).all()
        checks["reasonable_volatility"] = (abs(df["returns"]) < 0.5).all()  # No >50% moves
        
        # Data freshness (within last 24 hours - very lenient for testing)
        if len(df) > 0:
            latest_time = df["timestamp"].iloc[-1]  # UTC-aware timestamp
            now = datetime.now(timezone.utc)  # UTC time for proper comparison
            checks["data_fresh"] = (now - latest_time).total_seconds() < 86400  # 24 hours
        else:
            checks["data_fresh"] = False
            
        return checks
        
    def get_data_summary(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Get summary statistics for candle data.
        
        Args:
            df: Candle DataFrame
            
        Returns:
            Dictionary of summary statistics
        """
        if len(df) == 0:
            return {}
            
        return {
            "count": len(df),
            "timeframe_hours": (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 3600,
            "current_price": df["close"].iloc[-1],
            "price_change_pct": (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100,
            "avg_volume": df["volume"].mean(),
            "max_range_ratio": df["range_ratio"].max(),
            "avg_range_ratio": df["range_ratio"].mean(),
            "volatility_realized": df["returns"].std() * np.sqrt(1440),  # Daily vol
        }