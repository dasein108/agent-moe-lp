from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any

from .logging_config import get_logger

getcontext().prec = 78  # Enough for uint256 max (78 decimal digits)

Q128 = Decimal(2**128)
REAL_ID_SHIFT = 2**23


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def scaled_decimal(raw_amount: int, decimals: int) -> Decimal:
    return Decimal(raw_amount) / (Decimal(10) ** decimals)


def price_128x128_to_decimal(raw_price: int) -> Decimal:
    return Decimal(raw_price) / Q128


def price_from_bin_id(bin_id: int, bin_step: int, decimals_x: int, decimals_y: int) -> Decimal:
    ratio = Decimal(1) + (Decimal(bin_step) / Decimal(10_000))
    adjusted = ratio**Decimal(bin_id - REAL_ID_SHIFT)
    return adjusted * (Decimal(10) ** Decimal(decimals_x - decimals_y))


def format_decimal(value: Decimal, places: int = 8) -> str:
    quant = Decimal(10) ** -places
    return format(value.quantize(quant), "f")


def serialize_decimal(value: Decimal, places: int = 12) -> str:
    try:
        quant = Decimal(10) ** -places
        normalized = value.quantize(quant)
        text = format(normalized, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    except InvalidOperation:
        # Value too large for quantize (e.g., MAX_UINT256 allowance) — use str
        return str(value)


def json_serializer(obj):
    """Custom JSON serializer for handling Decimal, Enum, and numpy types."""
    from enum import Enum
    from decimal import Decimal
    from datetime import datetime
    
    if isinstance(obj, Decimal):
        return serialize_decimal(obj)
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif hasattr(obj, 'to_dict'):
        return obj.to_dict()
    else:
        # Handle numpy types if numpy is available
        try:
            import numpy as np
            if isinstance(obj, (np.integer, np.floating)):
                return obj.item()  # Convert numpy numbers to Python types
            elif isinstance(obj, np.ndarray):
                return obj.tolist()  # Convert numpy arrays to lists
            elif isinstance(obj, np.bool_):
                return bool(obj)  # Convert numpy bool to Python bool
        except ImportError:
            pass
        
        # Handle pandas types if pandas is available
        try:
            import pandas as pd
            if isinstance(obj, (pd.Timestamp, pd.Timedelta)):
                return str(obj)
            elif hasattr(obj, 'dtype') and 'bool' in str(obj.dtype):
                return bool(obj)  # Handle pandas bool types
        except ImportError:
            pass
        
        # Handle generic bool-like objects
        if hasattr(obj, 'item') and callable(obj.item):
            try:
                return obj.item()  # For numpy-like scalar objects
            except:
                pass
                
        # Last resort for bool-like objects
        if str(type(obj).__name__) in ['bool_', 'boolean']:
            return bool(obj)
        
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def debug_log(enabled: bool, message: str) -> None:
    if enabled:
        logger = get_logger("utils.debug")
        logger.debug(f"[moe-debug] {message}")
