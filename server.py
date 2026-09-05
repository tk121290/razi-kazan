from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("razi")

DB_PATH = Path(__file__).parent / "leaderboard.db"


# ── Başlangıç / Kapatma ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                level   INTEGER NOT NULL,
                room_id TEXT    DEFAULT '',
                ts      INTEGER NOT NULL
            )
        """)
        await db.commit()
    log.info("Database ready: %s", DB_PATH)
    yield


app = FastAPI(title="Ebû Bekir er-Râzî'nin Kazanı — Sunucu", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# room_id → {"desktop": WebSocket | None, "mobile": set[WebSocket]}
rooms: dict[str, dict[str, Any]] = defaultdict(lambda: {"desktop": None, "mobile": set()})


# ── Yardımcılar ───────────────────────────────────────────────────────────────

async def safe_send(ws: WebSocket, message: dict[str, Any]) -> None:
    try:
        await ws.send_json(message)
    except Exception:
        pass


async def broadcast_mobile(room: dict, message: dict[str, Any]) -> None:
    for mobile in list(room["mobile"]):
        await safe_send(mobile, message)


# ── Statik sayfalar ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse("""
        <!doctype html>
        <html lang="tr">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Ebû Bekir er-Râzî'nin Kazanı</title>
          <style>
            body { margin:0; min-height:100vh; display:grid; place-items:center;
                   background:#110d0b; color:#f0e4c8; font:18px Georgia,serif; }
            main { max-width:520px; padding:32px; text-align:center; }
            h1   { color:#d4a848; margin-bottom:12px; }
            p    { line-height:1.7; color:#a09278; }
            a    { color:#d4a848; }
            code { background:#211810; padding:2px 6px; border-radius:4px; color:#d4a848; }
          </style>
        </head>
        <body><main>
          <h1>Ebû Bekir er-Râzî'nin Kazanı</h1>
          <p>Sunucu çalışıyor. Oyuna bağlanmak için Pygame penceresindeki QR kodunu telefonunla okut.</p>
          <p><a href="/leaderboard">🏆 Liderlik Tablosu</a></p>
        </main></body>
        </html>
    """)


@app.get("/play", response_class=HTMLResponse)
async def play_without_room() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Oda kodu eksik</h1><p>Telefonunu Pygame penceresindeki QR koduyla bağlayın.</p>",
        status_code=400,
    )


@app.get("/play/{room_id}", response_class=HTMLResponse)
async def play(request: Request, room_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="play.html",
        context={"room_id": room_id.upper()},
    )


# ── Liderlik Tablosu ──────────────────────────────────────────────────────────

@app.post("/api/scores")
async def post_score(request: Request) -> dict[str, Any]:
    """desktop_game.py'nin save_score() fonksiyonu buraya POST atar."""
    try:
        payload = await request.json()
        level   = int(payload.get("level", 0))
        room_id = str(payload.get("room_id", ""))[:16]
        ts      = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO scores (level, room_id, ts) VALUES (?, ?, ?)",
                (level, room_id, ts),
            )
            await db.commit()
        log.info("Score saved: level=%d room=%s", level, room_id)
        return {"ok": True}
    except Exception as e:
        log.error("Score save error: %s", e)
        return {"ok": False, "error": str(e)}


@app.get("/api/scores")
async def get_scores() -> list[dict]:
    """JSON olarak liderlik tablosunu döndür."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT level, room_id, ts FROM scores ORDER BY level DESC, ts DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard() -> HTMLResponse:
    """Görsel liderlik tablosu."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT level, room_id, ts FROM scores ORDER BY level DESC, ts DESC LIMIT 20"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        total_cur = await db.execute("SELECT COUNT(*) FROM scores")
        total = (await total_cur.fetchone())[0]

    medals = ["🥇", "🥈", "🥉"]

    rows_html = ""
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        ts_str = time.strftime("%d.%m %H:%M", time.localtime(row["ts"]))
        room   = row["room_id"] or "—"
        rows_html += f"""
        <tr class="{'top' if i < 3 else ''}">
          <td class="rank">{medal}</td>
          <td class="level">{row['level']:02d}</td>
          <td class="room">{room}</td>
          <td class="time">{ts_str}</td>
        </tr>"""

    return HTMLResponse(f"""
<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>Liderlik Tablosu — Ebû Bekir er-Râzî'nin Kazanı</title>
  <style>
    :root {{
      --bg:#110d0b; --panel:#211810; --border:#6a4628;
      --ink:#f0e4c8; --muted:#a09278; --gold:#d4a848;
      --green:#52b982; --red:#c84b3c;
    }}
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    body {{
      min-height:100vh; background:var(--bg); color:var(--ink);
      font-family:Georgia,serif; padding:32px 16px;
    }}
    .container {{ max-width:640px; margin:0 auto; }}
    header {{ text-align:center; margin-bottom:32px; }}
    h1 {{ font-size:clamp(1.4rem,5vw,2rem); color:var(--gold); margin-bottom:6px; }}
    .subtitle {{ color:var(--muted); font-size:.9rem; }}
    .total {{ display:inline-block; margin-top:10px; font-size:.8rem;
              color:var(--muted); border:1px solid var(--border);
              padding:4px 12px; border-radius:20px; }}
    table {{
      width:100%; border-collapse:collapse;
      background:var(--panel); border:1px solid var(--border);
      border-radius:12px; overflow:hidden;
    }}
    th {{
      padding:12px 16px; text-align:left; font-size:.75rem;
      letter-spacing:.1em; color:var(--muted);
      border-bottom:1px solid var(--border);
      background:#1a100a;
    }}
    td {{ padding:12px 16px; border-bottom:1px solid #2a1a10; }}
    tr:last-child td {{ border-bottom:none; }}
    tr.top td {{ color:var(--gold); }}
    tr:hover td {{ background:#2a1810; }}
    .rank  {{ font-size:1.2rem; width:48px; }}
    .level {{ font-size:1.6rem; font-weight:bold; width:72px; color:var(--gold); }}
    tr.top .level {{ color:#f5d060; text-shadow:0 0 12px #f5d06066; }}
    .room  {{ color:var(--muted); font-size:.85rem; }}
    .time  {{ color:var(--muted); font-size:.78rem; white-space:nowrap; }}
    .empty {{ text-align:center; padding:48px; color:var(--muted); font-style:italic; }}
    .refresh {{ text-align:center; margin-top:16px; font-size:.75rem; color:var(--muted); }}
    a {{ color:var(--gold); text-decoration:none; }}
  </style>
</head>
<body>
<div class="container">
  <header>
    <h1>🏺 Liderlik Tablosu</h1>
    <div class="subtitle">Ebû Bekir er-Râzî'nin Kazanı</div>
    <span class="total">{total} toplam oyun</span>
  </header>

  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>SEVİYE</th>
        <th>ODA</th>
        <th>ZAMAN</th>
      </tr>
    </thead>
    <tbody>
      {'<tr><td colspan="4" class="empty">Henüz kayıt yok. İlk oyunu oyna!</td></tr>' if not rows else rows_html}
    </tbody>
  </table>

  <p class="refresh">Sayfa her 15 saniyede otomatik yenilenir · <a href="/leaderboard">Şimdi yenile</a></p>
</div>
</body>
</html>
""")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: str) -> None:
    room_id = room_id.upper()
    role    = websocket.query_params.get("role", "mobile")
    await websocket.accept()
    room = rooms[room_id]

    if role == "desktop":
        old = room["desktop"]
        if isinstance(old, WebSocket):
            await safe_send(old, {"type": "error", "message": "Replaced by a new game session."})
        room["desktop"] = websocket
        log.info("Desktop connected — room %s", room_id)
        await safe_send(websocket, {"type": "desktop_connected", "room_id": room_id})

    else:  # mobile
        mobile_set = room["mobile"]
        assert isinstance(mobile_set, set)

        if mobile_set:
            log.info("Extra mobile rejected — room %s already has a player", room_id)
            await safe_send(websocket, {
                "type": "error",
                "message": "Bu odada zaten bir oyuncu var.",
            })
            await websocket.close(code=4001)
            return

        mobile_set.add(websocket)
        log.info("Mobile connected — room %s", room_id)

        desktop = room["desktop"]
        if isinstance(desktop, WebSocket):
            await safe_send(desktop, {"type": "player_connected"})
        await safe_send(websocket, {"type": "connected", "room_id": room_id})

    try:
        while True:
            message = await websocket.receive_json()
            desktop = room["desktop"]

            if role != "desktop":
                if isinstance(desktop, WebSocket):
                    await safe_send(desktop, {"type": "button", "button": message.get("button")})
            else:
                await broadcast_mobile(room, message)

    except WebSocketDisconnect:
        pass
    finally:
        if role == "desktop":
            if room["desktop"] is websocket:
                room["desktop"] = None
                log.info("Desktop disconnected — room %s", room_id)
        else:
            mobile_set = room["mobile"]
            assert isinstance(mobile_set, set)
            mobile_set.discard(websocket)
            log.info("Mobile disconnected — room %s", room_id)
            desktop = room["desktop"]
            if isinstance(desktop, WebSocket):
                await safe_send(desktop, {"type": "player_disconnected"})

        if room["desktop"] is None and not room["mobile"]:
            rooms.pop(room_id, None)
            log.info("Room %s cleaned up", room_id)
