"""Liquidity Engine — runs server-side bots as asyncio background tasks.

Each bot runs in its own asyncio.Task. The engine acquires the exchange-level
lock before invoking each bot's on_tick, so the sync internal methods
(_place_order_sync / _cancel_order_sync) are safe to call.

Bots sleep on a stochastic schedule driven by a Marked Hawkes Process with
Dynamic Decay. A global shock_event wakes all bots when a MARKET_SHOCK is
detected by the ExchangeService, causing them to recalculate their intensity
lambda(t) and generate a new, shorter sleep interval.

Concurrency constraints:
- No while-True loops without yielding (uses asyncio.wait with interruptions)
- No Ogata's thinning algorithm (uses exponential variates directly)
- Bot base_intensity (mu) is read per-instance, defaulting to 0.5 if not declared.
"""

import asyncio
import math
import random


class LiquidityEngine:
    """Manages background liquidity-provider bots that quote continuously.

    Bots are stored in a registry keyed by trader_id. Each bot can be toggled
    on/off at runtime via enable(trader_id) / disable(trader_id).

    Shock-driven Hawkes scheduling:
    - Each bot sleeps for an exponential variate drawn from its current λ(t)
    - A global asyncio.Event is set when a MARKET_SHOCK occurs
    - If a bot's sleep is interrupted by a shock, it recalculates λ with the
      shock's α and β, then generates a shorter sleep
    """

    # ── Hawkes parameter defaults for bots that don't declare base_intensity ──
    DEFAULT_BASE_INTENSITY = 0.25   # ~1 tick every 4 seconds (conservative fallback)

    def __init__(self, exchange_service):
        self._service = exchange_service
        # _registry: trader_id -> {"bot": bot, "task": asyncio.Task | None, "enabled": bool}
        self._registry: dict = {}
        # _pending_order_ids: trader_id -> order_id | None
        # Tracks the last placed order for think()-style legacy bots so we can
        # cancel it before placing a new one, preventing orphan orders that
        # accumulate and cause self-matching (wash trades).
        self._pending_order_ids: dict = {}

        # ── Hawkes shock broadcast ────────────────────────────────────
        # When set, all sleeping bots wake and recalculate their intensity.
        self._shock_event = asyncio.Event()
        self._last_shock_alpha: float = 0.0
        self._last_shock_beta: float = 1.0

        # Track the last time a shock arrived (using the asyncio event loop
        # monotonic clock, which is safe because it's only used for dt
        # calculations within a single event loop).
        self._last_shock_loop_time: float = 0.0

    # ── Bot lifecycle ──────────────────────────────────────────────────────
    def register(self, bot):
        """Register a bot instance. It will be launched when start() is called."""
        self._registry[bot.trader_id] = {
            "bot": bot,
            "task": None,
            "enabled": True,
        }

    def disable(self, trader_id: str) -> dict:
        """Disable a running bot by cancelling its task. Returns status."""
        entry = self._registry.get(trader_id)
        if entry is None:
            return {"status": "NOT_FOUND", "trader_id": trader_id}
        if not entry["enabled"]:
            return {"status": "ALREADY_DISABLED", "trader_id": trader_id}
        entry["enabled"] = False
        if entry["task"] and not entry["task"].done():
            entry["task"].cancel()
        print(f"[LiquidityEngine] {trader_id} disabled")
        return {"status": "DISABLED", "trader_id": trader_id}

    def enable(self, trader_id: str) -> dict:
        """Re-enable a disabled bot by launching a new task. Returns status."""
        entry = self._registry.get(trader_id)
        if entry is None:
            return {"status": "NOT_FOUND", "trader_id": trader_id}
        if entry["enabled"]:
            return {"status": "ALREADY_ENABLED", "trader_id": trader_id}
        entry["enabled"] = True
        entry["task"] = asyncio.create_task(self._run_bot(entry["bot"]))
        print(f"[LiquidityEngine] {trader_id} enabled")
        return {"status": "ENABLED", "trader_id": trader_id}

    def status(self) -> list:
        """Return the status of all registered bots."""
        return [
            {
                "trader_id": tid,
                "class_name": type(entry["bot"]).__name__,
                "enabled": entry["enabled"],
            }
            for tid, entry in self._registry.items()
        ]

    # ── Shock publishing (called by ExchangeService on MARKET_SHOCK) ────────
    def publish_shock(self, alpha: float, beta: float):
        """Wake every sleeping bot and provide the latest Hawkes parameters.

        Called by ExchangeService._notify_shock() when a MARKET_SHOCK is
        detected (volume shock or liquidity shock).

        Replaces the internal asyncio.Event so that bots currently sleeping
        on the old event are woken, and bots looping back will wait on the
        fresh (unset) event.
        """
        self._last_shock_alpha = alpha
        self._last_shock_beta = beta
        self._last_shock_loop_time = asyncio.get_event_loop().time()

        # Set the old event to wake every coroutine waiting on it
        old_event = self._shock_event
        old_event.set()

        # Replace with a fresh event for the next shock cycle
        self._shock_event = asyncio.Event()

    # ── Core bot execution loop (Hawkes-driven stochastic sleep) ───────────
    async def _run_bot(self, bot):
        """Run a bot using Marked Hawkes Process arrival scheduling.

        Each iteration:
        1. Compute current intensity λ(t) = μ + α·e^(-β·Δt)
        2. Draw an exponential sleep ~ Exp(λ)
        3. Wait on [sleep, shock_event] — whichever completes first
        4. If shocked → absorb α, β, re-enter wait immediately
        5. If sleep completed → execute on_tick/think under exchange lock
        """

        # ── Build sync API proxy ────────────────────────────────────────
        class SyncApiProxy:
            """Synchronous proxy — must only be used while exchange._lock is held."""

            def __init__(self, service, trader_id):
                self.svc = service
                self.tid = trader_id

            def get_order_book(self, symbol, venue):
                state = self.svc.get_market_state(symbol=symbol, venue=venue)
                return {
                    "best_bid": state.get("best_bid"),
                    "best_ask": state.get("best_ask"),
                    "snapshot": state.get("snapshot"),
                    "recent_trades": state.get("recent_trades"),
                }

            def get_account(self):
                return self.svc.manager.get_account(self.tid)

            def get_position(self, symbol):
                account = self.svc.manager.get_account(self.tid)
                return account.positions.get(symbol, 0)

            def get_shock_state(self):
                """Return the current Hawkes shock intensity (0.0 = no active shock).

                The intensity is the remaining Hawkes jump magnitude at the
                current instant: α·e^(-β·Δt).  Bots should widen spreads
                when this value is non-zero to compensate for elevated
                adverse-selection risk.
                """
                engine = self.svc._engine_ref()
                if engine is None:
                    return 0.0
                now = asyncio.get_event_loop().time()
                dt = max(0.0, now - engine._last_shock_loop_time)
                if dt > 5.0:   # shock fully decayed after 5s
                    return 0.0
                alpha = engine._last_shock_alpha
                beta = engine._last_shock_beta
                if beta <= 0:
                    return 0.0
                return alpha * math.exp(-beta * dt)

            def place_order(self, side, price, quantity, symbol, venue):
                res = self.svc._place_order_sync(side, price, quantity, self.tid, symbol, venue)
                if "order_id" in res:
                    return res["order_id"]
                return None

            def cancel_order(self, order_id):
                res = self.svc._cancel_order_sync(order_id, self.tid)
                if isinstance(res, dict) and res.get("status") == "REJECTED":
                    reason = res.get("reason", "unknown")
                    if reason not in ("order not found", "order cannot be cancelled"):
                        raise RuntimeError(f"Cancel rejected: {reason}")
                return res

        api = SyncApiProxy(self._service, bot.trader_id)

        # ── Per-bot Hawkes state ────────────────────────────────────────
        # Base intensity μ: read from bot attribute or use engine default
        mu = getattr(bot, "base_intensity", self.DEFAULT_BASE_INTENSITY)
        if mu <= 0:
            mu = self.DEFAULT_BASE_INTENSITY

        # Current shock parameters (initialized to neutral)
        alpha = 0.0
        beta = 1.0
        last_shock_time = asyncio.get_event_loop().time()

        while True:
            # ══════════════════════════════════════════════════════════════
            # Check enabled state
            # ══════════════════════════════════════════════════════════════
            entry = self._registry.get(bot.trader_id)
            if entry is None or not entry["enabled"]:
                await asyncio.sleep(0.5)
                continue

            # ══════════════════════════════════════════════════════════════
            # Compute current Hawkes intensity λ(t)
            # λ(t) = μ + α · exp(-β · (t - t_shock))
            # Clamp to a floor so sleep doesn't blow up to infinity
            # ══════════════════════════════════════════════════════════════
            now = asyncio.get_event_loop().time()
            dt = max(0.0, now - last_shock_time)
            hawkes_jump = alpha * math.exp(-beta * dt)
            lambda_t = mu + hawkes_jump
            lambda_t = max(lambda_t, 0.02)   # floor: max ~50s sleep (allows slow bots)
            lambda_t = min(lambda_t, 50.0)   # cap: min ~20ms sleep

            # ══════════════════════════════════════════════════════════════
            # Draw exponential sleep time ~ Exp(λ)
            # ══════════════════════════════════════════════════════════════
            sleep_time = random.expovariate(lambda_t)
            sleep_time = max(sleep_time, 0.05)  # strict floor: 50ms minimum
            sleep_time = min(sleep_time, 10.0)  # safety cap at 10s

            # ══════════════════════════════════════════════════════════════
            # Interruptible wait: sleep vs shock_event
            # Snapshot the current event reference so we don't race with
            # publish_shock() replacing it.
            # ══════════════════════════════════════════════════════════════
            shock_ev = self._shock_event
            sleep_task = asyncio.create_task(asyncio.sleep(sleep_time))
            shock_task = asyncio.create_task(shock_ev.wait())

            done, pending = await asyncio.wait(
                [sleep_task, shock_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel whichever didn't complete
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # ══════════════════════════════════════════════════════════════
            # If interrupted by a shock, absorb the new α,β and re-enter
            # the sleep loop immediately (shorter sleep due to higher λ)
            # ══════════════════════════════════════════════════════════════
            if shock_task in done:
                alpha = self._last_shock_alpha
                beta = self._last_shock_beta
                last_shock_time = asyncio.get_event_loop().time()
                continue

            # ══════════════════════════════════════════════════════════════
            # Sleep completed naturally — execute the trading tick
            # ══════════════════════════════════════════════════════════════
            try:
                async with self._service._lock:
                    # Re-check enabled after acquiring lock
                    entry = self._registry.get(bot.trader_id)
                    if entry is None or not entry["enabled"]:
                        await asyncio.sleep(0.5)
                        continue

                    if hasattr(bot, "on_tick"):
                        bot.on_tick(api)
                    elif hasattr(bot, "think"):
                        # Cancel the bot's previous order before placing a new one.
                        prev_id = self._pending_order_ids.get(bot.trader_id)
                        if prev_id is not None:
                            try:
                                api.cancel_order(prev_id)
                            except Exception:
                                pass
                            self._pending_order_ids[bot.trader_id] = None

                        state = self._service.get_market_state()
                        best_bid = state.get("best_bid")
                        best_ask = state.get("best_ask")
                        order = bot.think(best_bid, best_ask)
                        if order:
                            new_id = api.place_order(
                                order["side"],
                                order["price"],
                                order["quantity"],
                                getattr(bot, "symbol", "AAPL"),
                                getattr(bot, "venue", "VENUE_1"),
                            )
                            if new_id is not None:
                                self._pending_order_ids[bot.trader_id] = new_id
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[LiquidityEngine] {bot.trader_id} error: {e}")

            # Update last_shock_time so the decay term exp(-β·Δt) starts
            # counting from the moment the tick completed
            last_shock_time = asyncio.get_event_loop().time()

    async def start(self):
        """Launch all registered bots as background asyncio tasks."""
        for entry in self._registry.values():
            if entry["enabled"]:
                entry["task"] = asyncio.create_task(self._run_bot(entry["bot"]))
        active = sum(1 for e in self._registry.values() if e["enabled"])
        print(f"[LiquidityEngine] Started {active} background bot(s)")

    async def stop(self):
        """Cancel all background bot tasks."""
        for entry in self._registry.values():
            if entry["task"] and not entry["task"].done():
                entry["task"].cancel()
        tasks = [e["task"] for e in self._registry.values() if e["task"]]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for entry in self._registry.values():
            entry["task"] = None
        print("[LiquidityEngine] All bots stopped")