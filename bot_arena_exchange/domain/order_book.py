from __future__ import annotations

import asyncio
import heapq
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class Order:
    order_id: str
    side: str
    price: int
    quantity: int
    trader_id: str
    timestamp: int
    symbol: str = "UNKNOWN"
    venue: str = "UNKNOWN"
    tif: str = "GTC"  # Good Till Cancelled
    remaining: int = field(init=False)
    status: str = field(default="open")

    def __post_init__(self) -> None:
        self.remaining = self.quantity


class WashTradeError(Exception):
    """Raised when an incoming order would cross a resting order from the same trader.

    Callers must catch this and decide how to handle it based on account type:
    - System accounts: log warning, reject order (no disconnection)
    - User accounts: reject order + disconnect for wash trading violation
    """

    def __init__(self, trader_id: str) -> None:
        self.trader_id = trader_id
        super().__init__(f"Self-matching blocked for trader '{trader_id}'")


class OrderBook:
    def __init__(self, self_matching_banned: bool = True, venue: str = "UNKNOWN") -> None:
        self.self_matching_banned = self_matching_banned
        self.venue = venue
        self.bids: Dict[int, Deque[Order]] = defaultdict(deque)
        self.asks: Dict[int, Deque[Order]] = defaultdict(deque)
        # Open quantity per price level. A key is present only while its
        # value is greater than zero. This is the single source of truth
        # for whether a price level is "live" in the heap, and avoids
        # scanning entire deques just to compute an aggregate.
        self.bid_open_qty: Dict[int, int] = {}
        self.ask_open_qty: Dict[int, int] = {}
        self.bid_prices: List[int] = []  # max-heap via negated prices
        self.ask_prices: List[int] = []  # min-heap
        # Full order history, keyed by order_id. Entries are never removed,
        # so callers can audit the final state of an order after it fills
        # or gets cancelled.
        self.orders: Dict[str, Order] = {}
        self.trades: List[Dict[str, object]] = []
        self._order_sequence = 0
        self._timestamp_sequence = 0
        self._lock = asyncio.Lock()

    def _next_id(self, side: str) -> str:
        self._order_sequence += 1
        return f"{self.venue}-{side.lower()}-{self._order_sequence:06d}"

    def _next_timestamp(self) -> int:
        self._timestamp_sequence += 1
        return self._timestamp_sequence

    def _normalize_price(self, price: int) -> int:
        # Prices are required to be positive integers (e.g. cents) to avoid
        # floating-point rounding issues in comparisons and arithmetic.
        if not isinstance(price, int) or price <= 0:
            raise ValueError("Price must be a positive integer (e.g. cents)")
        return price

    def _open_qty_book(self, side: str) -> Dict[int, int]:
        return self.bid_open_qty if side == "BUY" else self.ask_open_qty

    def _add_price_level(self, side: str, price: int, quantity: int) -> None:
        """Register open quantity at a price level, pushing a fresh heap
        entry only if the level currently has no open volume.

        Checking open_qty (rather than deque emptiness) is what makes this
        safe: a price level can have a non-empty deque made up entirely of
        cancelled orders after its heap entry was already evicted by
        _best_price. Relying on deque truthiness would silently skip the
        heap push and make the level unreachable to future matching.
        """
        open_qty = self._open_qty_book(side)
        if open_qty.get(price, 0) == 0:
            if side == "BUY":
                heapq.heappush(self.bid_prices, -price)
            else:
                heapq.heappush(self.ask_prices, price)
        open_qty[price] = open_qty.get(price, 0) + quantity

    def _reduce_open_qty(self, side: str, price: int, quantity: int) -> None:
        # Once a level's open quantity reaches zero, drop the key entirely
        # instead of leaving it at 0. This keeps bid_open_qty/ask_open_qty
        # bounded by the number of currently active levels, not by every
        # price ever touched over the book's lifetime.
        open_qty = self._open_qty_book(side)
        remaining_at_level = open_qty.get(price, 0) - quantity
        if remaining_at_level <= 0:
            open_qty.pop(price, None)
        else:
            open_qty[price] = remaining_at_level

    def _best_price(self, side: str) -> Optional[int]:
        # Lazy deletion: the heap may contain stale entries for levels that
        # have since been fully cancelled or filled. Pop them until the top
        # of the heap corresponds to a level with real open quantity.
        open_qty = self._open_qty_book(side)
        heap = self.bid_prices if side == "BUY" else self.ask_prices
        sign = -1 if side == "BUY" else 1
        while heap:
            price = sign * heap[0]
            if open_qty.get(price, 0) > 0:
                return price
            heapq.heappop(heap)
        return None

    def _match(self, incoming: Order) -> None:
        while incoming.remaining > 0:
            if incoming.side == "BUY":
                best_price = self._best_price("SELL")
                if best_price is None or best_price > incoming.price:
                    break
                book = self.asks
                resting_side = "SELL"
            else:
                best_price = self._best_price("BUY")
                if best_price is None or best_price < incoming.price:
                    break
                book = self.bids
                resting_side = "BUY"

            queue = book[best_price]

            # Drop cancelled orders from the front of the queue before
            # inspecting who is next in line. Only status matters here;
            # actual removal from the deque is deferred until an order
            # reaches the head, which is standard lazy-deletion behavior.
            while queue and queue[0].status == "cancelled":
                queue.popleft()

            if not queue:
                # Defensive: should be unreachable in a single-threaded
                # caller, since _best_price only returns a price backed by
                # positive open quantity at that exact level.
                continue

            resting = queue[0]

            # ── Self-Match Prevention ("Cancel Newest" SMP) ─────────────
            # If the incoming order would cross a resting order from the same
            # trader, halt matching immediately — do not execute, do not
            # insert into the book. The caller must handle WashTradeError.
            if self.self_matching_banned and incoming.trader_id == resting.trader_id:
                raise WashTradeError(incoming.trader_id)

            trade_quantity = min(incoming.remaining, resting.remaining)
            incoming.remaining -= trade_quantity
            resting.remaining -= trade_quantity
            self._reduce_open_qty(resting_side, best_price, trade_quantity)

            # Execution price is always the resting (maker) order's price,
            # which is the standard price-time priority convention.
            # Execution price is always the resting (maker) order's price,
            # which is the standard price-time priority convention.
            self.trades.append({
                "symbol": incoming.symbol,
                "venue": incoming.venue,
                "price": best_price,
                "quantity": trade_quantity,
                "buy_order_id": incoming.order_id if incoming.side == "BUY" else resting.order_id,
                "sell_order_id": incoming.order_id if incoming.side == "SELL" else resting.order_id,
                # Identity tracking required for the TournamentManager account processing
                "buyer_id": incoming.trader_id if incoming.side == "BUY" else resting.trader_id,
                "seller_id": incoming.trader_id if incoming.side == "SELL" else resting.trader_id,
                "timestamp": self._next_timestamp(),
            })

            if resting.remaining == 0:
                queue.popleft()
                resting.status = "filled"
                # Intentionally not removed from self.orders, to preserve
                # audit history for the trader who owned this order.

            while queue and queue[0].status == "cancelled":
                queue.popleft()

            if incoming.remaining == 0:
                incoming.status = "filled"
                break

    def place_order(
        self,
        side: str,
        price: int,
        quantity: int,
        trader_id: str,
        symbol: str = "UNKNOWN",
        venue: str = "UNKNOWN",
        order_id: Optional[str] = None,
        tif: str = "GTC",
    ) -> str:
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if not isinstance(trader_id, str) or not trader_id.strip():
            raise ValueError("trader_id must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("venue must be a non-empty string")
        if tif not in {"GTC", "IOC", "FOK"}:
            raise ValueError("tif must be GTC, IOC, or FOK")
        if order_id is not None and order_id in self.orders:
            raise ValueError(f"order_id '{order_id}' already exists")

        normalized_price = self._normalize_price(price)

        # Fill-Or-Kill: check up front whether the book can absorb the full
        # quantity at this price or better. If not, the order is recorded
        # as cancelled without ever touching the matching engine.
        if tif == "FOK" and not self._can_fill_completely(side, normalized_price, quantity):
            order = Order(
                order_id=order_id or self._next_id(side),
                side=side,
                price=normalized_price,
                quantity=quantity,
                trader_id=trader_id,
                timestamp=self._next_timestamp(),
                symbol=symbol,
                venue=venue,
                tif=tif,
            )
            order.status = "cancelled"
            order.remaining = 0
            self.orders[order.order_id] = order
            return order.order_id

        order = Order(
            order_id=order_id or self._next_id(side),
            side=side,
            price=normalized_price,
            quantity=quantity,
            trader_id=trader_id,
            timestamp=self._next_timestamp(),
            symbol=symbol,
            venue=venue,
            tif=tif,
        )
        self.orders[order.order_id] = order

        try:
            self._match(order)
        except WashTradeError:
            # "Cancel Newest" SMP: reject the incoming order entirely.
            # Do not add it to the book — it never executed.
            order.status = "cancelled"
            order.remaining = 0
            # Re-raise so the caller (ExchangeService) can handle
            # disconnection or warning as appropriate.
            raise

        # Post-match handling of any unfilled quantity.
        if order.remaining > 0:
            if tif == "GTC":
                # Good-Till-Cancelled: the remainder rests on the book.
                self._add_price_level(order.side, order.price, order.remaining)
                book = self.bids if order.side == "BUY" else self.asks
                book[order.price].append(order)
            else:
                # IOC: any unfilled remainder is cancelled outright.
                # FOK: defensive fallback only — _can_fill_completely already
                # guaranteed sufficient liquidity, so this branch should be
                # unreachable, but if it were ever hit, the order must not be
                # left "open" with no place in the book.
                order.status = "cancelled"
                order.remaining = 0

        return order.order_id

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if not order or order.status != "open":
            return False

        self._reduce_open_qty(order.side, order.price, order.remaining)
        order.status = "cancelled"
        order.remaining = 0
        # Not removed from its deque; it will be swept out lazily the next
        # time that price level is inspected by the matching engine.
        return True

    def best_bid(self) -> Optional[int]:
        return self._best_price("BUY")

    def best_ask(self) -> Optional[int]:
        return self._best_price("SELL")

    # ── Top-of-book (L1) volume queries for shock detection ──────────
    def best_bid_volume(self) -> int:
        """Return resting volume at the best bid level, or 0 if no bid."""
        price = self._best_price("BUY")
        return self.bid_open_qty.get(price, 0) if price is not None else 0

    def best_ask_volume(self) -> int:
        """Return resting volume at the best ask level, or 0 if no ask."""
        price = self._best_price("SELL")
        return self.ask_open_qty.get(price, 0) if price is not None else 0

    def total_volume(self, side: str) -> int:
        """Total open quantity across all price levels on one side."""
        open_qty = self._open_qty_book(side)
        return sum(open_qty.values())

    def get_order_status(self, order_id: str) -> Optional[str]:
        """Lets a trader check whether their order is open, filled, or cancelled."""
        order = self.orders.get(order_id)
        return order.status if order else None

    def get_open_order_quantity(self, order_id: str) -> int:
        order = self.orders[order_id]
        return order.remaining if order.status == "open" else 0

    def get_trades(self) -> List[Dict[str, object]]:
        return [dict(trade) for trade in self.trades]

    def get_snapshot(self) -> Dict[str, List[Dict[str, object]]]:
        # Built directly from bid_open_qty/ask_open_qty, which only ever
        # contain price levels with real open volume. This keeps snapshot
        # generation O(active levels), rather than O(every price level the
        # book has ever seen).
        snapshot = {"bids": [], "asks": []}
        for price in sorted(self.bid_open_qty.keys(), reverse=True):
            snapshot["bids"].append({"price": price, "quantity": self.bid_open_qty[price]})
        for price in sorted(self.ask_open_qty.keys()):
            snapshot["asks"].append({"price": price, "quantity": self.ask_open_qty[price]})
        return snapshot

    def _can_fill_completely(self, side: str, price: int, quantity: int) -> bool:
        """Check whether resting liquidity at price or better can absorb
        the full requested quantity, used for Fill-Or-Kill validation."""
        accumulated = 0

        if side == "BUY":
            for ask_price in sorted(self.ask_open_qty.keys()):
                if ask_price > price:
                    break  # remaining levels are only more expensive
                accumulated += self.ask_open_qty[ask_price]
                if accumulated >= quantity:
                    return True
        else:
            for bid_price in sorted(self.bid_open_qty.keys(), reverse=True):
                if bid_price < price:
                    break  # remaining levels are only cheaper
                accumulated += self.bid_open_qty[bid_price]
                if accumulated >= quantity:
                    return True

        return False