import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from fractions import Fraction


@dataclass
class TraderAccount:
    trader_id: str
    positions: Dict[str, int] = field(default_factory=dict)  # Net position per symbol (positive: long, negative: short)
    cost_basis_total: Dict[str, Fraction] = field(default_factory=dict)  # Total cost (sum price*qty) as Fraction, in pips
    avg_costs: Dict[str, int] = field(default_factory=dict)  # Average price per symbol in pips (int)
    realized_pnl: int = 0  # Accumulated realized PnL in minor currency units (e.g., cents)
    fees_paid: int = 0
    status: str = "ACTIVE"  # Account state: "ACTIVE" or "DISCONNECTED"
    is_system: bool = False  # System accounts bypass risk limits and are excluded from leaderboards

class TournamentManager:
    def __init__(self, position_limit: int = 100, system_account_ids: Optional[Set[str]] = None):
        self.accounts: Dict[str, TraderAccount] = {}
        self.position_limit = position_limit
        self.system_account_ids: Set[str] = system_account_ids or set()
        self._lock = asyncio.Lock()

    def get_account(self, trader_id: str) -> TraderAccount:
        if trader_id not in self.accounts:
            account = TraderAccount(trader_id=trader_id)
            account.is_system = trader_id in self.system_account_ids
            self.accounts[trader_id] = account
        return self.accounts[trader_id]

    def disconnect_account(self, trader_id: str, reason: str = "") -> dict:
        """Force-disconnect an account (e.g. for wash trading violation)."""
        account = self.get_account(trader_id)
        account.status = "DISCONNECTED"
        return {
            "event": "DISCONNECTION",
            "trader_id": trader_id,
            "reason": reason,
            "symbol": "N/A",
            "breached_quantity": 0,
            "limit": 0,
        }

    def _update_position(self, account: TraderAccount, symbol: str, trade_qty: int, price: int, fee: int = 0) -> Optional[dict]:
        """
        Updates account inventory, calculates realized PnL using the weighted average cost,
        and validates position risk limits.
        
        trade_qty must be positive for buy executions and negative for sell executions.
        """
        if account.status == "DISCONNECTED" and not account.is_system:
            return None  # Rejects activity for disqualified accounts (system accounts are never blocked)

        current_qty = account.positions.get(symbol, 0)
        current_cost_total: Fraction = account.cost_basis_total.get(symbol, Fraction(0))

        new_qty = current_qty + trade_qty

        # If we're reducing or closing an existing exposure (opposite signs)
        if current_qty != 0 and (current_qty > 0) != (trade_qty > 0):
            closed_qty = min(abs(trade_qty), abs(current_qty))

            # Average price as positive Fraction (price per unit in pips)
            avg_cost_frac = Fraction(abs(current_cost_total), abs(current_qty))

            if current_qty > 0:  # Closing a long position
                pnl_frac = Fraction(closed_qty) * (Fraction(price) - avg_cost_frac)
            else:  # Closing a short position
                pnl_frac = Fraction(closed_qty) * (avg_cost_frac - Fraction(price))
            # Accumulate realized PnL as integer pips (rounding to nearest pip)
            account.realized_pnl += int(round(pnl_frac))

            # Subtract closed portion from cost basis total, respecting sign of current position
            sign = 1 if current_qty > 0 else -1
            remaining_cost = current_cost_total - sign * (avg_cost_frac * closed_qty)

            # Handle position reversal: create new cost basis for the residual in the new direction
            if abs(trade_qty) > abs(current_qty):
                residual_qty = abs(trade_qty) - abs(current_qty)
                new_sign = 1 if trade_qty > 0 else -1
                account.cost_basis_total[symbol] = Fraction(new_sign * residual_qty * price)
                account.avg_costs[symbol] = int(abs(price))
            else:
                # Still have exposure in the original direction
                account.cost_basis_total[symbol] = remaining_cost
                # Update avg cost if there is remaining exposure
                if abs(new_qty) != 0:
                    account.avg_costs[symbol] = int(round(abs(account.cost_basis_total[symbol]) / abs(new_qty)))
                else:
                    account.avg_costs[symbol] = 0
        else:
            # Increasing exposure in the same direction or opening a fresh position
            if new_qty != 0:
                new_cost_total = current_cost_total + Fraction(trade_qty * price)
                account.cost_basis_total[symbol] = new_cost_total
                account.avg_costs[symbol] = int(round(abs(new_cost_total) / abs(new_qty)))
            else:
                # Net flat
                account.cost_basis_total[symbol] = Fraction(0)
                account.avg_costs[symbol] = 0

        account.positions[symbol] = new_qty
        account.fees_paid += fee

        # Hard risk limit verification (bypassed for system accounts)
        if not account.is_system and abs(new_qty) > self.position_limit:
            account.status = "DISCONNECTED"
            return {
                "event": "DISCONNECTION",
                "trader_id": account.trader_id,
                "symbol": symbol,
                "breached_quantity": new_qty,
                "limit": self.position_limit
            }
            
        return None

    def process_trades(self, trades: List[Dict[str, object]], fee_bps_by_venue: Optional[Dict[str, int]] = None) -> List[dict]:
        """
        Processes execution records generated by the MatchingEngine.
        Returns a list of account disconnection events resulting from risk breaches.
        """
        events = []
        fee_bps_by_venue = fee_bps_by_venue or {}

        for trade in trades:
            symbol = str(trade["symbol"])
            price = int(trade["price"])
            qty = int(trade["quantity"])
            venue = str(trade.get("venue", ""))
            fee = price * qty * fee_bps_by_venue.get(venue, 0) // 10000

            buyer_id = str(trade["buyer_id"])
            seller_id = str(trade["seller_id"])

            buyer = self.get_account(buyer_id)
            seller = self.get_account(seller_id)

            # Update buyer inventory
            ev_buy = self._update_position(buyer, symbol, qty, price, fee)
            if ev_buy: 
                events.append(ev_buy)

            # Update seller inventory
            ev_sell = self._update_position(seller, symbol, -qty, price, fee)
            if ev_sell: 
                events.append(ev_sell)

        return events