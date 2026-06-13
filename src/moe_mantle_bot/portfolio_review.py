"""
Portfolio Review Module - Comprehensive LP and balance analysis.

Provides full overview of all active LP positions (narrow/wide) and wallet balances.
"""

from __future__ import annotations

import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from decimal import Decimal
from datetime import datetime, timezone

from .config import Settings
from .core.wallet import load_wallet
from .logging_config import get_logger
from .rpc_client import RpcClient
from .snapshot import SnapshotService
from .utils import price_from_bin_id
from .telegram import send_to_telegram

logger = get_logger(__name__)


@dataclass
class LPPositionInfo:
    """Information about a single LP position."""
    position_type: str  # "narrow" or "wide"
    bin_range: tuple[int, int]
    price_range: tuple[float, float]
    bins_count: int
    in_range: bool
    mnt_amount: Decimal
    usdt_amount: Decimal
    total_value_usdt: Decimal
    position_id: Optional[str] = None
    entry_time: Optional[datetime] = None


@dataclass
class BalanceInfo:
    """Wallet balance information."""
    native_mnt: Decimal
    wmnt: Decimal
    usdt: Decimal
    total_mnt: Decimal  # native_mnt + wmnt
    mnt_price_usdt: Decimal
    total_value_mnt: Decimal  # All balances converted to MNT
    total_value_usdt: Decimal  # All balances converted to USDT


