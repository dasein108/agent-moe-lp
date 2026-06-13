"""
LP Registry System for tracking narrow and wide positions separately with JSON persistence.
Registry is the source of truth - any onchain positions not in registry are removed.
"""

import json
import shutil
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Any
from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class LPPosition:
    """Represents a single LP position (narrow or wide)."""
    id: str
    strategy_type: str  # "narrow" or "wide"
    min_bin: int
    max_bin: int
    bin_count: int
    created_at: str
    created_tx: str
    initial_mnt: float
    initial_usdt: float
    initial_value_usdt: float
    distribution_shape: Optional[str] = None
    keltner_config: Optional[str] = None
    exited_at: Optional[str] = None
    exit_tx: Optional[str] = None
    final_mnt: Optional[float] = None
    final_usdt: Optional[float] = None
    fees_earned_usdt: Optional[float] = None
    bin_amounts: Optional[Dict[int, int]] = None  # {bin_id: lb_token_amount_raw} for partial removal

    @property
    def bins(self) -> Tuple[int, int]:
        """Return bins as tuple (min, max)."""
        return (self.min_bin, self.max_bin)
    
    @property
    def is_active(self) -> bool:
        """Check if position is still active."""
        return self.exited_at is None
    
    def get_all_bins(self) -> List[int]:
        """Get all bin IDs in this position."""
        return list(range(self.min_bin, self.max_bin + 1))
    
    def overlaps_with(self, min_bin: int, max_bin: int) -> bool:
        """Check if this position overlaps with given bin range."""
        return not (max_bin < self.min_bin or min_bin > self.max_bin)


@dataclass
class ReconciliationResult:
    """Result of reconciliation between registry and onchain state."""
    action: str  # "SYNCED", "REMOVE_UNAUTHORIZED", "ERROR"
    bins_to_remove: List[int] = None
    reason: str = ""
    missing_in_registry: List[int] = None
    missing_onchain: List[int] = None


