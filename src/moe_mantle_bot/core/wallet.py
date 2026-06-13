"""
Centralized wallet management for the Merchant Moe (Mantle) farming bot.

This module consolidates wallet loading functionality that was previously
duplicated across multiple files (rebalance.py, lp_manager.py, farm_bot.py, etc).
"""

from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..wallet_store import WalletRecord


def load_wallet(settings: Settings) -> WalletRecord:
    """
    Load wallet from file or environment.
    
    This replaces the various _load_wallet functions scattered throughout
    the codebase with a single, centralized implementation.
    
    Args:
        settings: Bot settings containing wallet configuration
        
    Returns:
        WalletRecord with loaded wallet information
        
    Raises:
        FileNotFoundError: If wallet file doesn't exist
        ValueError: If wallet data is invalid
    """
    wallet_file = settings.wallet_file
    
    if wallet_file.exists():
        return WalletRecord.from_file(wallet_file)
    
    # Fallback to environment variable
    private_key = settings.private_key
    if private_key:
        return WalletRecord.from_private_key(private_key)
    
    raise FileNotFoundError(
        f"No wallet found. Expected wallet file at {wallet_file} or PRIVATE_KEY environment variable."
    )