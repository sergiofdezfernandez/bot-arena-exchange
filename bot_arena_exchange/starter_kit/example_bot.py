class ExampleBot:
    def __init__(self):
        self.last_order_id = None

    def on_tick(self, api):
        book = api.get_order_book(symbol="AAPL", venue="VENUE_1")
        position = api.get_position(symbol="AAPL")
        if self.last_order_id is not None:
            api.cancel_order(self.last_order_id)
        res = api.place_order(
            symbol="AAPL",
            venue="VENUE_1",
            side="BUY",
            price=book["best_bid"] or 9990,
            quantity=1,
        )
        self.last_order_id = res.get("order_id") if isinstance(res, dict) else getattr(res, "order_id", res)
        return {"position": position, "order_id": self.last_order_id}


def create_bot():
    return ExampleBot()
