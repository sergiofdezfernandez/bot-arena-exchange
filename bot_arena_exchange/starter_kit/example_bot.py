class ExampleBot:
    def __init__(self):
        self.last_order_id = None

    def on_tick(self, api):
        book = api.get_order_book(symbol="AAPL", venue="VENUE_1")
        position = api.get_position(symbol="AAPL")
        if self.last_order_id is not None:
            api.cancel_order(self.last_order_id)
        self.last_order_id = api.place_order(
            symbol="AAPL",
            venue="VENUE_1",
            side="BUY",
            price=book["best_bid"] or 9990,
            quantity=1,
        )
        return {"position": position, "order_id": self.last_order_id}


def create_bot():
    return ExampleBot()