class LPRegistry:
    """
    Registry for tracking LP positions with JSON persistence.
    Registry is the source of truth - unauthorized onchain positions are removed.
    """
    
    def __init__(self, wallet_address: str, data_dir: Path = None):
        """Initialize LP registry for a wallet."""
        self.wallet_address = wallet_address.lower()
        self.data_dir = Path(data_dir or "data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.registry_file = self.data_dir / "lp_registry.json"
        self.backup_file = self.data_dir / "lp_registry_backup.json"
        self.history_file = self.data_dir / "lp_history.jsonl"
        
        self.positions: Dict[str, List[LPPosition]] = {
            "narrow": [],
            "wide": []
        }
        
        self.load()
    
    def load(self) -> None:
        """Load registry from JSON file."""
        if not self.registry_file.exists():
            logger.info(f"📝 Creating new LP registry for {self.wallet_address}")
            self.save()
            return
        
        try:
            with open(self.registry_file, 'r') as f:
                data = json.load(f)
            
            if data.get("wallet_address", "").lower() != self.wallet_address:
                logger.warning(f"⚠️ Registry wallet mismatch: {data.get('wallet_address')} != {self.wallet_address}")
                self.save()
                return
            
            # Load positions
            self.positions = {
                "narrow": [],
                "wide": []
            }
            
            for strategy_type in ["narrow", "wide"]:
                for pos_data in data.get("positions", {}).get(strategy_type, []):
                    # JSON serializes int keys as strings — convert back
                    raw_bin_amounts = pos_data.get("bin_amounts")
                    if raw_bin_amounts is not None:
                        pos_data["bin_amounts"] = {int(k): v for k, v in raw_bin_amounts.items()}
                    position = LPPosition(**pos_data)
                    self.positions[strategy_type].append(position)
            
            logger.info(f"📚 Loaded {len(self.get_all_active_positions())} active positions from registry")
            
        except Exception as e:
            logger.error(f"❌ Failed to load registry: {e}")
            # Backup corrupted file and create new
            if self.registry_file.exists():
                backup_path = self.data_dir / f"lp_registry_corrupted_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                shutil.copy(self.registry_file, backup_path)
                logger.info(f"💾 Backed up corrupted registry to {backup_path}")
            self.save()
    
    def save(self) -> None:
        """Save registry to JSON file with backup."""
        # Backup existing file
        if self.registry_file.exists():
            shutil.copy(self.registry_file, self.backup_file)
        
        # Prepare data
        data = {
            "wallet_address": self.wallet_address,
            "last_updated": datetime.now().isoformat(),
            "positions": {
                "narrow": [asdict(p) for p in self.positions["narrow"]],
                "wide": [asdict(p) for p in self.positions["wide"]]
            },
            "statistics": self._calculate_statistics()
        }
        
        # Save to file
        with open(self.registry_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.debug(f"💾 Saved registry with {len(self.get_all_active_positions())} active positions")
    
    def _calculate_statistics(self) -> Dict[str, Any]:
        """Calculate registry statistics."""
        active_positions = self.get_all_active_positions()
        
        if not active_positions:
            return {
                "total_positions": 0,
                "total_bins_covered": 0,
                "oldest_position_age_hours": 0.0,
                "total_initial_value_usdt": 0.0
            }
        
        # Calculate total bins
        all_bins = set()
        for pos in active_positions:
            all_bins.update(pos.get_all_bins())
        
        # Find oldest position
        oldest_created = min(pos.created_at for pos in active_positions)
        oldest_age_hours = (datetime.now() - datetime.fromisoformat(oldest_created)).total_seconds() / 3600
        
        # Sum initial values
        total_value = sum(pos.initial_value_usdt for pos in active_positions)
        
        return {
            "total_positions": len(active_positions),
            "narrow_positions": len([p for p in active_positions if p.strategy_type == "narrow"]),
            "wide_positions": len([p for p in active_positions if p.strategy_type == "wide"]),
            "total_bins_covered": len(all_bins),
            "oldest_position_age_hours": round(oldest_age_hours, 2),
            "total_initial_value_usdt": round(total_value, 2)
        }
    
    def add_position(self, strategy_type: str, min_bin: int, max_bin: int,
                    tx_hash: str, initial_mnt: float, initial_usdt: float,
                    distribution_shape: Optional[str] = None,
                    keltner_config: Optional[str] = None,
                    bin_amounts: Optional[Dict[int, int]] = None) -> LPPosition:
        """Add a new position or merge into an existing active position of the same type.

        Liquidity Book merges bin balances on-chain for the same wallet, so the registry
        should track one aggregated position per strategy type. When an active
        position of the same type exists, this method merges bin_amounts, expands
        the bin range, and accumulates totals instead of creating a duplicate.
        """
        # Check for existing active position of the same strategy type
        existing = [p for p in self.positions[strategy_type] if p.is_active]
        if existing:
            pos = existing[0]
            # Merge bin_amounts
            if bin_amounts:
                if pos.bin_amounts is None:
                    pos.bin_amounts = {}
                for bid, amount in bin_amounts.items():
                    bid_key = int(bid) if not isinstance(bid, int) else bid
                    pos.bin_amounts[str(bid_key)] = (
                        pos.bin_amounts.get(str(bid_key), 0) + amount
                    )
            # Expand bin range
            pos.min_bin = min(pos.min_bin, min_bin)
            pos.max_bin = max(pos.max_bin, max_bin)
            pos.bin_count = pos.max_bin - pos.min_bin + 1
            # Accumulate capital
            pos.initial_mnt = round(pos.initial_mnt + initial_mnt, 4)
            pos.initial_usdt = round(pos.initial_usdt + initial_usdt, 4)
            mnt_price = initial_usdt / initial_mnt if initial_mnt > 0 else 0
            pos.initial_value_usdt = round(
                pos.initial_value_usdt + initial_usdt + (initial_mnt * mnt_price), 4
            )
            self.save()
            self._log_to_history("MERGE", pos)
            logger.info(
                f"🔀 Merged into {strategy_type} position {pos.id} "
                f"(bins {pos.min_bin}-{pos.max_bin}, "
                f"total {pos.initial_mnt:.1f} MNT + ${pos.initial_usdt:.2f} USDT)"
            )
            return pos

        # No existing position — create new
        timestamp = int(datetime.now().timestamp())
        position_id = f"{strategy_type}_{timestamp}_{min_bin}"

        mnt_price = initial_usdt / initial_mnt if initial_mnt > 0 else 0
        initial_value_usdt = initial_usdt + (initial_mnt * mnt_price)

        position = LPPosition(
            id=position_id,
            strategy_type=strategy_type,
            min_bin=min_bin,
            max_bin=max_bin,
            bin_count=max_bin - min_bin + 1,
            created_at=datetime.now().isoformat(),
            created_tx=tx_hash,
            initial_mnt=round(initial_mnt, 4),
            initial_usdt=round(initial_usdt, 4),
            initial_value_usdt=round(initial_value_usdt, 4),
            distribution_shape=distribution_shape,
            keltner_config=keltner_config,
            bin_amounts=bin_amounts,
        )

        self.positions[strategy_type].append(position)
        self.save()
        self._log_to_history("ADD", position)

        logger.info(f"✅ Added {strategy_type} position {position_id} (bins {min_bin}-{max_bin})")

        return position
    
    def remove_position(self, position_id: str, tx_hash: str, 
                       final_mnt: float = 0, final_usdt: float = 0,
                       fees_earned_usdt: float = 0) -> bool:
        """Mark a position as exited."""
        position = self.find_position_by_id(position_id)
        
        if not position:
            logger.warning(f"⚠️ Position {position_id} not found in registry")
            return False
        
        if not position.is_active:
            logger.warning(f"⚠️ Position {position_id} already exited")
            return False
        
        # Update position with exit info
        position.exited_at = datetime.now().isoformat()
        position.exit_tx = tx_hash
        position.final_mnt = round(final_mnt, 4)
        position.final_usdt = round(final_usdt, 4)
        position.fees_earned_usdt = round(fees_earned_usdt, 4)
        
        # Save and log
        self.save()
        self._log_to_history("REMOVE", position)
        
        logger.info(f"✅ Removed position {position_id} (recovered {final_mnt:.2f} MNT + {final_usdt:.2f} USDT)")
        
        return True
    
    def find_position_by_id(self, position_id: str) -> Optional[LPPosition]:
        """Find a position by its ID."""
        for strategy_type in ["narrow", "wide"]:
            for position in self.positions[strategy_type]:
                if position.id == position_id:
                    return position
        return None
    
    def find_positions_by_bins(self, bins: List[int]) -> List[LPPosition]:
        """Find all positions that contain any of the given bins."""
        bin_set = set(bins)
        affected = []
        
        for position in self.get_all_active_positions():
            position_bins = set(position.get_all_bins())
            if position_bins.intersection(bin_set):
                affected.append(position)
        
        return affected
    
    def get_all_active_positions(self) -> List[LPPosition]:
        """Get all active positions (narrow and wide)."""
        active = []
        for strategy_type in ["narrow", "wide"]:
            active.extend([p for p in self.positions[strategy_type] if p.is_active])
        return active
    
    def get_narrow_positions(self) -> List[LPPosition]:
        """Get active narrow positions."""
        return [p for p in self.positions["narrow"] if p.is_active]
    
    def get_wide_positions(self) -> List[LPPosition]:
        """Get active wide positions."""
        return [p for p in self.positions["wide"] if p.is_active]
    
    def get_all_active_bins(self) -> List[int]:
        """Get all bin IDs from active positions."""
        all_bins = []
        for position in self.get_all_active_positions():
            all_bins.extend(position.get_all_bins())
        return sorted(list(set(all_bins)))
    
    def can_create_position(self, strategy_type: str, min_bin: int, max_bin: int) -> Tuple[bool, str]:
        """
        Check if new position would overlap with existing positions of same type.
        NO OVERLAPS ALLOWED between positions of same type.
        """
        for position in self.positions[strategy_type]:
            if not position.is_active:
                continue
            
            if position.overlaps_with(min_bin, max_bin):
                return False, f"Overlaps with {position.id} (bins {position.min_bin}-{position.max_bin})"
        
        return True, "No overlap"
    
    def reconcile_with_onchain(self, onchain_bins: List[int]) -> ReconciliationResult:
        """
        Strict reconciliation: Registry is source of truth.
        - Bins in registry but not onchain: Error (should not happen)
        - Bins onchain but not in registry: REMOVE from blockchain
        """
        registry_bins = self.get_all_active_bins()
        onchain_set = set(onchain_bins)
        registry_set = set(registry_bins)
        
        # Bins that shouldn't exist
        unauthorized_bins = onchain_set - registry_set
        
        # Bins missing onchain (should not happen in normal operation)
        missing_onchain = registry_set - onchain_set
        
        if unauthorized_bins:
            logger.warning(f"⚠️ Found {len(unauthorized_bins)} unauthorized bins onchain")
            logger.warning(f"   Unauthorized bins: {sorted(list(unauthorized_bins))[:10]}...")
            
            return ReconciliationResult(
                action="REMOVE_UNAUTHORIZED",
                bins_to_remove=sorted(list(unauthorized_bins)),
                reason=f"Found {len(unauthorized_bins)} bins onchain not in registry"
            )
        
        if missing_onchain:
            logger.error(f"❌ Registry has {len(missing_onchain)} bins not found onchain")
            logger.error(f"   Missing bins: {sorted(list(missing_onchain))[:10]}...")
            
            return ReconciliationResult(
                action="ERROR",
                missing_onchain=sorted(list(missing_onchain)),
                reason=f"Registry has {len(missing_onchain)} bins not found onchain"
            )
        
        logger.info(f"✅ Registry synced with onchain: {len(registry_bins)} bins match")
        
        return ReconciliationResult(
            action="SYNCED",
            reason=f"All {len(registry_bins)} bins synchronized"
        )
    
    def _log_to_history(self, action: str, position: LPPosition) -> None:
        """Log position changes to history file."""
        try:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "action": action,
                "position": asdict(position)
            }
            
            with open(self.history_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as e:
            logger.error(f"Failed to log history: {e}")
    
    def get_position_summary(self) -> Dict[str, Any]:
        """Get summary of current positions."""
        narrow_positions = self.get_narrow_positions()
        wide_positions = self.get_wide_positions()
        
        return {
            "wallet": self.wallet_address,
            "narrow": {
                "count": len(narrow_positions),
                "positions": [
                    {
                        "id": p.id,
                        "bins": f"{p.min_bin}-{p.max_bin}",
                        "bin_count": p.bin_count,
                        "value": p.initial_value_usdt,
                        "age_hours": round((datetime.now() - datetime.fromisoformat(p.created_at)).total_seconds() / 3600, 1)
                    }
                    for p in narrow_positions
                ]
            },
            "wide": {
                "count": len(wide_positions),
                "positions": [
                    {
                        "id": p.id,
                        "bins": f"{p.min_bin}-{p.max_bin}",
                        "bin_count": p.bin_count,
                        "value": p.initial_value_usdt,
                        "age_hours": round((datetime.now() - datetime.fromisoformat(p.created_at)).total_seconds() / 3600, 1)
                    }
                    for p in wide_positions
                ]
            },
            "statistics": self._calculate_statistics()
        }


class CleanSlateRedeployment:
    """
    Efficient redeployment by removing everything and recreating positions.
    """
    
    def __init__(self, registry: LPRegistry, settings: Any):
        """Initialize clean slate redeployment manager."""
        self.registry = registry
        self.settings = settings
        self.logger = get_logger(__name__)
    
    def needs_redeployment(self, active_bin: int, price_range: Tuple[float, float]) -> bool:
        """Check if any position needs redeployment."""
        narrow_positions = self.registry.get_narrow_positions()
        wide_positions = self.registry.get_wide_positions()
        
        # Check narrow positions (strict in-range requirement)
        for narrow in narrow_positions:
            if not (narrow.min_bin <= active_bin <= narrow.max_bin):
                self.logger.info(f"🎯 Narrow position {narrow.id} out of range (active bin: {active_bin})")
                return True
        
        # Check wide positions (looser tolerance)
        for wide in wide_positions:
            tolerance = (wide.max_bin - wide.min_bin) * 0.2  # 20% tolerance
            if not (wide.min_bin - tolerance <= active_bin <= wide.max_bin + tolerance):
                self.logger.info(f"📊 Wide position {wide.id} significantly out of range")
                return True
        
        return False
    
    def calculate_optimal_allocation(self, total_mnt: Decimal, total_usdt: Decimal, 
                                    market_conditions: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate optimal allocation between narrow and wide strategies based on market conditions.
        """
        volatility = market_conditions.get('volatility', 'normal')
        trending = market_conditions.get('trending', False)
        active_bin = market_conditions['active_bin']
        
        if volatility == 'high':
            # High volatility: More narrow, less wide
            narrow_allocation = Decimal('0.7')
            wide_allocation = Decimal('0.3')
            narrow_bins = 5  # Tighter range
            wide_bins = 150  # Wider safety net
        elif trending:
            # Trending: Skip wide, all narrow
            narrow_allocation = Decimal('1.0')
            wide_allocation = Decimal('0.0')
            narrow_bins = 10
            wide_bins = 0
        else:
            # Normal/Ranging: Balanced approach
            narrow_allocation = Decimal('0.4')
            wide_allocation = Decimal('0.6')
            narrow_bins = 10
            wide_bins = 100
        
        allocation = {
            'narrow': {
                'mnt': float(total_mnt * narrow_allocation),
                'usdt': float(total_usdt * narrow_allocation),
                'min_bin': active_bin - narrow_bins,
                'max_bin': active_bin + narrow_bins,
                'bin_count': narrow_bins * 2,
                'strategy': 'concentrated'
            } if narrow_allocation > 0 else None
        }
        
        if wide_allocation > 0:
            allocation['wide'] = {
                'mnt': float(total_mnt * wide_allocation),
                'usdt': float(total_usdt * wide_allocation),
                'min_bin': active_bin - wide_bins // 2,
                'max_bin': active_bin + wide_bins // 2,
                'bin_count': wide_bins,
                'strategy': 'range'
            }
        
        self.logger.info(f"📊 Optimal allocation: Narrow {float(narrow_allocation)*100:.0f}%, Wide {float(wide_allocation)*100:.0f}%")
        
        return allocation
    
    def estimate_gas_cost(self, bins_to_remove: int, bins_to_add: int) -> Dict[str, int]:
        """Estimate gas costs for clean slate redeployment."""
        # Rough gas estimates based on bin counts
        gas_per_removal_batch = 150000  # ~50 bins per tx
        gas_per_add_batch = 250000  # ~100 bins per tx
        
        removal_txs = (bins_to_remove + 49) // 50  # Ceiling division
        add_txs = (bins_to_add + 99) // 100
        
        return {
            'removal_gas': removal_txs * gas_per_removal_batch,
            'add_gas': add_txs * gas_per_add_batch,
            'total_gas': (removal_txs * gas_per_removal_batch) + (add_txs * gas_per_add_batch),
            'removal_txs': removal_txs,
            'add_txs': add_txs,
            'total_txs': removal_txs + add_txs
        }