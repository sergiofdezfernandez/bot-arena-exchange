import asyncio
import csv
import os
from pathlib import Path
from datetime import datetime, timezone

# Sentinel to unblock the event loop during a reset
_RESET_SENTINEL = object()


class DataLogger:
    """Background subscriber that persists market events to CSV files.

    Listens to the ExchangeService event log and writes:
      - ORDERBOOK_UPDATE → data/quotes.csv
      - FILL             → data/trades.csv
    """

    def __init__(self, service):
        """*service* must be an instance of ExchangeService."""
        self.service = service
        self.queue: asyncio.Queue | None = None

        # Locate the project root: bot_arena_exchange/application/ → ../../
        project_root = Path(__file__).resolve().parent.parent.parent
        self._data_dir = project_root / "data"
        self._data_dir.mkdir(exist_ok=True)

        self._quotes_path = self._data_dir / "quotes.csv"
        self._trades_path = self._data_dir / "trades.csv"

        # Open CSV files in append mode; write headers only if newly created
        self._quotes_file = open(str(self._quotes_path), "a", newline="")
        self._trades_file = open(str(self._trades_path), "a", newline="")
        self._quotes_writer = csv.writer(self._quotes_file)
        self._trades_writer = csv.writer(self._trades_file)

        # Write headers if the file is empty
        if self._quotes_path.stat().st_size == 0:
            self._quotes_writer.writerow([
                "timestamp", "venue", "best_bid", "best_ask",
                "bid_qty", "ask_qty", "mid_price", "spread",
            ])
            self._quotes_file.flush()

        if self._trades_path.stat().st_size == 0:
            self._trades_writer.writerow([
                "timestamp", "venue", "price", "quantity",
                "buyer_id", "seller_id",
            ])
            self._trades_file.flush()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------
    async def _subscribe(self):
        """Acquire a fresh subscription queue from the event log."""
        self.queue = self.service.event_log.subscribe()

    async def reset_subscription(self):
        """Close the old subscription and attach to the current event log.

        Called whenever the ExchangeService is reset (startup or /reset)
        so the DataLogger always drains from the live event log.
        """
        old_queue = self.queue
        await self._subscribe()

        if old_queue is not None:
            # Unblock the main loop which might be blocked on old_queue.get()
            try:
                old_queue.put_nowait(_RESET_SENTINEL)
            except asyncio.QueueFull:
                pass
            self.service.event_log.unsubscribe(old_queue)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def start(self):
        """Subscribe to the event log and enter an infinite processing loop."""
        if self.queue is None:
            await self._subscribe()

        while True:
            event = await self.queue.get()

            if event is _RESET_SENTINEL:
                continue  # queue has been replaced; re-enter loop

            try:
                self._process_event(event)
            except Exception:
                # Never let a bad event kill the background logger
                pass

    def _process_event(self, event: dict):
        """Route an event to the appropriate CSV writer."""
        etype = event.get("event_type", "")

        if etype == "ORDERBOOK_UPDATE":
            self._write_quote(event)
        elif etype == "FILL":
            self._write_trade(event)

    # ------------------------------------------------------------------
    # CSV writers — null-safe extraction
    # ------------------------------------------------------------------
    def _write_quote(self, event: dict):
        """Extract quote data from an ORDERBOOK_UPDATE event payload.

        Payload is the dict returned by ExchangeService.get_market_state():
          {symbol, venue, best_bid, best_ask, snapshot: {bids, asks}, recent_trades}
        """
        payload = event.get("payload", {})
        venue = payload.get("venue", "")
        snapshot = payload.get("snapshot", {}) or {}
        bids = snapshot.get("bids", []) or []
        asks = snapshot.get("asks", []) or []

        best_bid = payload.get("best_bid")
        best_ask = payload.get("best_ask")

        # Null-safe top-of-book quantities
        bid_qty = bids[0]["quantity"] if bids else 0
        ask_qty = asks[0]["quantity"] if asks else 0

        # Null-safe mid-price and spread
        if best_bid is not None and best_ask is not None:
            mid_price = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid
        else:
            mid_price = ""
            spread = ""

        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        self._quotes_writer.writerow([
            timestamp, venue, best_bid or "", best_ask or "",
            bid_qty, ask_qty, mid_price, spread,
        ])
        self._quotes_file.flush()

    def _write_trade(self, event: dict):
        """Extract fill data from a FILL event payload."""
        payload = event.get("payload", {})
        venue = payload.get("venue", "")
        price = payload.get("price", "")
        quantity = payload.get("quantity", "")
        buyer_id = payload.get("buyer_id", "")
        seller_id = payload.get("seller_id", "")

        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        self._trades_writer.writerow([
            timestamp, venue, price, quantity, buyer_id, seller_id,
        ])
        self._trades_file.flush()

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------
    async def stop(self):
        """Close CSV files and unsubscribe from the event log."""
        if self.queue is not None:
            self.service.event_log.unsubscribe(self.queue)
            self.queue = None

        self._quotes_file.close()
        self._trades_file.close()