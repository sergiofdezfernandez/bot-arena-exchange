import asyncio
import json
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bot_arena_exchange.adapters.api.liquidity_engine import LiquidityEngine
from bot_arena_exchange.application.data_logger import DataLogger
from bot_arena_exchange.application.exchange_service import ExchangeService
from bot_arena_exchange.domain.bots import RandomTrader, MeanRevertingTrader


# ---------------------------------------------------------------------------
# App & service singletons
# ---------------------------------------------------------------------------
app = FastAPI(title="Bot Arena Exchange")
service = ExchangeService()
data_logger = DataLogger(service)

# Seed default liquidity so the book is not empty
# DISABLED: Bot_MM was flooding the book with size-10 orders, making it hard
# to audit real bot performance. Uncomment if you need seed liquidity.
# service._seed_default_liquidity_sync()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class OrderRequest(BaseModel):
    side: str
    price: int
    quantity: int
    trader_id: str
    symbol: Optional[str] = None
    venue: Optional[str] = None


class CancelOrderRequest(BaseModel):
    trader_id: str


class BotFilesRequest(BaseModel):
    files: dict[str, str]


class BotSubmissionRequest(BaseModel):
    owner_id: str
    bot_name: str
    files: dict[str, str]


class TournamentEntryRequest(BaseModel):
    owner_id: str
    bot_name: str
    version: int


