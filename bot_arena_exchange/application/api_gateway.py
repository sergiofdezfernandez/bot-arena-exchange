from dataclasses import dataclass
from typing import Any, Dict, Optional

from bot_arena_exchange.config.tournament_config import TournamentConfig
from bot_arena_exchange.domain.tournament import TournamentManager


@dataclass(frozen=True)
class GatewayValidationResult:
    accepted: bool
    reason: Optional[str] = None


class ApiGateway:
    def __init__(self, config: TournamentConfig, manager: TournamentManager):
        self.config = config
        self.manager = manager

    def validate_order_request(self, payload: Dict[str, Any], tournament_status: str = "RUNNING") -> GatewayValidationResult:
        if tournament_status != "RUNNING":
            return GatewayValidationResult(False, "tournament is not running")

        required_fields = {"side", "price", "quantity", "trader_id", "symbol", "venue"}
        missing_fields = sorted(required_fields - set(payload))
        if missing_fields:
            return GatewayValidationResult(False, f"missing fields: {missing_fields}")

        side = payload["side"]
        price = payload["price"]
        quantity = payload["quantity"]
        trader_id = payload["trader_id"]
        symbol = payload["symbol"]
        venue = payload["venue"]

        if side not in {"BUY", "SELL"}:
            return GatewayValidationResult(False, "side must be BUY or SELL")
        if not isinstance(price, int) or price <= 0:
            return GatewayValidationResult(False, "price must be a positive integer")
        if not isinstance(quantity, int) or quantity <= 0:
            return GatewayValidationResult(False, "quantity must be a positive integer")
        if not isinstance(trader_id, str) or not trader_id.strip():
            return GatewayValidationResult(False, "trader_id must be a non-empty string")
        if symbol not in self.config.market_symbols():
            return GatewayValidationResult(False, "unsupported symbol")
        if venue not in self.config.venue_ids():
            return GatewayValidationResult(False, "unsupported venue")

        venue_config = self.config.venue_for(venue)
        if symbol not in venue_config.supported_symbols:
            return GatewayValidationResult(False, "symbol is not supported on venue")

        market = self.config.market_for(symbol)
        if price % market.tick_size != 0:
            return GatewayValidationResult(False, "price does not match tick size")
        if quantity % market.lot_size != 0:
            return GatewayValidationResult(False, "quantity does not match lot size")

        account = self.manager.get_account(trader_id)

        # System accounts bypass all account status and position limit checks
        if not account.is_system:
            if account.status != "ACTIVE":
                return GatewayValidationResult(False, "trader account is not active")

            current_position = account.positions.get(symbol, 0)
            signed_quantity = quantity if side == "BUY" else -quantity
            if abs(current_position + signed_quantity) > self.manager.position_limit:
                return GatewayValidationResult(False, "position limit would be exceeded")

        return GatewayValidationResult(True)
