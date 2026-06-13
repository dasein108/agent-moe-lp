"""
Wide-Range LP Manager for Keltner Channel-based fee farming.

Manages wide-range LP positions (1-10%) for consistent fee capture
over longer time periods, complementing narrow-range reward sniping.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from .keltner_analyzer import KeltnerAnalyzer, ChannelConfig, ChannelAnalysis
from ..config import Settings
from ..logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class WideRangePosition:
    """Wide-range LP position data."""
    position_id: str
    entry_time: float
    entry_price: float
    lower_price: float
    upper_price: float
    lower_bin_id: int
    upper_bin_id: int
    total_bins: int
    capital_deployed: float
    strategy_config: str
    channel_quality: str
    expected_hold_time_hours: int
    
    # Performance tracking
    initial_mnt_amount: float = 0.0
    initial_usdt_amount: float = 0.0
    fees_accumulated: float = 0.0
    impermanent_loss: float = 0.0
    last_update_time: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'WideRangePosition':
        return cls(**data)


@dataclass
class WideRangeOpportunity:
    """Wide-range LP opportunity assessment."""
    action: str  # ENTER_WIDE_RANGE, WAIT, CONSIDER_ENTRY
    confidence: float
    channel_analysis: ChannelAnalysis
    optimal_range: Dict[str, float]
    capital_allocation: float
    expected_hold_time: int
    reasoning: str
    risk_assessment: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            'action': self.action,
            'confidence': self.confidence,
            'channel_analysis': self.channel_analysis.to_dict(),
            'optimal_range': self.optimal_range,
            'capital_allocation': self.capital_allocation,
            'expected_hold_time': self.expected_hold_time,
            'reasoning': self.reasoning,
            'risk_assessment': self.risk_assessment
        }


class WideRangeLPManager:
    """Manage wide-range LP positions based on Keltner Channel analysis."""

    def __init__(self, settings: Settings, lp_service=None):
        self.settings = settings
        self.keltner_analyzer = KeltnerAnalyzer()
        self.lp_service = lp_service

        # Strategy parameters
        self.strategy_params = {
            'min_capital_utilization': 0.6,
            'max_capital_utilization': 0.9,
            'min_hold_time_hours': 6,
            'max_hold_time_hours': 168,
            'safety_margin': 0.1,
            'min_expected_apr': 0.25,
            'max_positions': 1,
        }

        self._can_execute = self.lp_service is not None
        if self._can_execute:
            logger.info("Wide-range LP manager initialized")
        else:
            logger.warning("Wide-range LP manager: no LPService — execution disabled")
    
    # Pool bin_step=5 → each bin covers 0.05% price range
    BIN_PRICE_PCT = 0.05

    # Keltner width multiplier — primary driver, sized for realized ranging band
    KELTNER_MULTIPLIER = 2.5

    # Minimum bin count floor
    MIN_WIDE_BINS = 40

    # Max bins per tx — bounded by the block gas limit for addLiquidityNATIVE
    MAX_WIDE_BINS = 200

    def calculate_wide_range_params(self, keltner_analysis: Dict[str, Any] | None = None,
                                    daily_atr_pct: float | None = None,
                                    pool_stats: dict | None = None,
                                    regime: str | None = None) -> Dict[str, Any]:
        """Calculate bin range and distribution params for wide strategy.

        Keltner-width-driven sizing (not ATR-dominated). Rationale:
        - Keltner channel width reflects *realized ranging band* — the price
          action LP can actually capture fees from.
        - ATR includes gap/trend moves that blow through any range. Using it
          as primary driver always saturates at 200 bins, wasting capital
          concentration (0.5% per bin vs 1% at 100 bins).
        - ATR kept as a floor (0.5×) for extreme-vol safety but not primary.

        Typical outputs (bin_step=15, BIN_PRICE_PCT≈0.05%):
        - Keltner 2%, ATR 10%: max(40, 100, 42, 100) = 100 bins (16% range)
        - Keltner 3%, ATR 12%: max(40, 150, 51, 120) = 150 bins (24% range)
        - Keltner 5%, ATR 14%: max(40, 200, 67, 140) = 200 bins (cap)
        """
        if keltner_analysis is not None:
            bounds = keltner_analysis.get("bounds", {})
            width_pct = bounds.get("width_pct") or keltner_analysis.get("width_pct") or 3.0

            # Primary: Keltner channel width × 2.5 — covers full ranging band
            # with headroom for overshoot. At 2% width → 100 bins (16% range).
            from_keltner = int(width_pct * self.KELTNER_MULTIPLIER / self.BIN_PRICE_PCT)

            # Secondary: sqrt scaling for diminishing returns at wider channels
            from_sqrt = int(30 * (width_pct ** 0.5))

            # Floor: ATR × 0.5 — safety net in extreme vol, not primary driver.
            # At 10% ATR → 100 bins. At 14% ATR → 140 bins.
            atr = daily_atr_pct or 0
            from_atr = int(atr * 0.5 / self.BIN_PRICE_PCT) if atr else 0

            bin_count = max(self.MIN_WIDE_BINS, from_keltner, from_sqrt, from_atr)
            bin_count = min(self.MAX_WIDE_BINS, bin_count)

            # Dynamic fee-rate adjustment: when pool fees are high, tighten range
            # for more concentrated fee capture. When low, widen for fewer exits.
            fee_adj = ""
            if pool_stats:
                vol = pool_stats.get("volume_usd_24h") or 0
                fees = pool_stats.get("fees_usd_24h") or 0
                base_fee = pool_stats.get("base_fee_pct") or 0.04
                if vol > 0 and fees > 0:
                    effective_fee_pct = fees / vol * 100
                    fee_ratio = effective_fee_pct / base_fee  # 1.0=base, ~7=max
                    # High fee (ratio>3): tighten 20%. Low fee (ratio<1): widen 15%.
                    if fee_ratio > 3:
                        bin_count = max(self.MIN_WIDE_BINS, int(bin_count * 0.80))
                        fee_adj = f" fee_adj=-20% (ratio={fee_ratio:.1f})"
                    elif fee_ratio < 1:
                        bin_count = min(self.MAX_WIDE_BINS, int(bin_count * 1.15))
                        fee_adj = f" fee_adj=+15% (ratio={fee_ratio:.1f})"

            # Bear regime widening: +30% bins to reduce exit frequency.
            # Backtest: cuts exits from 14→10, IL from $32→$24 in bear scenarios.
            regime_adj = ""
            if regime == "TRENDING_DOWN":
                bin_count = min(self.MAX_WIDE_BINS, int(bin_count * 1.30))
                regime_adj = " bear_wider=+30%"

            logger.info(
                "Wide bin sizing: keltner_width=%.1f%% atr=%.1f%% "
                "from_keltner=%d from_sqrt=%d from_atr=%d → %d bins%s%s",
                width_pct, atr, from_keltner, from_sqrt, from_atr, bin_count, fee_adj, regime_adj,
            )
        else:
            bin_count = 100  # fallback ~16% range

        return {
            "bin_count": bin_count,
            "distribution_params": self.settings.get_wide_distribution_params(),
            "target_pct": 0.9,
            "keltner_analysis": keltner_analysis,
        }

    def analyze_wide_range_opportunity(self,
                                     config: ChannelConfig = ChannelConfig.BALANCED) -> WideRangeOpportunity:
        """
        Analyze opportunity for wide-range LP deployment.
        
        Strategy Logic:
        1. Assess current channel quality using Keltner analysis
        2. Check existing position status
        3. Determine optimal range width and positioning
        4. Calculate capital allocation based on opportunity quality
        5. Generate entry/exit/hold decision with risk assessment
        """
        
        try:
            # Get current channel analysis
            channel_analysis = self.keltner_analyzer.analyze_channel_conditions(
                config=config,
                timeframe="5"  # Use 5-minute timeframe for analysis
            )
            
            # Check existing wide-range positions
            current_positions = self._load_current_positions()
            
            # Determine strategy action based on analysis and position status
            if len(current_positions) >= self.strategy_params['max_positions']:
                # At position limit - evaluate existing positions
                decision = self._evaluate_position_management(current_positions, channel_analysis)
            elif not current_positions:
                # No positions - evaluate fresh entry
                decision = self._evaluate_entry_opportunity(channel_analysis)
            else:
                # Has positions but can add more - evaluate additional entry
                decision = self._evaluate_additional_entry(current_positions, channel_analysis)
            
            return decision
            
        except Exception as e:
            logger.error(f"Wide-range opportunity analysis failed: {e}")
            return self._get_default_opportunity()
    
    def _evaluate_entry_opportunity(self, channel_analysis: ChannelAnalysis) -> WideRangeOpportunity:
        """Evaluate whether to enter a new wide-range position."""
        
        # Entry criteria based on channel quality
        logger.info(f"📊 Wide-range entry criteria check:")
        logger.info(f"   Quality score: {channel_analysis.quality_score:.3f} (need > 0.6)")
        logger.info(f"   Is ranging: {channel_analysis.is_ranging} (need True)")
        logger.info(f"   Width: {channel_analysis.bounds.width_pct:.3f}% (need 1.0-10.0%)")
        logger.info(f"   Recommendation: {channel_analysis.recommendation} (need ENTER_WIDE_RANGE or CONSIDER_ENTRY)")
        
        should_enter = (
            channel_analysis.quality_score > 0.6 and          # Good quality channel
            channel_analysis.is_ranging and                   # Ranging market preferred
            0.5 <= channel_analysis.bounds.width_pct <= 10.0 and  # Adjusted width threshold
            channel_analysis.recommendation in ["ENTER_WIDE_RANGE", "CONSIDER_ENTRY"]
        )
        
        logger.info(f"   Should enter: {should_enter}")
        
        if should_enter:
            # Calculate optimal range
            optimal_range = self.keltner_analyzer.get_optimal_lp_range(
                channel_analysis,
                safety_margin=self.strategy_params['safety_margin']
            )
            
            # Determine capital allocation based on quality and confidence
            capital_allocation = self._calculate_capital_allocation(channel_analysis)
            
            # Estimate hold time based on channel characteristics
            expected_hold_time = self._estimate_hold_time(channel_analysis)
            
            # Risk assessment
            risk_assessment = self._assess_entry_risk(channel_analysis, optimal_range)
            
            action = "ENTER_WIDE_RANGE" if channel_analysis.quality_score > 0.7 else "CONSIDER_ENTRY"
            
            return WideRangeOpportunity(
                action=action,
                confidence=channel_analysis.confidence,
                channel_analysis=channel_analysis,
                optimal_range=optimal_range,
                capital_allocation=capital_allocation,
                expected_hold_time=expected_hold_time,
                reasoning=(
                    f"{channel_analysis.quality.value.title()} quality "
                    f"{channel_analysis.bounds.width_pct:.1f}% channel, "
                    f"ranging market conditions"
                ),
                risk_assessment=risk_assessment
            )
        else:
            # Determine specific reasons for waiting
            wait_reasons = self._get_wait_reasons(channel_analysis)
            
            return WideRangeOpportunity(
                action="WAIT",
                confidence=0.0,
                channel_analysis=channel_analysis,
                optimal_range={},
                capital_allocation=0.0,
                expected_hold_time=0,
                reasoning=f"Waiting for better conditions: {wait_reasons}",
                risk_assessment={'overall_risk': 'HIGH', 'recommendation': 'AVOID'}
            )
    
    def _evaluate_position_management(self,
                                    positions: List[WideRangePosition],
                                    channel_analysis: ChannelAnalysis) -> WideRangeOpportunity:
        """Evaluate management of existing wide-range positions."""
        
        current_time = time.time()
        management_actions = []
        
        for position in positions:
            position_age_hours = (current_time - position.entry_time) / 3600
            
            # Check if position should be exited
            should_exit = self._should_exit_position(position, channel_analysis, position_age_hours)
            
            if should_exit:
                management_actions.append({
                    'position_id': position.position_id,
                    'action': 'EXIT',
                    'reason': should_exit
                })
            else:
                # Check if position needs adjustment
                needs_adjustment = self._needs_position_adjustment(position, channel_analysis)
                if needs_adjustment:
                    management_actions.append({
                        'position_id': position.position_id,
                        'action': 'ADJUST',
                        'reason': needs_adjustment
                    })
        
        if management_actions:
            action = "MANAGE_POSITIONS"
            reasoning = f"Managing {len(management_actions)} position(s)"
        else:
            action = "HOLD_POSITIONS"
            reasoning = f"Holding {len(positions)} position(s)"
        
        return WideRangeOpportunity(
            action=action,
            confidence=0.5,  # Neutral confidence for management actions
            channel_analysis=channel_analysis,
            optimal_range={},
            capital_allocation=0.0,
            expected_hold_time=0,
            reasoning=reasoning,
            risk_assessment={'management_actions': management_actions}
        )
    
    def _calculate_capital_allocation(self, channel_analysis: ChannelAnalysis) -> float:
        """Calculate optimal capital allocation based on opportunity quality."""
        
        base_allocation = 0.7  # 70% base allocation
        
        # Adjust based on quality score
        quality_multiplier = channel_analysis.quality_score
        
        # Adjust based on channel width (wider = more capital needed)
        width_pct = channel_analysis.bounds.width_pct
        if width_pct > 6.0:
            width_adjustment = 0.9  # Reduce for very wide channels
        elif width_pct < 2.0:
            width_adjustment = 1.1  # Increase for narrow channels
        else:
            width_adjustment = 1.0  # Neutral for balanced channels
        
        # Apply constraints
        allocation = base_allocation * quality_multiplier * width_adjustment
        allocation = max(self.strategy_params['min_capital_utilization'], allocation)
        allocation = min(self.strategy_params['max_capital_utilization'], allocation)
        
        return allocation
    
    def _estimate_hold_time(self, channel_analysis: ChannelAnalysis) -> int:
        """Estimate optimal hold time based on channel characteristics."""
        
        # Base hold time in hours
        base_hold_time = 24  # 1 day base
        
        # Adjust based on channel quality (higher quality = longer hold)
        quality_multiplier = 0.5 + (channel_analysis.quality_score * 1.5)
        
        # Adjust based on market conditions
        if channel_analysis.is_ranging:
            market_multiplier = 1.5  # Longer holds in ranging markets
        else:
            market_multiplier = 0.7  # Shorter holds in trending markets
        
        # Adjust based on channel width
        width_pct = channel_analysis.bounds.width_pct
        if width_pct > 6.0:
            width_multiplier = 1.8  # Longer holds for wider channels
        elif width_pct < 2.0:
            width_multiplier = 0.6  # Shorter holds for narrow channels
        else:
            width_multiplier = 1.0
        
        estimated_hours = int(base_hold_time * quality_multiplier * market_multiplier * width_multiplier)
        
        # Apply constraints
        estimated_hours = max(self.strategy_params['min_hold_time_hours'], estimated_hours)
        estimated_hours = min(self.strategy_params['max_hold_time_hours'], estimated_hours)
        
        return estimated_hours
    
    def _assess_entry_risk(self,
                          channel_analysis: ChannelAnalysis,
                          optimal_range: Dict[str, float]) -> Dict[str, Any]:
        """Assess risk factors for entering wide-range position."""
        
        risks = {}
        overall_risk_score = 0.0
        
        # Channel quality risk
        quality_risk = 1.0 - channel_analysis.quality_score
        risks['channel_quality'] = {
            'score': quality_risk,
            'level': 'HIGH' if quality_risk > 0.4 else 'MEDIUM' if quality_risk > 0.2 else 'LOW'
        }
        overall_risk_score += quality_risk * 0.3
        
        # Width risk (too narrow = higher risk)
        width_pct = channel_analysis.bounds.width_pct
        if width_pct < 1.5:
            width_risk = 0.8  # High risk for very narrow channels
        elif width_pct > 8.0:
            width_risk = 0.6  # Medium-high risk for very wide channels
        else:
            width_risk = 0.2  # Low risk for optimal widths
        
        risks['channel_width'] = {
            'score': width_risk,
            'level': 'HIGH' if width_risk > 0.6 else 'MEDIUM' if width_risk > 0.3 else 'LOW',
            'width_pct': width_pct
        }
        overall_risk_score += width_risk * 0.25
        
        # Trend risk (trending markets higher risk)
        trend_risk = 0.8 if not channel_analysis.is_ranging else 0.2
        risks['market_trend'] = {
            'score': trend_risk,
            'level': 'HIGH' if trend_risk > 0.6 else 'LOW',
            'is_ranging': channel_analysis.is_ranging
        }
        overall_risk_score += trend_risk * 0.25
        
        # Position risk (price near bounds)
        position_risk = abs(channel_analysis.price_position - 0.5) * 2  # 0.0-1.0
        risks['price_position'] = {
            'score': position_risk,
            'level': 'HIGH' if position_risk > 0.7 else 'MEDIUM' if position_risk > 0.4 else 'LOW',
            'position': channel_analysis.price_position
        }
        overall_risk_score += position_risk * 0.2
        
        # Overall assessment
        if overall_risk_score > 0.6:
            risk_level = 'HIGH'
            recommendation = 'AVOID'
        elif overall_risk_score > 0.3:
            risk_level = 'MEDIUM'
            recommendation = 'CAUTION'
        else:
            risk_level = 'LOW'
            recommendation = 'PROCEED'
        
        return {
            'overall_risk_score': overall_risk_score,
            'overall_risk_level': risk_level,
            'recommendation': recommendation,
            'individual_risks': risks
        }
    
    async def create_wide_range_position(self, 
                                       opportunity: WideRangeOpportunity,
                                       dry_run: bool = True) -> Dict[str, Any]:
        """
        Create a wide-range LP position based on opportunity analysis.
        
        Args:
            opportunity: Wide-range opportunity assessment
            dry_run: If True, simulate the creation without actual execution
            
        Returns:
            Dictionary with creation result and position details
        """
        if not self._can_execute and not dry_run:
            raise RuntimeError("LP execution components not available")
            
        logger.info(f"🏗️ Creating wide-range LP position {'(DRY RUN)' if dry_run else '(LIVE)'}")
        
        try:
            # Calculate position parameters
            optimal_range = opportunity.optimal_range
            lower_price = optimal_range['lower_price']
            upper_price = optimal_range['upper_price']
            center_price = optimal_range['center_price']
            
            # Convert prices to bin IDs (assuming pool step = 25)
            pool_step = 25  # From WMNT/USDT pool
            lower_bin_id = self._price_to_bin_id(lower_price, center_price, pool_step)
            upper_bin_id = self._price_to_bin_id(upper_price, center_price, pool_step) 
            total_bins = upper_bin_id - lower_bin_id + 1
            
            # Calculate capital allocation from real wallet
            try:
                from ..wallet_store import WalletRecord
                from ..snapshot import SnapshotService
                
                # Load wallet and get real balances
                wallet_file = self.settings.wallet_file
                if wallet_file and wallet_file.exists():
                    wallet = WalletRecord.from_file(wallet_file)
                    snapshot_service = SnapshotService(self.settings)
                    snapshot = snapshot_service.build(wallet.address)
                    
                    # Get real balances from snapshot structure
                    wallet_data = snapshot.get('wallet', {})
                    if wallet_data:
                        # Get MNT balances (native + wrapped)
                        native_mnt = float(wallet_data.get('native_mnt', {}).get('normalized', 0))
                        wmnt = float(wallet_data.get('wmnt', {}).get('normalized', 0))
                        mnt_balance = native_mnt + wmnt
                        
                        # Get USDT balance
                        usdt_balance = float(wallet_data.get('usdt', {}).get('normalized', 0))
                    else:
                        mnt_balance = 0.0
                        usdt_balance = 0.0
                    
                    # Check for existing LP position
                    position_data = snapshot.get('position', {})
                    has_existing_position = position_data.get('position_exists', False)
                    existing_in_range = position_data.get('in_range', False)
                    
                    if has_existing_position:
                        existing_min_bin = position_data.get('min_bin_id', 0)
                        existing_max_bin = position_data.get('max_bin_id', 0)
                        existing_bins = existing_max_bin - existing_min_bin + 1 if existing_max_bin > existing_min_bin else 0
                        
                        logger.info(f"📊 Existing LP position detected:")
                        logger.info(f"   Range: bins {existing_min_bin} - {existing_max_bin} ({existing_bins} bins)")
                        logger.info(f"   In range: {existing_in_range}")
                        
                        # Only consider it a blocking wide-range position if it has > 50 bins
                        # Narrow-range positions typically have 10-40 bins
                        is_wide_range_position = existing_bins > 50
                        
                        if existing_in_range and is_wide_range_position:
                            logger.info("✅ Existing position is in range - skipping new position creation")
                            logger.info(f"📊 Existing position details: bins {existing_min_bin}-{existing_max_bin} ({existing_bins} bins)")
                            logger.info("💡 Reason: Bot detected existing LP position is still profitable and in range")
                            
                            # Calculate capital estimate for notification
                            try:
                                # Get wallet balances for capital estimate
                                wallet_data = snapshot.get('wallet', {})
                                native_mnt = float(wallet_data.get('native_mnt', {}).get('normalized', 0))
                                wmnt = float(wallet_data.get('wmnt', {}).get('normalized', 0))
                                usdt_balance = float(wallet_data.get('usdt', {}).get('normalized', 0))
                                mnt_balance = native_mnt + wmnt
                                estimated_capital = usdt_balance + (mnt_balance * center_price)
                                
                                # Send economic notification for skip (position in range)
                                from ..telegram import send_transaction_alert
                                send_transaction_alert(
                                    operation="SKIP",
                                    details=f"Position in range (bins {existing_min_bin}-{existing_max_bin})",
                                    economic_impact=f"Capital: ${estimated_capital:.2f} deployed"
                                )
                            except Exception as e:
                                logger.error(f"Failed to send skip notification: {e}")
                                # Send error notification to telegram
                                try:
                                    from ..telegram import send_error_with_action
                                    send_error_with_action(
                                        error=f"Skip notification failed: {str(e)[:50]}",
                                        action="Check logs"
                                    )
                                except:
                                    pass
                            
                            return {
                                "success": True,
                                "skipped": True,
                                "reason": "existing_position_in_range",
                                "message": f"Existing LP position (bins {existing_min_bin}-{existing_max_bin}) is already in range",
                                "existing_position": {
                                    "min_bin_id": existing_min_bin,
                                    "max_bin_id": existing_max_bin,
                                    "bin_count": existing_bins,
                                    "in_range": existing_in_range
                                }
                            }
                        elif not existing_in_range and is_wide_range_position:
                            logger.info("📤 Existing wide-range position is out of range - will need to exit first")
                            # TODO: Add logic to exit existing position before creating new one
                            return {
                                "success": False,
                                "reason": "existing_position_out_of_range", 
                                "message": f"Existing wide-range LP position is out of range. Please remove it first before creating new position.",
                                "existing_position": {
                                    "min_bin_id": existing_min_bin,
                                    "max_bin_id": existing_max_bin,
                                    "bin_count": existing_bins,
                                    "in_range": existing_in_range
                                }
                            }
                        elif not is_wide_range_position:
                            logger.info(f"📍 Existing position is narrow-range ({existing_bins} bins) - proceeding with wide-range creation")
                            # Continue with wide-range creation since narrow position doesn't block it
                    
                    # Calculate total available capital in USDT terms
                    available_usdt = usdt_balance + (mnt_balance * center_price)
                    logger.info(f"💰 Real wallet: {mnt_balance:.2f} MNT, {usdt_balance:.2f} USDT (${available_usdt:.2f} total)")
                else:
                    # Fallback if no wallet file
                    available_usdt = 100.0  # Conservative fallback
                    logger.warning("⚠️ No wallet file found, using conservative capital estimate")
                    
            except Exception as e:
                logger.error(f"❌ Failed to get real wallet balance: {e}")
                available_usdt = 100.0  # Conservative fallback
                
            capital_to_deploy = available_usdt * opportunity.capital_allocation
            
            # Check if position meets minimum size requirement
            mnt_price_decimal = Decimal(str(center_price))

            min_size_check, size_reason = self.lp_service.validate_position_size(
                amount_wmnt=Decimal('0'),
                amount_usdt=Decimal(str(capital_to_deploy)),
                mnt_price_usdt=mnt_price_decimal,
            )
            
            if not min_size_check:
                logger.warning(f"💰 Wide-range position too small: {size_reason}")
                return {
                    "success": False,
                    "reason": "position_too_small",
                    "message": size_reason,
                    "capital_to_deploy": capital_to_deploy,
                    "min_required": self.settings.min_position_size_usdt
                }
            
            # Split capital between MNT and USDT (approximately 50/50 by value)
            usdt_amount = capital_to_deploy / 2
            mnt_amount = usdt_amount / center_price
            
            logger.info(f"   Range: {lower_price:.4f} - {upper_price:.4f} ({total_bins} bins)")
            logger.info(f"   Capital: {capital_to_deploy:.2f} USDT ({usdt_amount:.2f} USDT + {mnt_amount:.2f} MNT)")
            
            if dry_run:
                # Simulate position creation
                position_id = f"wide_{int(time.time())}"
                position = WideRangePosition(
                    position_id=position_id,
                    entry_time=time.time(),
                    entry_price=center_price,
                    lower_price=lower_price,
                    upper_price=upper_price,
                    lower_bin_id=lower_bin_id,
                    upper_bin_id=upper_bin_id,
                    total_bins=total_bins,
                    capital_deployed=capital_to_deploy,
                    strategy_config=opportunity.channel_analysis.quality.value,
                    channel_quality=opportunity.channel_analysis.quality.value,
                    expected_hold_time_hours=opportunity.expected_hold_time,
                    initial_mnt_amount=mnt_amount,
                    initial_usdt_amount=usdt_amount,
                    last_update_time=time.time()
                )
                
                return {
                    "success": True,
                    "position": position.to_dict(),
                    "simulation": True,
                    "message": f"Simulated wide-range position creation: {total_bins} bins, ${capital_to_deploy:.2f}"
                }
            
            else:
                # Actual LP creation using existing LP manager
                logger.info("🚀 Creating actual wide-range LP position...")
                
                # Use LP manager to create position
                lp_result = await self._execute_lp_creation(
                    mnt_amount=mnt_amount,
                    usdt_amount=usdt_amount,
                    lower_bin_id=lower_bin_id,
                    upper_bin_id=upper_bin_id
                )
                
                if lp_result['success']:
                    # Create and store position record
                    position_id = lp_result.get('transaction_hash', f"wide_{int(time.time())}")
                    position = WideRangePosition(
                        position_id=position_id,
                        entry_time=time.time(),
                        entry_price=center_price,
                        lower_price=lower_price,
                        upper_price=upper_price,
                        lower_bin_id=lower_bin_id,
                        upper_bin_id=upper_bin_id,
                        total_bins=total_bins,
                        capital_deployed=capital_to_deploy,
                        strategy_config=opportunity.channel_analysis.quality.value,
                        channel_quality=opportunity.channel_analysis.quality.value,
                        expected_hold_time_hours=opportunity.expected_hold_time,
                        initial_mnt_amount=mnt_amount,
                        initial_usdt_amount=usdt_amount,
                        last_update_time=time.time()
                    )
                    
                    # Store position
                    await self._store_position(position)
                    
                    return {
                        "success": True,
                        "position": position.to_dict(),
                        "transaction_hash": lp_result.get('transaction_hash'),
                        "message": f"Created wide-range LP: {total_bins} bins, ${capital_to_deploy:.2f}"
                    }
                else:
                    return {
                        "success": False,
                        "error": lp_result.get('error', 'LP creation failed'),
                        "message": "Failed to create wide-range position"
                    }
                    
        except Exception as e:
            logger.error(f"❌ Wide-range position creation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Position creation error: {e}"
            }
    
    async def exit_wide_range_positions(self, 
                                      position_ids: Optional[List[str]] = None,
                                      dry_run: bool = True) -> Dict[str, Any]:
        """
        Exit wide-range LP positions.
        
        Args:
            position_ids: Specific position IDs to exit (None = all active)
            dry_run: If True, simulate the exit without actual execution
            
        Returns:
            Dictionary with exit results
        """
        if not self._can_execute and not dry_run:
            raise RuntimeError("LP execution components not available")
            
        logger.info(f"📤 Exiting wide-range positions {'(DRY RUN)' if dry_run else '(LIVE)'}")
        
        try:
            # Get active positions
            active_positions = await self._get_active_positions()
            
            if position_ids:
                positions_to_exit = [p for p in active_positions if p.position_id in position_ids]
            else:
                positions_to_exit = active_positions
                
            if not positions_to_exit:
                return {
                    "success": True,
                    "positions_exited": 0,
                    "message": "No positions to exit"
                }
            
            exit_results = []
            total_capital_recovered = 0.0
            
            for position in positions_to_exit:
                logger.info(f"   Exiting position {position.position_id[:8]}... ({position.total_bins} bins)")
                
                if dry_run:
                    # Simulate exit
                    simulated_capital = position.capital_deployed * 1.02  # Assume 2% gain
                    total_capital_recovered += simulated_capital
                    
                    exit_results.append({
                        "position_id": position.position_id,
                        "success": True,
                        "capital_recovered": simulated_capital,
                        "simulation": True
                    })
                    
                else:
                    # Actual exit
                    exit_result = await self._execute_lp_exit(position)
                    exit_results.append(exit_result)
                    
                    if exit_result['success']:
                        total_capital_recovered += exit_result.get('capital_recovered', 0)
                        # Mark position as closed
                        await self._close_position(position.position_id)
            
            return {
                "success": True,
                "positions_exited": len(positions_to_exit),
                "total_capital_recovered": total_capital_recovered,
                "exit_results": exit_results,
                "message": f"Exited {len(positions_to_exit)} wide-range positions"
            }
            
        except Exception as e:
            logger.error(f"❌ Wide-range position exit failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": f"Position exit error: {e}"
            }
    
    def _price_to_bin_id(self, price: float, center_price: float, pool_step: int) -> int:
        """Convert price to bin ID relative to center price."""
        import math
        # Simplified conversion - actual implementation would use pool-specific math
        price_ratio = price / center_price
        log_ratio = math.log(price_ratio)
        bin_offset = int(log_ratio / (pool_step * 0.0001))  # Approximate conversion
        return bin_offset
    
    async def _execute_lp_creation(self,
                                 mnt_amount: float,
                                 usdt_amount: float,
                                 lower_bin_id: int,
                                 upper_bin_id: int) -> Dict[str, Any]:
        """Execute LP creation via LPService."""
        bin_count = upper_bin_id - lower_bin_id + 1
        try:
            logger.info(f"Creating wide LP position: {mnt_amount:.2f} MNT + {usdt_amount:.2f} USDT ({bin_count} bins)")

            results = self.lp_service.create_position(
                amount_wmnt=Decimal(str(mnt_amount)),
                amount_usdt=Decimal(str(usdt_amount)),
                bin_count=bin_count,
                distribution_params=self.settings.get_wide_distribution_params(),
                strategy_type="wide",
                dry_run=False,
            )

            tx_hash = None
            for r in results:
                if r.action == "add_liquidity" and r.tx_hash:
                    tx_hash = r.tx_hash
                    break

            if tx_hash:
                logger.info(f"Wide LP created: {bin_count} bins (tx: {tx_hash[:10]}...)")
                return {"success": True, "transaction_hash": tx_hash, "message": f"LP created: {bin_count} bins"}
            else:
                return {"success": False, "error": "LP creation failed - no successful transaction found"}

        except Exception as e:
            logger.error(f"LP creation failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def _execute_lp_exit(self, position: WideRangePosition) -> Dict[str, Any]:
        """Execute LP exit via LPService."""
        logger.info(f"Removing LP position {position.position_id[:8]}... ({position.total_bins} bins)")
        try:
            results = self.lp_service.remove_position(dry_run=False)
            tx_hash = None
            for r in results:
                if r.action == "remove_liquidity" and r.tx_hash:
                    tx_hash = r.tx_hash
                    break
            if tx_hash:
                return {
                    "success": True,
                    "position_id": position.position_id,
                    "capital_recovered": position.capital_deployed,
                    "transaction_hash": tx_hash,
                    "message": f"LP removed: {position.total_bins} bins",
                }
            else:
                return {
                    "success": False,
                    "position_id": position.position_id,
                    "error": "LP removal failed - no successful transaction found",
                }
        except Exception as e:
            logger.error(f"LP exit failed: {e}")
            return {"success": False, "position_id": position.position_id, "error": str(e)}
    
    async def _store_position(self, position: WideRangePosition) -> None:
        """No-op: LPService.create_position() already registers via _register_position hook."""
        logger.debug(f"Position {position.position_id[:8]} tracked via LP registry")

    async def _close_position(self, position_id: str) -> None:
        """No-op: LPService.remove_position() already deregisters via _deregister_positions hook."""
        logger.debug(f"Position {position_id[:8]} deregistered via LP registry")

    async def _get_active_positions(self) -> List[WideRangePosition]:
        """Get active wide-range positions from LP registry."""
        try:
            from ..core.wallet import load_wallet
            wallet = load_wallet(self.settings)
            registry_positions = self.lp_service.get_tracked_positions(
                wallet.address, strategy_type="wide"
            )
            result = []
            for rp in registry_positions:
                result.append(WideRangePosition(
                    position_id=rp.id,
                    entry_time=datetime.fromisoformat(rp.created_at).timestamp() if hasattr(rp, 'created_at') else time.time(),
                    entry_price=0.0,
                    lower_price=0.0,
                    upper_price=0.0,
                    lower_bin_id=rp.min_bin,
                    upper_bin_id=rp.max_bin,
                    total_bins=rp.bin_count,
                    capital_deployed=rp.initial_value_usdt,
                    strategy_config=rp.distribution_shape or "wide",
                    channel_quality="",
                    expected_hold_time_hours=0,
                    initial_mnt_amount=rp.initial_mnt,
                    initial_usdt_amount=rp.initial_usdt,
                ))
            return result
        except Exception as e:
            logger.error(f"Failed to get positions from registry: {e}")
            return []
    
    def _should_exit_position(self,
                            position: WideRangePosition,
                            channel_analysis: ChannelAnalysis,
                            position_age_hours: float) -> Optional[str]:
        """Determine if a position should be exited."""
        
        # Time-based exit
        if position_age_hours > self.strategy_params['max_hold_time_hours']:
            return f"Maximum hold time exceeded ({position_age_hours:.1f}h)"
        
        # Channel quality degradation
        if channel_analysis.quality_score < 0.3:
            return f"Channel quality degraded ({channel_analysis.quality.value})"
        
        # Strong trending market
        if not channel_analysis.is_ranging and abs(channel_analysis.trend_slope) > 0.002:
            return f"Strong trend detected (slope: {channel_analysis.trend_slope:.4f})"
        
        # Price approaching bounds (risk of going out of range)
        if channel_analysis.price_position < 0.1 or channel_analysis.price_position > 0.9:
            return f"Price near channel bounds (position: {channel_analysis.price_position:.2f})"
        
        # Channel became too narrow
        if channel_analysis.bounds.width_pct < 0.8:
            return f"Channel too narrow ({channel_analysis.bounds.width_pct:.1f}%)"
        
        # No exit criteria met
        return None
    
    def _needs_position_adjustment(self,
                                 position: WideRangePosition,
                                 channel_analysis: ChannelAnalysis) -> Optional[str]:
        """Check if position needs adjustment."""
        
        # Channel bounds significantly changed
        current_width = channel_analysis.bounds.width_pct
        position_range_pct = ((position.upper_price - position.lower_price) / position.entry_price) * 100
        
        width_change = abs(current_width - position_range_pct) / position_range_pct
        
        if width_change > 0.3:  # 30% change in effective range
            return f"Channel bounds changed significantly ({width_change:.1%})"
        
        # Position age suggests rebalancing
        current_time = time.time()
        position_age_hours = (current_time - position.entry_time) / 3600
        
        if position_age_hours > 48 and channel_analysis.quality_score > 0.7:  # 48h+ with good quality
            return f"Position aged {position_age_hours:.1f}h with good market conditions"
        
        return None
    
    def _get_wait_reasons(self, channel_analysis: ChannelAnalysis) -> str:
        """Generate specific reasons for waiting."""
        
        reasons = []
        
        if channel_analysis.quality_score <= 0.6:
            reasons.append(f"low quality ({channel_analysis.quality.value})")
        
        if not channel_analysis.is_ranging:
            reasons.append("trending market")
        
        width_pct = channel_analysis.bounds.width_pct
        if width_pct < 1.0:
            reasons.append(f"channel too narrow ({width_pct:.1f}%)")
        elif width_pct > 10.0:
            reasons.append(f"channel too wide ({width_pct:.1f}%)")
        
        if channel_analysis.price_position < 0.1 or channel_analysis.price_position > 0.9:
            reasons.append("price near channel bounds")
        
        return ", ".join(reasons) if reasons else "suboptimal conditions"
    
    def _load_current_positions(self) -> List[WideRangePosition]:
        """Load current wide-range positions from storage."""
        
        try:
            if not self.positions_file.exists():
                return []
            
            with open(self.positions_file, 'r') as f:
                data = json.load(f)
            
            return [WideRangePosition.from_dict(pos_data) for pos_data in data.get('positions', [])]
        
        except Exception as e:
            logger.warning(f"Failed to load positions: {e}")
            return []
    
    def _save_positions(self, positions: List[WideRangePosition]) -> None:
        """Save positions to storage."""
        
        try:
            data = {
                'last_updated': time.time(),
                'positions': [pos.to_dict() for pos in positions]
            }
            
            with open(self.positions_file, 'w') as f:
                json.dump(data, f, indent=2)
        
        except Exception as e:
            logger.error(f"Failed to save positions: {e}")
    
    def _get_default_opportunity(self) -> WideRangeOpportunity:
        """Return safe default opportunity when analysis fails."""
        
        return WideRangeOpportunity(
            action="WAIT",
            confidence=0.0,
            channel_analysis=self.keltner_analyzer._get_default_analysis(),
            optimal_range={},
            capital_allocation=0.0,
            expected_hold_time=0,
            reasoning="Analysis failed - waiting for stable conditions",
            risk_assessment={'overall_risk': 'HIGH', 'recommendation': 'AVOID'}
        )
    
    def add_position(self, position: WideRangePosition) -> None:
        """Add a new wide-range position to tracking."""
        
        positions = self._load_current_positions()
        positions.append(position)
        self._save_positions(positions)
        
        logger.info(f"Added wide-range position: {position.position_id}")
    
    def remove_position(self, position_id: str) -> Optional[WideRangePosition]:
        """Remove and return a position from tracking."""
        
        positions = self._load_current_positions()
        
        for i, pos in enumerate(positions):
            if pos.position_id == position_id:
                removed_position = positions.pop(i)
                self._save_positions(positions)
                logger.info(f"Removed wide-range position: {position_id}")
                return removed_position
        
        logger.warning(f"Position not found for removal: {position_id}")
        return None
    
    def get_position_summary(self) -> Dict[str, Any]:
        """Get summary of all wide-range positions."""
        
        positions = self._load_current_positions()
        current_time = time.time()
        
        if not positions:
            return {
                'active_positions': 0,
                'total_capital_deployed': 0.0,
                'average_age_hours': 0.0,
                'positions': []
            }
        
        total_capital = sum(pos.capital_deployed for pos in positions)
        total_age_hours = sum((current_time - pos.entry_time) / 3600 for pos in positions)
        average_age = total_age_hours / len(positions)
        
        return {
            'active_positions': len(positions),
            'total_capital_deployed': total_capital,
            'average_age_hours': average_age,
            'positions': [
                {
                    'id': pos.position_id,
                    'age_hours': (current_time - pos.entry_time) / 3600,
                    'capital': pos.capital_deployed,
                    'range_pct': ((pos.upper_price - pos.lower_price) / pos.entry_price) * 100,
                    'strategy': pos.strategy_config
                }
                for pos in positions
            ]
        }