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


@app.get("/config")
def get_config():
    return service.get_tournament_config()


@app.get("/snapshot")
def get_market_snapshot():
    return service.get_market_snapshot()


@app.get("/traders")
def get_traders_status():
    return service.get_traders_status()


@app.get("/events")
def get_events():
    return service.get_event_log()


@app.get("/scores")
def get_scores():
    return service.score_traders()


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
