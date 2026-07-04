from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class TournamentRulesConfig:
    duration_ticks: int
    entry_deadline_seconds_before_start: int
    minimum_participants: int


@dataclass(frozen=True)
class ScoringConfig:
    liquidation_fee_bps: int
    delta_penalty_enabled: bool = True


@dataclass(frozen=True)
class MarketConfig:
    symbol: str
    market_type: str
    tick_size: int
    lot_size: int
    initial_reference_price: int


@dataclass(frozen=True)
class FeeConfig:
    fee_bps: int


@dataclass(frozen=True)
class LatencyConfig:
    latency_ticks: int


@dataclass(frozen=True)
class VenueConfig:
    venue_id: str
    fee_bps: int
    spread_bps: int
    latency_ticks: int
    supported_symbols: List[str]


@dataclass(frozen=True)
class RegimeConfig:
    name: str
    visible_before_tournament: bool
    volatility_bps: int
    liquidity_multiplier: float
    spread_multiplier: float


@dataclass(frozen=True)
class TournamentConfig:
    tournament_id: str
    rules: TournamentRulesConfig
    scoring: ScoringConfig
    markets: List[MarketConfig]
    venues: List[VenueConfig]
    regimes: List[RegimeConfig]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TournamentConfig":
        config = cls(
            tournament_id=str(data["tournament_id"]),
            rules=TournamentRulesConfig(**data["rules"]),
            scoring=ScoringConfig(**data["scoring"]),
            markets=[MarketConfig(**market) for market in data["markets"]],
            venues=[VenueConfig(**venue) for venue in data["venues"]],
            regimes=[RegimeConfig(**regime) for regime in data["regimes"]],
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.tournament_id.strip():
            raise ValueError("tournament_id must be non-empty")
        if self.rules.duration_ticks <= 0:
            raise ValueError("duration_ticks must be positive")
        if self.rules.entry_deadline_seconds_before_start < 0:
            raise ValueError("entry deadline cannot be negative")
        if self.rules.minimum_participants <= 0:
            raise ValueError("minimum_participants must be positive")
        if self.scoring.liquidation_fee_bps < 0:
            raise ValueError("liquidation_fee_bps cannot be negative")
        if not self.markets:
            raise ValueError("at least one market is required")
        if not self.venues:
            raise ValueError("at least one venue is required")
        if not self.regimes:
            raise ValueError("at least one regime is required")

        symbols = {market.symbol for market in self.markets}
        if len(symbols) != len(self.markets):
            raise ValueError("market symbols must be unique")

        venue_ids = {venue.venue_id for venue in self.venues}
        if len(venue_ids) != len(self.venues):
            raise ValueError("venue ids must be unique")

        for market in self.markets:
            if not market.symbol.strip():
                raise ValueError("market symbol must be non-empty")
            if market.market_type not in {"spot", "future"}:
                raise ValueError("market_type must be spot or future")
            if market.tick_size <= 0 or market.lot_size <= 0:
                raise ValueError("tick_size and lot_size must be positive")
            if market.initial_reference_price <= 0:
                raise ValueError("initial_reference_price must be positive")

        for venue in self.venues:
            if not venue.venue_id.strip():
                raise ValueError("venue_id must be non-empty")
            if venue.fee_bps < 0 or venue.spread_bps < 0 or venue.latency_ticks < 0:
                raise ValueError("fee, spread, and latency cannot be negative")
            unsupported = set(venue.supported_symbols) - symbols
            if unsupported:
                raise ValueError(f"venue {venue.venue_id} references unsupported symbols: {sorted(unsupported)}")

        regime_names = {regime.name for regime in self.regimes}
        if len(regime_names) != len(self.regimes):
            raise ValueError("regime names must be unique")
        for regime in self.regimes:
            if not regime.name.strip():
                raise ValueError("regime name must be non-empty")
            if regime.volatility_bps < 0:
                raise ValueError("volatility_bps cannot be negative")
            if regime.liquidity_multiplier < 0 or regime.spread_multiplier < 0:
                raise ValueError("regime multipliers cannot be negative")

    def market_symbols(self) -> set:
        return {market.symbol for market in self.markets}

    def venue_ids(self) -> set:
        return {venue.venue_id for venue in self.venues}

    def venue_for(self, venue_id: str) -> VenueConfig:
        for venue in self.venues:
            if venue.venue_id == venue_id:
                return venue
        raise KeyError(venue_id)

    def market_for(self, symbol: str) -> MarketConfig:
        for market in self.markets:
            if market.symbol == symbol:
                return market
        raise KeyError(symbol)


def load_tournament_config(path: str | Path) -> TournamentConfig:
    with Path(path).open() as config_file:
        return TournamentConfig.from_dict(json.load(config_file))


DEFAULT_TOURNAMENT_CONFIG = load_tournament_config(Path(__file__).with_name("default_tournament.json"))
