import random
from typing import Optional


def generate_pareto_size(base_size: int, alpha: float, max_limit: int = 5000) -> int:
    """
    Generates heavy-tailed order sizes using a Pareto distribution.
    - base_size: The minimum order size floor.
    - alpha: Tail index (lower = fatter tails, more institutional whales).
    - max_limit: Hard cap to protect matching engine and position boundaries.
    """
    sample = random.paretovariate(alpha)
    calculated_size = int(base_size * sample)
    return min(calculated_size, max_limit)


class SimpleMarketMaker:
    def __init__(self, trader_id: str, symbol: str, venue: str, edge: int = 2, size: int = 25):
        self.trader_id = trader_id
        self.symbol = symbol
        self.venue = venue
        self.edge = edge  
        self.size = size  

    def generate_quotes(self, current_price: int) -> list:
        """
        Generates a pair of orders (one BUY, one SELL) around a reference price.
        """
        bid_price = current_price - self.edge
        ask_price = current_price + self.edge
        qty = generate_pareto_size(base_size=100, alpha=3.0, max_limit=400)

        return [
            {"side": "BUY", "price": bid_price, "quantity": qty},
            {"side": "SELL", "price": ask_price, "quantity": qty}
        ]
class RandomTrader:
    def __init__(self, trader_id: str, symbol: str = "AAPL", venue: str = "VENUE_1"):
        self.trader_id = trader_id
        self.symbol = symbol
        self.venue = venue
        self.last_order_id = None  # Tracked for cancellation by LiquidityEngine

    
    def think(self, best_bid: Optional[int], best_ask: Optional[int]) -> Optional[dict]:
        """
        Decide whether to send an order based on visible top-of-book.
        Returns an order dict or None to skip the tick.
        """
        # Randomly skip to avoid flooding
        if random.random() < 0.4:
            return None

        side = random.choice(["BUY", "SELL"])

        if best_bid is None and best_ask is None:
            price = 10000 + random.randint(-50, 50)
        elif best_bid is None:
            price = (best_ask or 10000) - random.randint(1, 5)
        elif best_ask is None:
            price = (best_bid or 10000) + random.randint(1, 5)
        else:
            
            if random.random() < 0.6:
                price = best_ask if side == "BUY" else best_bid
            else:
                
                mid = (best_bid + best_ask) // 2
                price = mid + (1 if side == "BUY" else -1)

        quantity = generate_pareto_size(base_size=10, alpha=2.5, max_limit=200)
        return {"side": side, "price": price, "quantity": quantity}

class MeanRevertingTrader:
    def __init__(self, trader_id: str, symbol: str = "AAPL", venue: str = "VENUE_1"):
        self.trader_id = trader_id
        self.symbol = symbol
        self.venue = venue
        self.prices_seen = []
        self.last_order_id = None  # Tracked for cancellation by LiquidityEngine

    def think(self, best_bid: Optional[int], best_ask: Optional[int]) -> Optional[dict]:
        """
        Decide whether to send an order based on visible top-of-book.
        Returns an order dict or None to skip the tick.
        """
        if best_bid is None or best_ask is None:
            return None

        mid_price = (best_bid + best_ask) // 2
        self.prices_seen.append(mid_price)

        

        mean_price = 10000

        if mid_price > mean_price:
            side = "SELL"
            price = best_bid  # Take the bid
        elif mid_price < mean_price :
            side = "BUY"
            price = best_ask  # Take the ask
        else:
            return None  # No action if within the mean range

        quantity = generate_pareto_size(base_size=10, alpha=2.5, max_limit=200)
        return {"side": side, "price": price, "quantity": quantity}