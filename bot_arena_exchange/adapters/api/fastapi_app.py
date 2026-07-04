from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bot_arena_exchange.application.exchange_service import ExchangeService


app = FastAPI(title="app")
service = ExchangeService.with_default_liquidity()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrderRequest(BaseModel):
    side: str
    price: int
    quantity: int
    trader_id: str
    symbol: Optional[str] = None
    venue: Optional[str] = None


class CancelOrderRequest(BaseModel):
    trader_id: str


class TickRequest(BaseModel):
    ticks: int = 1


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
def run_tournament(tournament_id: str):
    return service.run_scheduled_tournament(tournament_id)


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


@app.post("/order")
def place_market_order(order: OrderRequest):
    return service.place_order(
        side=order.side,
        price=order.price,
        quantity=order.quantity,
        trader_id=order.trader_id,
        symbol=order.symbol,
        venue=order.venue,
    )


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, request: CancelOrderRequest):
    return service.cancel_order(order_id, request.trader_id)


@app.post("/tick")
def advance_tick(request: TickRequest):
    return service.advance_tick(request.ticks)
