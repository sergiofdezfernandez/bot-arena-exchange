from dataclasses import dataclass
from typing import Dict

from bot_arena_exchange.config.tournament_config import ScoringConfig
from bot_arena_exchange.domain.tournament import TraderAccount


@dataclass(frozen=True)
class ScoreResult:
    trader_id: str
    realized_pnl: int
    delta_exposure: int
    liquidation_penalty: int
    adjusted_score: int
    status: str


def calculate_delta_liquidation_penalty(
    positions: Dict[str, int],
    reference_prices: Dict[str, int],
    spread_bps: int,
    liquidation_fee_bps: int,
) -> int:
    penalty = 0
    total_bps = spread_bps + liquidation_fee_bps
    for symbol, quantity in positions.items():
        reference_price = reference_prices.get(symbol)
        if reference_price is None:
            continue
        penalty += abs(quantity) * reference_price * total_bps // 10000
    return penalty


def score_account(
    account: TraderAccount,
    scoring_config: ScoringConfig,
    reference_prices: Dict[str, int],
    spread_bps_by_symbol: Dict[str, int],
) -> ScoreResult:
    delta_exposure = sum(abs(quantity) for quantity in account.positions.values())
    penalty = 0
    if scoring_config.delta_penalty_enabled:
        for symbol, quantity in account.positions.items():
            penalty += calculate_delta_liquidation_penalty(
                {symbol: quantity},
                reference_prices,
                spread_bps_by_symbol.get(symbol, 0),
                scoring_config.liquidation_fee_bps,
            )
    return ScoreResult(
        trader_id=account.trader_id,
        realized_pnl=account.realized_pnl,
        delta_exposure=delta_exposure,
        liquidation_penalty=penalty,
        adjusted_score=account.realized_pnl - penalty,
        status=account.status,
    )
