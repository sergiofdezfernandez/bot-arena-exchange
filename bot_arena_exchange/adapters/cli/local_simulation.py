import time

from bot_arena_exchange.application.exchange_service import ExchangeService
from bot_arena_exchange.domain.bots import SimpleMarketMaker, RandomTrader, MeanRevertingTrader
from bot_arena_exchange.domain.order_book import OrderBook
from bot_arena_exchange.domain.tournament import TournamentManager


def create_exchange_from_config(config_path):
    return ExchangeService.from_config_file(config_path)


def run_local_simulation(total_ticks=15):
    print("INITIALIZING LOCAL TICK-BY-TICK SIMULATION ")
    
    book = OrderBook()
    manager = TournamentManager(position_limit=100)
    
    
    mm_bot = SimpleMarketMaker(trader_id="Bot_MM", symbol="AAPL", venue="VENUE_1", edge=5, size=10)
    speculator_1 = RandomTrader(trader_id="Trader_Alpha")
    speculator_2 = RandomTrader(trader_id="Trader_Bravo")
    mean_reverter = MeanRevertingTrader(trader_id="Trader_MeanRev")
    
   
    last_price = 1000
    active_mm_orders = []

    # The Core Game Loop (Tick-by-Tick)
    for tick in range(1, total_ticks + 1):
        print(f"\n--- TICK {tick} ---")
        
        
        for order_id in active_mm_orders:
            book.cancel_order(order_id)
        active_mm_orders.clear()
        
       
        trades = book.get_trades()
        if trades:
            last_price = trades[-1]["price"]
            print(f"[MARKET] Last traded price updated to: {last_price}")
        book.trades.clear() # Flush historical trades to isolate the next tick
        
        
        quotes = mm_bot.generate_quotes(last_price)
        for q in quotes:
            oid = book.place_order(
                side=q["side"],
                price=q["price"],
                quantity=q["quantity"],
                trader_id=mm_bot.trader_id,
                symbol=mm_bot.symbol,
                venue=mm_bot.venue,
            )
            active_mm_orders.append(oid)
            
        
        snap = book.get_snapshot()
        best_bid = snap["bids"][0]["price"] if snap["bids"] else None
        best_ask = snap["asks"][0]["price"] if snap["asks"] else None
        
        
        for trader in [speculator_1, speculator_2, mean_reverter]:
            if account := manager.get_account(trader.trader_id):
                if account.status == "DISCONNECTED":
                    print(f"[RISK] {trader.trader_id} is DISCONNECTED and cannot trade.")
                    continue
            order_data = trader.think(best_bid, best_ask)
            if order_data:
                print(f"[ORDER] {trader.trader_id} fires {order_data['side']} {order_data['quantity']} @ {order_data['price']}")
                
              
                book.place_order(
                    side=order_data["side"],
                    price=order_data["price"],
                    quantity=order_data["quantity"],
                    trader_id=trader.trader_id,
                    symbol=trader.symbol,
                    venue=trader.venue,
                )
                
                # Check for executions and risk updates immediately
                tick_trades = book.get_trades()
                breaches = manager.process_trades(tick_trades)
                
                for breach in breaches:
                    print(f"🚨 [RISK BREACH] {breach['trader_id']} exceeded limits! Status: DISCONNECTED")

        
        print(f"[BOOK STATUS] Bids: {snap['bids'][:1]} | Asks: {snap['asks'][:1]}")
        
        
        time.sleep(0.5)

    
    print("\n=== SIMULATION CONCLUDED ===")
    print("Final Ledger Standings:")
    for trader_id, acc in manager.accounts.items():
        print(f" - {trader_id}: Position={acc.positions.get('AAPL', 0)}, PnL={acc.realized_pnl}, Status={acc.status}")

if __name__ == "__main__":
    run_local_simulation(total_ticks=30)