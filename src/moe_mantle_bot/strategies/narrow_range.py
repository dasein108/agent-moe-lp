"""
Narrow-range LP strategy for V3 farm bot.

Extracted from V2 bot to provide focused narrow-range functionality
without the complexity of the full V2 implementation.
"""

from __future__ import annotations

from typing import Dict, Any, Optional
from dataclasses import dataclass
from decimal import Decimal

from ..balance_manager import BalanceManager
from ..config import Settings
from ..core.wallet import load_wallet
from ..logging_config import get_logger
from ..lp_service import LPService
from ..rpc_client import RpcClient
from ..tx_sender import TxSender

logger = get_logger(__name__)


@dataclass
class NarrowRangeResult:
    """Result of narrow-range strategy execution."""
    success: bool
    action_taken: str
    message: str
    details: Optional[Dict[str, Any]] = None


class NarrowRangeStrategy:
    """
    Lightweight narrow-range LP strategy.

    Provides the essential narrow-range functionality that V3 needs
    without the complexity of the full V2 implementation.
    """

    def __init__(
        self,
        settings: Settings,
        balance: BalanceManager | None = None,
        lp: LPService | None = None,
    ):
        self.settings = settings
        self.wallet = load_wallet(settings)

        # Use injected services or create standalone
        if balance is not None and lp is not None:
            self.balance = balance
            self.lp = lp
        else:
            rpc = RpcClient(settings)
            tx = TxSender(rpc, self.wallet, settings)
            self.balance = BalanceManager(rpc, tx, settings)
            self.lp = LPService(rpc, tx, self.balance, settings)

    def has_active_position(self) -> bool:
        """Check if there's an active narrow-range position."""
        try:
            return self.lp.has_active_position(self.wallet.address)
        except Exception as e:
            logger.debug(f"Could not check narrow position status: {e}")
            return False

    def analyze_conditions(self) -> Dict[str, Any]:
        """Analyze narrow-range market conditions."""
        try:
            pos = self.lp.get_position(self.wallet.address, include_inventory=False)
            has_position = pos.position_exists
            in_range = pos.in_range

            should_enter = not has_position
            should_exit = has_position and not in_range
            should_hold = has_position and in_range

            return {
                "should_enter": should_enter,
                "should_exit": should_exit,
                "should_hold": should_hold,
                "has_position": has_position,
                "in_range": in_range,
                "regime": "SIMPLIFIED",
                "quant_enabled": True,
                "combined_confidence": 0.7,
                "reasoning": self._generate_reasoning(should_enter, should_exit, should_hold),
            }
        except Exception as e:
            logger.error(f"Narrow-range analysis failed: {e}")
            return {
                "should_enter": False, "should_exit": False, "should_hold": True,
                "has_position": False, "in_range": False,
                "regime": "ERROR", "quant_enabled": False,
                "combined_confidence": 0.0, "reasoning": f"Analysis failed: {e}",
            }

    def _generate_reasoning(self, should_enter: bool, should_exit: bool, should_hold: bool) -> str:
        if should_enter:
            return "No active position detected - favorable for narrow-range entry"
        if should_exit:
            return "Position out of range - exit recommended"
        if should_hold:
            return "Position in range - maintain current position"
        return "No clear action - hold current state"

    async def execute_enhanced_farming_cycle(self, dry_run: bool = True) -> NarrowRangeResult:
        """Execute simplified narrow-range farming cycle."""
        logger.info(f"Executing narrow-range cycle {'(DRY RUN)' if dry_run else '(LIVE)'}")

        try:
            conditions = self.analyze_conditions()
            action_taken = "HOLD"
            message = conditions["reasoning"]

            if conditions["should_exit"] and not dry_run:
                action_taken, message = self._execute_exit()

            elif conditions["should_enter"] and not dry_run:
                action_taken, message = self._execute_enter()

            return NarrowRangeResult(
                success=True, action_taken=action_taken, message=message, details=conditions,
            )
        except Exception as e:
            logger.error(f"Narrow-range cycle failed: {e}")
            return NarrowRangeResult(success=False, action_taken="ERROR", message=f"Cycle failed: {e}")

    def _execute_exit(self) -> tuple[str, str]:
        """Remove out-of-range position."""
        try:
            self.lp.remove_position(dry_run=False)
            logger.info("Removed narrow-range position")
            return "EXIT", "Removed out-of-range position"
        except Exception as e:
            logger.error(f"Failed to remove position: {e}")
            return "ERROR", f"Position removal failed: {e}"

    def _execute_enter(self) -> tuple[str, str]:
        """Create new narrow-range position."""
        try:
            pool = self.lp.get_pool_state()
            if pool.mnt_price_usdt is None:
                return "ERROR", "Cannot determine MNT price"

            alloc = self.balance.calculate_lp_allocation(
                self.wallet.address,
                target_pct=0.6,
                safety_margin=0.95,
                min_size_usdt=self.settings.min_position_size_usdt,
            )

            if not alloc.is_viable:
                logger.warning(f"Insufficient capital: {alloc.reason}")
                return "SKIP", alloc.reason

            # Validate position size
            ok, reason = self.lp.validate_position_size(
                alloc.amount_wmnt, alloc.amount_usdt, pool.mnt_price_usdt,
            )
            if not ok:
                logger.warning(f"Position validation failed: {reason}")
                return "SKIP", reason

            total_value = alloc.amount_wmnt * pool.mnt_price_usdt + alloc.amount_usdt
            logger.info(f"Creating narrow position: ${float(total_value):.2f} "
                        f"({float(alloc.amount_wmnt):.2f} MNT + {float(alloc.amount_usdt):.2f} USDT)")

            self.lp.create_position(
                amount_wmnt=alloc.amount_wmnt,
                amount_usdt=alloc.amount_usdt,
                bin_count=self.settings.bin_count,
                distribution_params=self.settings.get_narrow_distribution_params(),
                dry_run=False,
            )
            logger.info("Created narrow-range position successfully")
            return "ENTER", "Created new narrow-range position"

        except Exception as e:
            logger.error(f"Failed to create position: {e}")
            return "ERROR", f"Position creation failed: {e}"


class EnhancedFarmBotV2:
    """Lightweight replacement for the archived V2 bot."""

    def __init__(self, settings: Settings, enable_quant: bool = True,
                 balance: BalanceManager | None = None, lp: LPService | None = None):
        self.settings = settings
        self.enable_quant = enable_quant
        self.strategy = NarrowRangeStrategy(settings, balance=balance, lp=lp)
        logger.info("Lightweight narrow-range strategy initialized")

    def has_active_position(self) -> bool:
        return self.strategy.has_active_position()

    def analyze_market_conditions(self) -> Dict[str, Any]:
        return self.strategy.analyze_conditions()

    async def analyze_enhanced_market_conditions(self) -> Dict[str, Any]:
        return self.strategy.analyze_conditions()

    async def execute_enhanced_farming_cycle(self, dry_run: bool = True):
        return await self.strategy.execute_enhanced_farming_cycle(dry_run)