@dataclass
class PortfolioReview:
    """Complete portfolio review."""
    timestamp: datetime
    wallet_address: str
    balances: BalanceInfo
    narrow_positions: List[LPPositionInfo]
    wide_positions: List[LPPositionInfo]
    total_lp_value_usdt: Decimal
    total_portfolio_value_usdt: Decimal
    total_portfolio_value_mnt: Decimal
    active_bin: int
    current_price: Decimal
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "wallet_address": self.wallet_address,
            "balances": {
                "native_mnt": float(self.balances.native_mnt),
                "wmnt": float(self.balances.wmnt),
                "usdt": float(self.balances.usdt),
                "total_mnt": float(self.balances.total_mnt),
                "mnt_price_usdt": float(self.balances.mnt_price_usdt),
                "total_value_mnt": float(self.balances.total_value_mnt),
                "total_value_usdt": float(self.balances.total_value_usdt),
            },
            "narrow_positions": [
                {
                    "type": pos.position_type,
                    "bin_range": pos.bin_range,
                    "price_range": pos.price_range,
                    "bins_count": pos.bins_count,
                    "in_range": pos.in_range,
                    "mnt_amount": float(pos.mnt_amount),
                    "usdt_amount": float(pos.usdt_amount),
                    "total_value_usdt": float(pos.total_value_usdt),
                }
                for pos in self.narrow_positions
            ],
            "wide_positions": [
                {
                    "type": pos.position_type,
                    "bin_range": pos.bin_range,
                    "price_range": pos.price_range,
                    "bins_count": pos.bins_count,
                    "in_range": pos.in_range,
                    "mnt_amount": float(pos.mnt_amount),
                    "usdt_amount": float(pos.usdt_amount),
                    "total_value_usdt": float(pos.total_value_usdt),
                    "position_id": pos.position_id,
                }
                for pos in self.wide_positions
            ],
            "total_lp_value_usdt": float(self.total_lp_value_usdt),
            "total_portfolio_value_usdt": float(self.total_portfolio_value_usdt),
            "total_portfolio_value_mnt": float(self.total_portfolio_value_mnt),
            "active_bin": self.active_bin,
            "current_price": float(self.current_price),
        }
    
    def format_display(self) -> str:
        """Format review for display."""
        lines = [
            "=" * 60,
            "📊 PORTFOLIO REVIEW",
            "=" * 60,
            f"🕐 Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"👛 Wallet: {self.wallet_address[:8]}...{self.wallet_address[-6:]}",
            f"💹 MNT Price: ${self.balances.mnt_price_usdt:.4f}",
            f"📍 Active Bin: {self.active_bin} (Price: ${self.current_price:.4f})",
            "",
            "💰 WALLET BALANCES",
            "-" * 40,
            f"  MNT (native): {self.balances.native_mnt:.4f} MNT",
            f"  WMNT:         {self.balances.wmnt:.4f} WMNT",
            f"  USDT:         {self.balances.usdt:.2f} USDT",
            f"  Total MNT:    {self.balances.total_mnt:.4f} MNT",
            "",
            f"  Value in USDT: ${self.balances.total_value_usdt:.2f}",
            f"  Value in MNT:  {self.balances.total_value_mnt:.4f} MNT",
        ]
        
        # Narrow positions
        if self.narrow_positions:
            lines.extend([
                "",
                "🎯 NARROW-RANGE POSITIONS",
                "-" * 40,
            ])
            for i, pos in enumerate(self.narrow_positions, 1):
                status = "✅ IN RANGE" if pos.in_range else "⚠️ OUT OF RANGE"
                lines.extend([
                    f"  Position #{i}: {status}",
                    f"    Bins: {pos.bin_range[0]}-{pos.bin_range[1]} ({pos.bins_count} bins)",
                    f"    Price Range: ${pos.price_range[0]:.4f} - ${pos.price_range[1]:.4f}",
                    f"    Holdings: {pos.mnt_amount:.2f} MNT + {pos.usdt_amount:.2f} USDT",
                    f"    Value: ${pos.total_value_usdt:.2f}",
                ])
        else:
            lines.extend([
                "",
                "🎯 NARROW-RANGE POSITIONS",
                "-" * 40,
                "  No narrow-range positions",
            ])
        
        # Wide positions
        if self.wide_positions:
            lines.extend([
                "",
                "🌊 WIDE-RANGE POSITIONS",
                "-" * 40,
            ])
            for i, pos in enumerate(self.wide_positions, 1):
                status = "✅ IN RANGE" if pos.in_range else "⚠️ OUT OF RANGE"
                lines.extend([
                    f"  Position #{i}: {status}",
                    f"    Bins: {pos.bin_range[0]}-{pos.bin_range[1]} ({pos.bins_count} bins)",
                    f"    Price Range: ${pos.price_range[0]:.4f} - ${pos.price_range[1]:.4f}",
                    f"    Holdings: {pos.mnt_amount:.2f} MNT + {pos.usdt_amount:.2f} USDT",
                    f"    Value: ${pos.total_value_usdt:.2f}",
                ])
        else:
            lines.extend([
                "",
                "🌊 WIDE-RANGE POSITIONS",
                "-" * 40,
                "  No wide-range positions",
            ])
        
        # Summary
        lines.extend([
            "",
            "📈 PORTFOLIO SUMMARY",
            "-" * 40,
            f"  Total LP Value:        ${self.total_lp_value_usdt:.2f}",
            f"  Total Portfolio (USD): ${self.total_portfolio_value_usdt:.2f}",
            f"  Total Portfolio (MNT): {self.total_portfolio_value_mnt:.2f} MNT",
            "=" * 60,
        ])
        
        return "\n".join(lines)
    
    def format_telegram(self) -> str:
        """Format review for Telegram notification."""
        # Emoji status for positions
        narrow_status = "🟢" if any(p.in_range for p in self.narrow_positions) else "🔴" if self.narrow_positions else "⚫"
        wide_status = "🟢" if any(p.in_range for p in self.wide_positions) else "🔴" if self.wide_positions else "⚫"
        
        lines = [
            "📊 <b>PORTFOLIO REVIEW</b>",
            "",
            f"👛 <code>{self.wallet_address[:8]}...{self.wallet_address[-6:]}</code>",
            f"💹 MNT: <b>${self.balances.mnt_price_usdt:.4f}</b>",
            f"📍 Bin: {self.active_bin} (${self.current_price:.4f})",
            "",
            "<b>💰 BALANCES</b>",
            f"├ MNT: <b>{self.balances.native_mnt:.2f}</b>",
            f"├ WMNT: <b>{self.balances.wmnt:.2f}</b>",
            f"├ USDT: <b>{self.balances.usdt:.2f}</b>",
            f"└ Total: <b>{self.balances.total_mnt:.2f} MNT</b>",
            "",
            f"<b>📊 LP POSITIONS</b>",
            f"{narrow_status} Narrow: {len(self.narrow_positions)} position(s)",
        ]
        
        if self.narrow_positions:
            for pos in self.narrow_positions:
                range_str = f"${pos.price_range[0]:.4f}-${pos.price_range[1]:.4f}"
                status = "✅" if pos.in_range else "❌"
                lines.append(f"  {status} {range_str} (${pos.total_value_usdt:.0f})")
        
        lines.append(f"{wide_status} Wide: {len(self.wide_positions)} position(s)")
        
        if self.wide_positions:
            for pos in self.wide_positions:
                range_str = f"${pos.price_range[0]:.4f}-${pos.price_range[1]:.4f}"
                status = "✅" if pos.in_range else "❌"
                lines.append(f"  {status} {range_str} (${pos.total_value_usdt:.0f})")
        
        lines.extend([
            "",
            "<b>💎 TOTAL VALUE</b>",
            f"├ LP Positions: <b>${self.total_lp_value_usdt:.2f}</b>",
            f"├ Portfolio USD: <b>${self.total_portfolio_value_usdt:.2f}</b>",
            f"└ Portfolio MNT: <b>{self.total_portfolio_value_mnt:.0f} MNT</b>",
        ])
        
        return "\n".join(lines)


