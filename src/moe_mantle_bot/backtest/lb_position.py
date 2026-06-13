"""Pure Liquidity Book position emulator (no blockchain).

Models a set of bins around an active price. Uses the standard memoryless LB
inventory rule: a bin at price ``p_b`` holds only MNT (token X) when the market
price ``P`` is below ``p_b``, and only quote (token Y) when ``P`` is above
``p_b``. Value is continuous at ``p_b`` (``y_capacity = x_capacity * p_b``), so
inventory and impermanent loss are computed exactly from the price path.

Each bin is described by its **x-capacity** ``X_b`` — the MNT amount it holds
when fully on the MNT side. Quote-side value once converted is ``X_b * p_b``
(frozen in quote terms, the limit-order fill).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

REAL_ID_SHIFT = 2**23  # LB reference bin id (2^23)


def price_at_bin(bin_id: int, bin_step: int, dx: int, dy: int) -> float:
    """Human price (quote per MNT) at a bin id. Float mirror of utils.price_from_bin_id."""
    ratio = 1.0 + bin_step / 10_000.0
    return ratio ** (bin_id - REAL_ID_SHIFT) * (10.0 ** (dx - dy))


def bin_id_from_price(price: float, bin_step: int, dx: int, dy: int) -> int:
    """Inverse of price_at_bin — the active bin id for a given price."""
    ratio = 1.0 + bin_step / 10_000.0
    adjusted = price / (10.0 ** (dx - dy))
    return round(REAL_ID_SHIFT + math.log(adjusted) / math.log(ratio))


@dataclass
class LBPosition:
    """A live position: per-bin MNT x-capacity, plus the entry basket for IL."""

    x_capacity: dict[int, float]   # bin_id -> MNT amount when on the MNT side
    bin_step: int
    dx: int
    dy: int
    entry_mnt: float               # total MNT in the entry basket (for HODL/IL)
    entry_quote: float             # total quote in the entry basket
    entry_price: float

    @property
    def min_bin(self) -> int:
        return min(self.x_capacity)

    @property
    def max_bin(self) -> int:
        return max(self.x_capacity)

    @property
    def bin_count(self) -> int:
        return len(self.x_capacity)

    def _price_at(self, bin_id: int) -> float:
        return price_at_bin(bin_id, self.bin_step, self.dx, self.dy)

    def in_range(self, price: float) -> bool:
        active = bin_id_from_price(price, self.bin_step, self.dx, self.dy)
        return self.min_bin <= active <= self.max_bin

    def inventory(self, price: float) -> tuple[float, float]:
        """(mnt, quote) currently held at the given price."""
        mnt = 0.0
        quote = 0.0
        for bin_id, xcap in self.x_capacity.items():
            p_b = self._price_at(bin_id)
            if p_b > price:        # price below bin → still MNT
                mnt += xcap
            else:                  # price at/above bin → converted to quote
                quote += xcap * p_b
        return mnt, quote

    def value(self, price: float) -> float:
        mnt, quote = self.inventory(price)
        return mnt * price + quote

    def active_bin_value(self, price: float) -> float:
        """USD value the position holds in the current active bin (for fee share)."""
        active = bin_id_from_price(price, self.bin_step, self.dx, self.dy)
        xcap = self.x_capacity.get(active)
        if xcap is None:
            return 0.0
        p_b = self._price_at(active)
        # Active bin is the conversion bin; value ~ its MNT side marked at price.
        return xcap * price if p_b >= price else xcap * p_b

    def hodl_value(self, price: float) -> float:
        return self.entry_mnt * price + self.entry_quote


def build_position(
    *,
    center_price: float,
    bin_count: int,
    capital_usd: float,
    quote_usd_target: float,
    bin_step: int,
    dx: int,
    dy: int,
    weights: list[float] | None = None,
) -> LBPosition:
    """Construct a position centered on ``center_price``.

    Quote (USDT) is placed in bins below the active bin; MNT in the active bin
    and above — mirroring how the live bot deploys. ``quote_usd_target`` caps
    the quote side; the remainder goes to MNT.
    """
    active = bin_id_from_price(center_price, bin_step, dx, dy)
    half = bin_count // 2
    bins = list(range(active - half, active - half + bin_count))

    quote_bins = [b for b in bins if b < active]
    mnt_bins = [b for b in bins if b >= active]

    quote_usd = min(quote_usd_target, capital_usd)
    mnt_usd = max(0.0, capital_usd - quote_usd)

    def norm(ids: list[int]) -> list[float]:
        n = len(ids)
        if n == 0:
            return []
        if weights and len(weights) >= n:
            w = weights[:n]
        else:
            w = [1.0] * n
        s = sum(w) or 1.0
        return [x / s for x in w]

    x_capacity: dict[int, float] = {}
    entry_quote = 0.0
    entry_mnt = 0.0

    for b, frac in zip(quote_bins, norm(quote_bins)):
        usd = quote_usd * frac
        p_b = price_at_bin(b, bin_step, dx, dy)
        # quote-side bin holds `usd` in quote; x-capacity = usd / p_b
        x_capacity[b] = usd / p_b
        entry_quote += usd

    for b, frac in zip(mnt_bins, norm(mnt_bins)):
        usd = mnt_usd * frac
        mnt_tokens = usd / center_price
        x_capacity[b] = mnt_tokens
        entry_mnt += mnt_tokens

    return LBPosition(
        x_capacity=x_capacity,
        bin_step=bin_step,
        dx=dx,
        dy=dy,
        entry_mnt=entry_mnt,
        entry_quote=entry_quote,
        entry_price=center_price,
    )
