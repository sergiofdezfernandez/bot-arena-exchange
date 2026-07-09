"""market_spy.py — Monitor de actividad del exchange bajo demanda.

Se conecta al WebSocket local, escucha eventos FILL y ORDERBOOK_UPDATE
durante N segundos, imprime un resumen en texto claro, y se cierra.

Uso:
    python market_spy.py                          # 5s por defecto
    python market_spy.py --duration 10            # 10 segundos
    python market_spy.py --trader Juan_Alpha      # filtrar por trader
    python market_spy.py --trader Juan_Alpha -d 15
"""

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict


WS_URL = "ws://127.0.0.1:8000/ws/stream"


async def spy(duration: float, trader_filter: str | None = None):
    fills: list[dict] = []
    book_snapshots: list[dict] = []
    start_ts = time.time()

    try:
        import websockets  # type: ignore
    except ImportError:
        print("[market_spy] ERROR: 'websockets' no está instalado.")
        print("           Instálalo con: pip install websockets")
        sys.exit(1)

    print(f"[market_spy] Conectando a {WS_URL} ...")
    try:
        async with websockets.connect(WS_URL) as ws:
            connected_at = time.time()
            print(f"[market_spy] Conectado. Escuchando {duration}s ...\n")

            while (time.time() - connected_at) < duration:
                remaining = duration - (time.time() - connected_at)
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                msg = json.loads(raw)
                etype = msg.get("event_type") or msg.get("type")

                if etype == "FILL":
                    # Aplica filtro si se especificó
                    payload = msg.get("payload", {})
                    buyer = str(payload.get("buyer_id", ""))
                    seller = str(payload.get("seller_id", ""))
                    if trader_filter and trader_filter not in (buyer, seller):
                        continue
                    fills.append(msg)

                elif etype == "ORDERBOOK_UPDATE":
                    book_snapshots.append(msg.get("payload", msg))

        elapsed = time.time() - connected_at
        print(f"[market_spy] Sesión finalizada ({elapsed:.1f}s reales)\n")

    except (ConnectionRefusedError, OSError) as e:
        print(f"[market_spy] ERROR: No se pudo conectar a {WS_URL}")
        print(f"           ¿Está el servidor corriendo? ({e})")
        sys.exit(1)

    # ── Resumen ──────────────────────────────────────────────────────────
    print("=" * 64)
    print(" RESUMEN DE ACTIVIDAD")
    print("=" * 64)

    # Fills
    print(f"\n[FILLS] Totales capturados: {len(fills)}")
    if trader_filter:
        print(f"     (filtrado por trader: {trader_filter})")

    if fills:
        traders = defaultdict(int)
        total_volume = 0
        for f in fills:
            p = f.get("payload", {})
            buyer = str(p.get("buyer_id", ""))
            seller = str(p.get("seller_id", ""))
            traders[buyer] += 1
            traders[seller] += 1
            total_volume += p.get("quantity", 0)

        print(f"     Volumen total: {total_volume} unidades")
        print(f"\n     Participación por trader:")
        for tid, count in sorted(traders.items(), key=lambda x: -x[1]):
            marker = " ◀◀" if trader_filter and tid == trader_filter else ""
            print(f"       {tid}: {count} operaciones{marker}")

        print(f"\n     Últimos 3 fills:")
        for f in fills[-3:]:
            p = f.get("payload", {})
            print(f"       {p.get('buyer_id','?')} ← compra a {p.get('seller_id','?')} "
                  f"  qty={p.get('quantity','?')}  price={p.get('price','?')}")

    # Order Book
    print(f"\n[ORDER BOOK] Snapshots: {len(book_snapshots)}")
    if book_snapshots:
        first = book_snapshots[0]
        last = book_snapshots[-1]
        bb_first = first.get("best_bid")
        ba_first = first.get("best_ask")
        bb_last = last.get("best_bid")
        ba_last = last.get("best_ask")

        def _fmt(v):
            return f"{v:.2f}" if isinstance(v, (int, float)) else "—"

        print(f"     Best bid: {_fmt(bb_first)} → {_fmt(bb_last)}")
        print(f"     Best ask: {_fmt(ba_first)} → {_fmt(ba_last)}")

        if len(book_snapshots) >= 2 and bb_last is not None and ba_last is not None:
            spreads = []
            for s in book_snapshots:
                b = s.get("best_bid")
                a = s.get("best_ask")
                if b is not None and a is not None:
                    spreads.append(a - b)
            if spreads:
                avg_spread = sum(spreads) / len(spreads)
                min_spread = min(spreads)
                max_spread = max(spreads)
                print(f"     Spread (bid-ask): avg={avg_spread:.1f}  min={min_spread}  max={max_spread}")

    print("\n" + "=" * 64)
    if not fills and not book_snapshots:
        print(" [!] No se recibio ningun evento. El torneo esta corriendo?")
    print()


def main():
    parser = argparse.ArgumentParser(description="Market Spy — monitor de actividad del exchange")
    parser.add_argument("-d", "--duration", type=float, default=5.0,
                        help="Duración de la escucha en segundos (default: 5)")
    parser.add_argument("-t", "--trader", type=str, default=None,
                        help="Filtrar fills por trader_id (ej: Juan_Alpha)")
    args = parser.parse_args()

    asyncio.run(spy(args.duration, args.trader))


if __name__ == "__main__":
    main()