class PortfolioReviewer:
    """Service for generating portfolio reviews."""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.wallet = load_wallet(settings)
        self._rpc = RpcClient(settings)
        from .lp_service import LPService
        self._lp = LPService.read_only(self._rpc, settings)
        self.snapshot_service = SnapshotService(settings)
        
        # Initialize LP registry
        from ._lp_registry import LPRegistry
        self.registry = LPRegistry(self.wallet.address, settings.data_dir)
        
    def get_narrow_positions(self, snapshot: Dict[str, Any]) -> List[LPPositionInfo]:
        """Extract narrow-range positions from registry and snapshot."""
        positions = []
        
        # Get active narrow positions from registry
        registry_narrow = self.registry.get_narrow_positions()
        
        # Get current position data from snapshot for verification
        position_data = snapshot.get('position', {})
        pool_data = snapshot.get('pool', {})
        active_bin = pool_data.get('active_bin_id', 0)
        
        for reg_pos in registry_narrow:
            # Check if this position's bins actually exist onchain
            # (registry position may be stale if removal failed)
            try:
                # Calculate price range
                lower_price = float(price_from_bin_id(reg_pos.min_bin, 25, 18, 6))
                upper_price = float(price_from_bin_id(reg_pos.max_bin, 25, 18, 6))
                
                # Check if in range
                in_range = reg_pos.min_bin <= active_bin <= reg_pos.max_bin
                
                # Use registry data for amounts (initial values)
                # TODO: Get current values from onchain if needed
                mnt_amount = Decimal(str(reg_pos.initial_mnt))
                usdt_amount = Decimal(str(reg_pos.initial_usdt))
                
                # Calculate value
                mnt_price = Decimal(str(snapshot.get('pool', {}).get('mnt_price_usdt', 0)))
                total_value_usdt = mnt_amount * mnt_price + usdt_amount
                
                positions.append(LPPositionInfo(
                    position_type="narrow",
                    bin_range=(reg_pos.min_bin, reg_pos.max_bin),
                    price_range=(lower_price, upper_price),
                    bins_count=reg_pos.bin_count,
                    in_range=in_range,
                    mnt_amount=mnt_amount,
                    usdt_amount=usdt_amount,
                    total_value_usdt=total_value_usdt,
                    position_id=reg_pos.id,
                    entry_time=datetime.fromisoformat(reg_pos.created_at),
                ))
            except Exception as e:
                logger.debug(f"Could not process narrow position {reg_pos.id}: {e}")
        
        return positions
    
    def get_wide_positions(self) -> List[LPPositionInfo]:
        """Extract wide-range positions from registry."""
        positions = []
        
        # Get active wide positions from registry
        registry_wide = self.registry.get_wide_positions()
        
        # Get current pool state for active bin
        try:
            pool_state = self._lp.get_pool_state()
            active_bin = pool_state.active_bin_id
            mnt_price = pool_state.mnt_price_usdt or Decimal(0)
        except Exception:
            logger.warning("Failed to fetch pool state for wide position analysis")
            return positions
        
        for reg_pos in registry_wide:
            try:
                # Calculate price range
                lower_price = float(price_from_bin_id(reg_pos.min_bin, 25, 18, 6))
                upper_price = float(price_from_bin_id(reg_pos.max_bin, 25, 18, 6))
                
                # Check if in range
                in_range = reg_pos.min_bin <= active_bin <= reg_pos.max_bin
                
                # Use registry data for amounts
                mnt_amount = Decimal(str(reg_pos.initial_mnt))
                usdt_amount = Decimal(str(reg_pos.initial_usdt))
                total_value_usdt = mnt_amount * mnt_price + usdt_amount
                
                positions.append(LPPositionInfo(
                    position_type="wide",
                    bin_range=(reg_pos.min_bin, reg_pos.max_bin),
                    price_range=(lower_price, upper_price),
                    bins_count=reg_pos.bin_count,
                    in_range=in_range,
                    mnt_amount=mnt_amount,
                    usdt_amount=usdt_amount,
                    total_value_usdt=total_value_usdt,
                    position_id=reg_pos.id,
                    entry_time=datetime.fromisoformat(reg_pos.created_at),
                ))
            except Exception as e:
                logger.debug(f"Could not process wide position {reg_pos.id}: {e}")
        
        # Note: Wide range manager deprecated - using registry only
        # Registry provides single source of truth for all positions
        
        return positions
    
    def generate_review(self) -> PortfolioReview:
        """Generate comprehensive portfolio review."""
        # Get snapshot with LP inventory to ensure we detect positions
        # Note: deep_position_search disabled by default for performance
        # Set to True only if you need to find very old positions
        snapshot = self.snapshot_service.build(
            self.wallet.address, 
            include_position_inventory=True,
            deep_position_search=False  # Disabled for faster startup
        )
        
        # Get pool info
        pool_data = snapshot.get('pool', {})
        active_bin = pool_data.get('active_bin_id', 0)
        mnt_price = Decimal(str(pool_data.get('mnt_price_usdt', 0)))
        current_price = mnt_price  # For simplicity, using MNT price as current price
        
        # Get wallet balances
        wallet_data = snapshot.get('wallet', {})
        native_mnt = Decimal(str(wallet_data.get('native_mnt', {}).get('normalized', 0)))
        wmnt = Decimal(str(wallet_data.get('wmnt', {}).get('normalized', 0)))
        usdt = Decimal(str(wallet_data.get('usdt', {}).get('normalized', 0)))
        
        total_mnt = native_mnt + wmnt
        total_value_usdt = total_mnt * mnt_price + usdt
        total_value_mnt = total_value_usdt / mnt_price if mnt_price > 0 else Decimal(0)
        
        balances = BalanceInfo(
            native_mnt=native_mnt,
            wmnt=wmnt,
            usdt=usdt,
            total_mnt=total_mnt,
            mnt_price_usdt=mnt_price,
            total_value_mnt=total_value_mnt,
            total_value_usdt=total_value_usdt,
        )
        
        # Get positions
        narrow_positions = self.get_narrow_positions(snapshot)
        wide_positions = self.get_wide_positions()
        
        # Calculate total LP value
        total_lp_value_usdt = sum(pos.total_value_usdt for pos in narrow_positions + wide_positions)
        
        # Total portfolio value
        total_portfolio_value_usdt = total_value_usdt + total_lp_value_usdt
        total_portfolio_value_mnt = total_portfolio_value_usdt / mnt_price if mnt_price > 0 else Decimal(0)
        
        return PortfolioReview(
            timestamp=datetime.now(timezone.utc),
            wallet_address=self.wallet.address,
            balances=balances,
            narrow_positions=narrow_positions,
            wide_positions=wide_positions,
            total_lp_value_usdt=total_lp_value_usdt,
            total_portfolio_value_usdt=total_portfolio_value_usdt,
            total_portfolio_value_mnt=total_portfolio_value_mnt,
            active_bin=active_bin,
            current_price=current_price,
        )
    
    def display_and_notify(self) -> PortfolioReview:
        """Generate review, display it, and send to Telegram."""
        review = self.generate_review()
        
        # Display in console
        print(review.format_display())
        
        # Send to Telegram
        try:
            telegram_message = review.format_telegram()
            success = send_to_telegram(telegram_message)
            if success:
                logger.info("✅ Portfolio review sent to Telegram")
            else:
                logger.warning("⚠️ Failed to send portfolio review to Telegram")
        except Exception as e:
            logger.error(f"❌ Error sending portfolio review to Telegram: {e}")
        
        # Also log as JSON for analysis
        logger.debug(f"Portfolio review data: {json.dumps(review.to_dict(), indent=2)}")
        
        return review


def get_portfolio_review(settings: Settings) -> PortfolioReview:
    """Get a portfolio review."""
    reviewer = PortfolioReviewer(settings)
    return reviewer.generate_review()


def display_portfolio_review(settings: Settings) -> PortfolioReview:
    """Display portfolio review and send to Telegram."""
    reviewer = PortfolioReviewer(settings)
    return reviewer.display_and_notify()