# ---------------------------------------------------------------------------
# Bot class registry & factories — extracted so startup and /reset can share
# ---------------------------------------------------------------------------
def _build_bot_registry_and_factories():
    """Return a tuple of (CLASS_MAP, create_fn_maker) for bot instantiation."""

    from bot_arena_exchange.starter_kit.market_maker_bot import MarketMakerBot
    from bot_arena_exchange.starter_kit.trend_bot import TrendBot
    from bot_arena_exchange.starter_kit.lagging_mm_bot import LaggingMarketMakerBot
    from bot_arena_exchange.starter_kit.spatial_arbitrage_bot import SpatialArbitrageBot
    from bot_arena_exchange.starter_kit.institutional_trend_bot import InstitutionalTrendBot
    from bot_arena_exchange.starter_kit.hft_market_maker_bot import HftMarketMakerBot
    from bot_arena_exchange.starter_kit.retail_noise_trader_bot import RetailNoiseTraderBot
    from bot_arena_exchange.starter_kit.stochastic_lagging_mm_bot import StochasticLaggingMMBot
    from bot_arena_exchange.starter_kit.slow_mean_reverter_bot import SlowMeanReverterBot
    from bot_arena_exchange.starter_kit.phantom_spoofer_bot import PhantomSpooferBot
    from bot_arena_exchange.starter_kit.stat_arb_bot import StatArbBot

    def _make_market_maker(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        # VENUE_2 Market Makers: thinner, more elastic liquidity
        # Designed to be resilient but not provide the same massive depth as VENUE_1
        if "V2" in tid:
            bot.venue = "VENUE_2"
            bot.order_size = 20              # 5x smaller than V1 (100)
            bot.max_position = 500           # 4x smaller than V1 (2000)
            bot.inventory_risk_aversion = 30 # 2x more risk averse than V1 (15)
        else:
            bot.venue = ven  # Default behavior for V1

    def _make_trend_bot(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = ven

    def _make_lagging_mm(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = "VENUE_2"  # LaggingMarketMakerBot opera EXCLUSIVAMENTE en VENUE_2

    def _make_arbitrage(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = ven

    def _make_inst_trend(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = ven

    def _make_hft_mm(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = ven

    def _make_noise(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = ven

    def _make_stoch_lagging(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = "VENUE_2"  # StochasticLaggingMMBot operates EXCLUSIVELY on VENUE_2

    def _make_slow_mr(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        # SlowMeanReverterBot operates cross-venue; venue is set per-order, not globally

    def _make_spoofer(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        bot.venue = "VENUE_2"  # PhantomSpooferBot operates EXCLUSIVELY on VENUE_2

    def _make_stat_arb(bot, tid, sym, ven):
        bot.trader_id = tid
        bot.symbol = sym
        # StatArbBot trades on both venues; venue is set per-order, not globally

    CLASS_MAP = {
        "MarketMakerBot":   (MarketMakerBot,   _make_market_maker),
        "RandomTrader":     (RandomTrader,     None),  # takes trader_id/symbol/venue in constructor
        "MeanRevertingTrader": (MeanRevertingTrader, None),
        "TrendBot":         (TrendBot,         _make_trend_bot),
        "LaggingMarketMakerBot": (LaggingMarketMakerBot, _make_lagging_mm),
        "SpatialArbitrageBot": (SpatialArbitrageBot, _make_arbitrage),
        "InstitutionalTrendBot": (InstitutionalTrendBot, _make_inst_trend),
        "HftMarketMakerBot": (HftMarketMakerBot, _make_hft_mm),
        "RetailNoiseTraderBot": (RetailNoiseTraderBot, _make_noise),
        "StochasticLaggingMMBot": (StochasticLaggingMMBot, _make_stoch_lagging),
        "SlowMeanReverterBot": (SlowMeanReverterBot, _make_slow_mr),
        "PhantomSpooferBot": (PhantomSpooferBot, _make_spoofer),
        "StatArbBot": (StatArbBot, _make_stat_arb),
    }

    def _create_and_register(engine, trader_id, registry, market_config, venue_config):
        raw_entry = registry.get(trader_id)
        if raw_entry is None:
            print(f"[startup] WARNING: {trader_id} not found in bot_registry, skipping")
            return

        # Support both legacy string-only format and new dict format:
        #   "Bot_A": "MarketMakerBot"
        #   "Bot_B": {"class": "MarketMakerBot", "base_intensity": 10.0}
        if isinstance(raw_entry, dict):
            class_name = raw_entry.get("class")
            base_intensity = raw_entry.get("base_intensity")
        else:
            class_name = raw_entry
            base_intensity = None

        entry = CLASS_MAP.get(class_name)
        if entry is None:
            print(f"[startup] WARNING: unknown class '{class_name}' for {trader_id}, skipping")
            return

        bot_cls, init_fn = entry

        if init_fn is not None:
            # No-arg constructor → bot = cls(), then init_fn sets fields
            bot = bot_cls()
            init_fn(bot, trader_id, market_config.symbol, venue_config.venue_id)
        else:
            # Constructor takes (trader_id, symbol, venue)
            bot = bot_cls(
                trader_id=trader_id,
                symbol=market_config.symbol,
                venue=venue_config.venue_id,
            )

        # Apply base_intensity from JSON config if provided (overrides class default)
        if base_intensity is not None:
            bot.base_intensity = float(base_intensity)

        engine.register(bot)
        print(f"[startup] {trader_id} ({class_name}) registered")

    return CLASS_MAP, _create_and_register


async def _launch_engine():
    """Stop any existing engine, reset exchange state, and launch a fresh one."""
    old_engine = getattr(app.state, "liquidity_engine", None)
    if old_engine is not None:
        await old_engine.stop()
        print("[LiquidityEngine] Previous engine stopped before reset")

    # Cancel old leaderboard task if it exists
    old_lb_task = getattr(app.state, "leaderboard_task", None)
    if old_lb_task is not None:
        old_lb_task.cancel()

    # Reset all exchange state (accounts, order books, events)
    service.reset_state()

    # Re-anchor the DataLogger to the fresh event log
    await data_logger.reset_subscription()

    engine = LiquidityEngine(service)
    service._set_engine(engine)

    market_config = service.config.markets[0]
    venue_config = service.config.venues[0]
    registry = service.config.bot_registry.registry

    _cls_map, _create_and_register = _build_bot_registry_and_factories()

    # ── Launch liquidity bots ───────────────────────────────────────────
    for tid in service.config.liquidity_bots.enabled:
        _create_and_register(engine, tid, registry, market_config, venue_config)

    # ── Launch trading bots ─────────────────────────────────────────────
    for tid in service.config.trading_bots.enabled:
        _create_and_register(engine, tid, registry, market_config, venue_config)

    app.state.liquidity_engine = engine
    await engine.start()

    # Background task: broadcast leaderboard every 2s via event_log → WebSocket
    async def _broadcast_leaderboard():
        while True:
            await asyncio.sleep(2)
            try:
                lb = service.get_leaderboard()
                service.event_log.record(
                    event_type="LEADERBOARD_UPDATE",
                    bot_id="SYSTEM",
                    tournament_id=service.config.tournament_id,
                    payload={"leaderboard": lb},
                    validation_result="ACCEPTED",
                    final_action="BROADCAST",
                )
            except Exception:
                pass

    app.state.leaderboard_task = asyncio.create_task(_broadcast_leaderboard())


# ---------------------------------------------------------------------------
# REST read endpoints (sync, safe to call without lock)
# ---------------------------------------------------------------------------
@app.get("/config")
def get_config():
    return service.get_tournament_config()


@app.get("/snapshot")
def get_market_snapshot():
    return service.get_market_snapshot()


@app.get("/market")
def get_market_state():
    return service.get_market_state()


@app.get("/orders/{order_id}")
def get_order_state(order_id: str):
    return service.get_order_state(order_id)


@app.get("/accounts/{trader_id}")
def get_account_state(trader_id: str):
    return service.get_account_state(trader_id)


@app.get("/traders")
def get_traders_status():
    return service.get_traders_status()


@app.get("/events")
def get_events():
    return service.get_event_log()


@app.get("/scores")
def get_scores():
    return service.score_traders()


@app.get("/leaderboard")
def get_leaderboard():
    return service.get_leaderboard()


# ---------------------------------------------------------------------------
# Bot status — read-only, shows which bots are enabled in config
# ---------------------------------------------------------------------------
@app.get("/liquidity/bots")
def list_liquidity_bots():
    engine = getattr(app.state, "liquidity_engine", None)
    if engine is None:
        return {"status": "NO_ENGINE", "bots": []}
    all_bots = engine.status()
    liquidity_ids = set(service.config.liquidity_bots.enabled)
    return {"status": "OK", "bots": [b for b in all_bots if b["trader_id"] in liquidity_ids]}


@app.get("/trading/bots")
def list_trading_bots():
    engine = getattr(app.state, "liquidity_engine", None)
    if engine is None:
        return {"status": "NO_ENGINE", "bots": []}
    all_bots = engine.status()
    trading_ids = set(service.config.trading_bots.enabled)
    return {"status": "OK", "bots": [b for b in all_bots if b["trader_id"] in trading_ids]}


@app.get("/tournaments")
def list_tournaments():
    return service.list_tournaments()


@app.get("/tournaments/{tournament_id}")
def get_tournament(tournament_id: str):
    return service.get_tournament(tournament_id)


@app.post("/tournaments/{tournament_id}/entries")
def enter_tournament(tournament_id: str, request: TournamentEntryRequest):
    return service.enter_tournament(tournament_id, request.owner_id, request.bot_name, request.version)


@app.post("/tournaments/{tournament_id}/run")
async def run_tournament(tournament_id: str):
    return await service.run_scheduled_tournament(tournament_id)


@app.get("/starter-kit")
def get_starter_kit():
    return service.get_starter_kit()


@app.post("/bots/validate")
def validate_bot(request: BotFilesRequest):
    return service.validate_bot(request.files)


@app.post("/bots/submit")
def submit_bot(request: BotSubmissionRequest):
    return service.submit_bot(request.owner_id, request.bot_name, request.files)


@app.get("/bots/{owner_id}/versions")
def list_bot_versions(owner_id: str, bot_name: Optional[str] = None):
    return service.list_bot_versions(owner_id, bot_name)


@app.get("/bots/{owner_id}/{bot_name}/versions/{version}")
def get_bot_version(owner_id: str, bot_name: str, version: int):
    return service.get_bot_version(owner_id, bot_name, version)


# ---------------------------------------------------------------------------
# Reset endpoint — wipes all state, restarts bots from scratch
# ---------------------------------------------------------------------------
@app.post("/reset")
async def reset_simulator():
    """Reset all exchange state and relaunch bots from a clean slate."""
    await _launch_engine()
    return {"status": "RESET", "message": "Exchange state cleared, bots relaunched"}


# ---------------------------------------------------------------------------
# Order endpoints — async, lock-protected, instant execution (no latency)
# ---------------------------------------------------------------------------
@app.post("/order")
async def place_market_order(order: OrderRequest):
    return await service.place_order(
        side=order.side,
        price=order.price,
        quantity=order.quantity,
        trader_id=order.trader_id,
        symbol=order.symbol,
        venue=order.venue,
    )


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, request: CancelOrderRequest):
    return await service.cancel_order(order_id, request.trader_id)


# ---------------------------------------------------------------------------
# WebSocket streaming endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/stream")
async def stream_market_data(websocket: WebSocket):
    await websocket.accept()
    queue = service.event_log.subscribe()

    try:
        # Send initial snapshot so the client has current state immediately
        await websocket.send_json({
            "type": "snapshot",
            "market": service.get_market_state(),
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        })

        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        service.event_log.unsubscribe(queue)


# ---------------------------------------------------------------------------
# Startup / Shutdown — launch background liquidity bots
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_liquidity_bots():
    """Reset state and launch all bots fresh on every server start."""
    await _launch_engine()
    app.state.logger_task = asyncio.create_task(data_logger.start())


@app.on_event("shutdown")
async def shutdown_liquidity_bots():
    engine = getattr(app.state, "liquidity_engine", None)
    if engine:
        await engine.stop()
    leaderboard_task = getattr(app.state, "leaderboard_task", None)
    if leaderboard_task:
        leaderboard_task.cancel()
    await data_logger.stop()
    logger_task = getattr(app.state, "logger_task", None)
    if logger_task:
        logger_task.cancel()
