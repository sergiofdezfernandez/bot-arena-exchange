import pytest
from bot_arena_exchange.domain.tournament import TournamentManager, TraderAccount

def test_initial_account_state():
    """Verifies that a new account is initialized with default safe values."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    assert account.trader_id == "trader_1"
    assert account.realized_pnl == 0
    assert account.status == "ACTIVE"
    assert "AAPL" not in account.positions

def test_average_cost_long_position():
    """Tests volume-weighted average price (VWAP) calculation for scaling into a long position."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    # Buy 10 @ 100
    manager._update_position(account, "AAPL", trade_qty=10, price=100)
    assert account.positions["AAPL"] == 10
    assert account.avg_costs["AAPL"] == 100
    
    # Buy 10 @ 110
    manager._update_position(account, "AAPL", trade_qty=10, price=110)
    assert account.positions["AAPL"] == 20
    assert account.avg_costs["AAPL"] == 105  # (1000 + 1100) / 20

def test_realized_pnl_closing_long():
    """Tests PnL calculation when partially closing a long position."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    # Build position: Buy 20 @ 105 avg cost
    manager._update_position(account, "AAPL", trade_qty=20, price=105)
    
    # Sell 10 @ 115 (Should realize +100 PnL: 10 * (115 - 105))
    manager._update_position(account, "AAPL", trade_qty=-10, price=115)
    
    assert account.positions["AAPL"] == 10
    assert account.realized_pnl == 100
    assert account.avg_costs["AAPL"] == 105  # Average cost remains unchanged on reduction

def test_realized_pnl_closing_short():
    """Tests PnL calculation when partially closing a short position."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    # Build short position: Sell 20 @ 100
    manager._update_position(account, "AAPL", trade_qty=-20, price=100)
    assert account.avg_costs["AAPL"] == 100
    
    # Buy 10 @ 90 (Should realize +100 PnL: 10 * (100 - 90))
    manager._update_position(account, "AAPL", trade_qty=10, price=90)
    
    assert account.positions["AAPL"] == -10
    assert account.realized_pnl == 100

def test_position_flip():
    """Tests transitioning from a net long position to a net short position in a single trade."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    # Buy 10 @ 100
    manager._update_position(account, "AAPL", trade_qty=10, price=100)
    
    # Sell 20 @ 120 (Closes 10 for +200 PnL, opens 10 short @ 120)
    manager._update_position(account, "AAPL", trade_qty=-20, price=120)
    
    assert account.positions["AAPL"] == -10
    assert account.realized_pnl == 200
    assert account.avg_costs["AAPL"] == 120  # New cost basis established by the short entry

def test_flat_position_memory_cleanup():
    """Ensures that returning to a flat position resets the average cost to zero."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    manager._update_position(account, "AAPL", trade_qty=10, price=100)
    manager._update_position(account, "AAPL", trade_qty=-10, price=110)
    
    assert account.positions["AAPL"] == 0
    assert account.avg_costs["AAPL"] == 0

def test_hard_limit_disconnection():
    """Verifies that exceeding the position limit triggers an immediate disconnection event."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    
    # Safe trade
    event1 = manager._update_position(account, "AAPL", trade_qty=90, price=100)
    assert event1 is None
    assert account.status == "ACTIVE"
    
    # Breach trade (+20 pushes total to 110)
    event2 = manager._update_position(account, "AAPL", trade_qty=20, price=100)
    
    assert event2 is not None
    assert event2["event"] == "DISCONNECTION"
    assert event2["breached_quantity"] == 110
    assert account.status == "DISCONNECTED"

def test_ignore_disconnected_traders():
    """Ensures that the manager halts processing for disconnected accounts."""
    manager = TournamentManager(position_limit=100)
    account = manager.get_account("trader_1")
    account.status = "DISCONNECTED"
    account.positions["AAPL"] = 110
    
    # Attempting to trade while disconnected
    event = manager._update_position(account, "AAPL", trade_qty=-10, price=100)
    
    assert event is None
    assert account.positions["AAPL"] == 110  # State remains frozen