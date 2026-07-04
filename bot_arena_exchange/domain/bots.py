import random
from typing import Optional


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

        return [
            {"side": "BUY", "price": bid_price, "quantity": self.size},
            {"side": "SELL", "price": ask_price, "quantity": self.size}
        ]
class RandomTrader:
    def __init__(self, trader_id: str, symbol: str = "AAPL", venue: str = "VENUE_1"):
        self.trader_id = trader_id
        self.symbol = symbol
        self.venue = venue

    def generate_random_order(self) -> dict:
        """
        Generates a random order with a random side, price, and quantity.
        """
        side = random.choice(["BUY", "SELL"])
        price = random.randint(90, 110)  # Random price between 90 and 110
        quantity = random.randint(1, 10)  # Random quantity between 1 and 10

        return {
            "side": side,
            "price": price,
            "quantity": quantity
        }

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

        quantity = random.randint(10,50)
        return {"side": side, "price": price, "quantity": quantity}

class MeanRevertingTrader:
    def __init__(self, trader_id: str, symbol: str = "AAPL", venue: str = "VENUE_1"):
        self.trader_id = trader_id
        self.symbol = symbol
        self.venue = venue
        self.prices_seen = []

    def think(self, best_bid: Optional[int], best_ask: Optional[int]) -> Optional[dict]:
        """
        Decide whether to send an order based on visible top-of-book.
        Returns an order dict or None to skip the tick.
        """
        if best_bid is None or best_ask is None:
            return None

        mid_price = (best_bid + best_ask) // 2
        self.prices_seen.append(mid_price)

        # Keep only the last 10 prices for mean calculation
        if len(self.prices_seen) > 10:
            self.prices_seen.pop(0)

        mean_price = sum(self.prices_seen) / len(self.prices_seen)

        # If current mid price is above mean, consider selling; below mean, consider buying
        if mid_price > mean_price + 1:
            side = "SELL"
            price = best_bid  # Take the bid
        elif mid_price < mean_price - 1:
            side = "BUY"
            price = best_ask  # Take the ask
        else:
            return None  # No action if within the mean range

        quantity = random.randint(25,100) 
        return {"side": side, "price": price, "quantity": quantity} 
