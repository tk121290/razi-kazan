from __future__ import annotations

import html
import logging
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("razi")

DB_PATH = Path(__file__).parent / "leaderboard.db"

# ── Güvenlik Sabitleri ────────────────────────────────────────────────────────
ROOM_ID_PATTERN = re.compile(r"^[A-Z0-9_-]{3,12}$")
VALID_BUTTON_PATTERN = re.compile(r"^[a-z0-9_]{1,32}$")
MAX_ACTIVE_ROOMS = 100
MAX_MSG_RATE_PER_SEC = 25  # Bir WebSocket'ten saniyede izin verilen maks mesaj
SCORE_POST_LIMIT_PER_MIN = 10

# IP bazlı rate-limiter
score_rate_tracker: dict[str, list[float]] = defaultdict(list)


# ── Başlangıç / Kapatma ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT    DEFAULT 'Simyacı',
                level       INTEGER NOT NULL,
                max_combo   INTEGER DEFAULT 0,
                room_id     TEXT    DEFAULT '',
                ts          INTEGER NOT NULL
            )
        """)
        cursor = await db.execute("PRAGMA table_info(scores)")
        cols = [c[1] for c in await cursor.fetchall()]
        if "player_name" not in cols:
            await db.execute("ALTER TABLE scores ADD COLUMN player_name TEXT DEFAULT 'Simyacı'")
        if "max_combo" not in cols:
            await db.execute("ALTER TABLE scores ADD COLUMN max_combo INTEGER DEFAULT 0")
        await db.commit()
    log.info("Database ready: %s", DB_PATH)
    yield


app = FastAPI(title="Ebû Bekir er-Râzî'nin Kazanı — Sunucu", lifespan=lifespan)


# ── Güvenlik Başlıkları Middleware'i ─────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# room_id → {"desktop": WebSocket | None, "players": dict[str, dict], "sockets": dict[WebSocket, str]}
rooms: dict[str, dict[str, Any]] = {}


# ── Yardımcılar ───────────────────────────────────────────────────────────────

async def safe_send(ws: WebSocket, message: dict[str, Any]) -> None:
    try:
        await ws.send_json(message)
    except Exception:
        pass


async def broadcast_mobile(room: dict, message: dict[str, Any]) -> None:
    target = message.get("target")
    players = room.get("players", {})
    if target and target in players:
        ws = players[target].get("ws")
        if ws:
            await safe_send(ws, message)
        return

    # Broadcast to all players in room
    for p_info in list(players.values()):
        ws = p_info.get("ws")
        if ws:
            await safe_send(ws, message)


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
    clean_id = room_id.strip().upper()
    if not ROOM_ID_PATTERN.match(clean_id):
        return HTMLResponse(
            "<h1>Geçersiz Oda Kodu</h1><p>Oda kodu 3-12 karakter ve alfanümerik olmalıdır.</p>",
            status_code=400,
        )
    return templates.TemplateResponse(
        request=request,
        name="play.html",
        context={"room_id": clean_id},
    )


@app.get("/razi_elements.json")
async def get_razi_elements():
    path = Path(__file__).parent / "razi_elements.json"
    if path.exists():
        return FileResponse(path, media_type="application/json")
    return JSONResponse({"error": "not found"}, status_code=404)


# ── Liderlik Tablosu ──────────────────────────────────────────────────────────

@app.post("/api/scores")
async def post_score(request: Request) -> JSONResponse:
    """desktop_game.py'nin save_score() fonksiyonu buraya POST atar."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    
    # Rate limit: 1 dakikada maks 10 skor kaydı
    timestamps = [t for t in score_rate_tracker[client_ip] if now - t < 60.0]
    if len(timestamps) >= SCORE_POST_LIMIT_PER_MIN:
        return JSONResponse({"ok": False, "error": "Çok fazla skor isteği. Lütfen bekleyin."}, status_code=429)
    timestamps.append(now)
    score_rate_tracker[client_ip] = timestamps

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse({"ok": False, "error": "Geçersiz veri biçimi"}, status_code=400)

        # Seviye sınır kontrolü (1 - 100 arası mantıksal sınır)
        level_raw = payload.get("level")
        if not isinstance(level_raw, int) or not (1 <= level_raw <= 100):
            return JSONResponse({"ok": False, "error": "Geçersiz seviye değeri (1-100)"}, status_code=400)
        level = level_raw

        # Oda kodu format kontrolü
        room_raw = str(payload.get("room_id", "")).strip().upper()[:12]
        if room_raw and not ROOM_ID_PATTERN.match(room_raw):
            return JSONResponse({"ok": False, "error": "Geçersiz oda kodu formatı"}, status_code=400)
        room_id = room_raw

        # Simyacı adı doğrulama ve temizleme
        player_name_raw = str(payload.get("player_name", "Simyacı")).strip()
        player_name = re.sub(r"[^\w\s\-çğıöşüÇĞİÖŞÜ]", "", player_name_raw)[:24].strip()
        if not player_name:
            player_name = "Simyacı"

        # Kombo rekoru
        max_combo_raw = payload.get("max_combo", 0)
        max_combo = int(max_combo_raw) if isinstance(max_combo_raw, (int, float)) and 0 <= max_combo_raw <= 200 else 0

        ts = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO scores (player_name, level, max_combo, room_id, ts) VALUES (?, ?, ?, ?, ?)",
                (player_name, level, max_combo, room_id, ts),
            )
            await db.commit()
        log.info("Score saved: player=%s level=%d combo=%d room=%s", player_name, level, max_combo, room_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        log.error("Score save error: %s", e)
        return JSONResponse({"ok": False, "error": "Kayıt sırasında hata oluştu"}, status_code=400)


@app.get("/api/scores")
async def get_scores() -> list[dict]:
    """JSON olarak liderlik tablosunu döndür."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT player_name, level, max_combo, room_id, ts FROM scores ORDER BY level DESC, max_combo DESC, ts DESC LIMIT 50"
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


def get_alchemical_title(lvl: int) -> str:
    if lvl >= 100: return "İksir-i Âzam Üstadı 🌌"
    if lvl >= 75:  return "Şeyhü'l-Etıbbâ 👑"
    if lvl >= 50:  return "Büyük Hekim 📜"
    if lvl >= 25:  return "Usta Simyager ⚗️"
    if lvl >= 10:  return "Kalfa Tabip 🧪"
    return "Çırak Simyacı 🕯️"


@app.get("/leaderboard", response_class=HTMLResponse)
async def leaderboard() -> HTMLResponse:
    """Görsel liderlik tablosu (XSS korumalı, unvanlı ve kombolu)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT player_name, level, max_combo, room_id, ts FROM scores ORDER BY level DESC, max_combo DESC, ts DESC LIMIT 20"
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        total_cur = await db.execute("SELECT COUNT(*) FROM scores")
        total = (await total_cur.fetchone())[0]

    medals = ["🥇", "🥈", "🥉"]

    rows_html = ""
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i+1}."
        ts_str = time.strftime("%d.%m %H:%M", time.localtime(row["ts"]))
        
        # Stored XSS koruması: html.escape
        safe_name = html.escape(str(row["player_name"] or "Simyacı"))
        safe_room = html.escape(str(row["room_id"] or "—"))
        safe_level = int(row["level"])
        safe_combo = int(row["max_combo"] or 0)
        title = get_alchemical_title(safe_level)

        combo_str = f"🔥 x{safe_combo}" if safe_combo >= 2 else "—"

        rows_html += f"""
        <tr class="{'top' if i < 3 else ''}">
          <td class="rank">{medal}</td>
          <td class="player"><strong>{safe_name}</strong><small>{title}</small></td>
          <td class="level">{safe_level:02d}</td>
          <td class="combo">{combo_str}</td>
          <td class="room">{safe_room}</td>
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
    .container {{ max-width:720px; margin:0 auto; }}
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
      padding:12px 14px; text-align:left; font-size:.72rem;
      letter-spacing:.1em; color:var(--muted);
      border-bottom:1px solid var(--border);
      background:#1a100a;
    }}
    td {{ padding:12px 14px; border-bottom:1px solid #2a1a10; }}
    tr:last-child td {{ border-bottom:none; }}
    tr.top td {{ color:var(--gold); }}
    tr:hover td {{ background:#2a1810; }}
    .rank  {{ font-size:1.2rem; width:44px; }}
    .player strong {{ display:block; font-size:.92rem; color:var(--ink); }}
    .player small {{ display:block; font-size:.68rem; color:var(--gold); margin-top:1px; }}
    .level {{ font-size:1.5rem; font-weight:bold; width:64px; color:var(--gold); }}
    tr.top .level {{ color:#f5d060; text-shadow:0 0 12px #f5d06066; }}
    .combo {{ font-size:.82rem; color:var(--gold); font-weight:bold; }}
    .room  {{ color:var(--muted); font-size:.80rem; }}
    .time  {{ color:var(--muted); font-size:.75rem; white-space:nowrap; }}
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
        <th>SİMYACI</th>
        <th>SEVİYE</th>
        <th>KOMBO</th>
        <th>ODA</th>
        <th>ZAMAN</th>
      </tr>
    </thead>
    <tbody>
      {'<tr><td colspan="6" class="empty">Henüz kayıt yok. İlk oyunu oyna!</td></tr>' if not rows else rows_html}
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
    clean_id = room_id.strip().upper()

    # Oda ID format kontrolü
    if not ROOM_ID_PATTERN.match(clean_id):
        await websocket.close(code=4000)
        return

    role = websocket.query_params.get("role", "mobile")
    if role not in ("desktop", "mobile"):
        await websocket.close(code=4000)
        return

    # DoS koruması: Oda sayısı sınırı kontrolü
    if clean_id not in rooms:
        if len(rooms) >= MAX_ACTIVE_ROOMS:
            log.warning("Max active rooms reached (%d). Rejecting %s", MAX_ACTIVE_ROOMS, clean_id)
            await websocket.close(code=4002)
            return
        rooms[clean_id] = {"desktop": None, "players": {}, "sockets": {}}

    await websocket.accept()
    room = rooms[clean_id]

    if role == "desktop":
        old = room["desktop"]
        if isinstance(old, WebSocket):
            await safe_send(old, {"type": "error", "message": "Replaced by a new game session."})
        room["desktop"] = websocket
        log.info("Desktop connected — room %s", clean_id)
        await safe_send(websocket, {"type": "desktop_connected", "room_id": clean_id})

    else:  # mobile
        players = room.setdefault("players", {})
        sockets = room.setdefault("sockets", {})

        # 2 oyuncu sınırı (Çırak 1 ve Çırak 2)
        if "player_1" not in players:
            assigned_id = "player_1"
        elif "player_2" not in players:
            assigned_id = "player_2"
        else:
            log.info("Room %s is full (max 2 players)", clean_id)
            await safe_send(websocket, {
                "type": "error",
                "message": "Bu oda dolu (Maksimum 2 çırak düellosu).",
            })
            await websocket.close(code=4001)
            return

        players[assigned_id] = {
            "ws": websocket,
            "name": f"Çırak {1 if assigned_id == 'player_1' else 2}",
            "emblem": "☿" if assigned_id == "player_1" else "🜍",
            "ready": False,
        }
        sockets[websocket] = assigned_id
        player_num = 1 if assigned_id == "player_1" else 2
        log.info("Mobile %s connected as %s — room %s", assigned_id, players[assigned_id]["name"], clean_id)

        desktop = room.get("desktop")
        player_summary = {pid: {"name": p["name"], "emblem": p["emblem"], "ready": p["ready"]} for pid, p in players.items()}
        if isinstance(desktop, WebSocket):
            await safe_send(desktop, {
                "type": "player_connected",
                "player_id": assigned_id,
                "player_num": player_num,
                "player_count": len(players),
                "players": player_summary,
            })

        await safe_send(websocket, {
            "type": "connected",
            "room_id": clean_id,
            "player_id": assigned_id,
            "player_num": player_num,
            "player_count": len(players),
            "players": player_summary,
        })

        # Diğer bağlı oyuncuya yeni rakibi bildir
        for other_id, other_p in players.items():
            if other_id != assigned_id and other_p.get("ws"):
                await safe_send(other_p["ws"], {
                    "type": "opponent_joined",
                    "player_id": assigned_id,
                    "player_num": player_num,
                    "player_count": len(players),
                    "players": player_summary,
                })

    # Mesaj işleme döngüsü (Rate limiting & Type validation)
    msg_timestamps: list[float] = []

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except Exception:
                break

            if not isinstance(message, dict):
                continue

            # Rate limiting
            now = time.monotonic()
            msg_timestamps = [t for t in msg_timestamps if now - t < 1.0]
            if len(msg_timestamps) >= MAX_MSG_RATE_PER_SEC:
                continue
            msg_timestamps.append(now)

            desktop = room.get("desktop")
            if role != "desktop":
                p_id = room.get("sockets", {}).get(websocket, "player_1")
                msg_type = message.get("type")

                # 1. Buton basımı
                btn = message.get("button")
                if btn is not None:
                    btn_str = str(btn).strip().lower()
                    if VALID_BUTTON_PATTERN.match(btn_str):
                        if isinstance(desktop, WebSocket):
                            await safe_send(desktop, {
                                "type": "button",
                                "player_id": p_id,
                                "button": btn_str,
                            })

                # 2. Lobi isim/amblem güncellemesi
                elif msg_type == "join_lobby":
                    name_raw = str(message.get("name", "")).strip()
                    name = re.sub(r"[^\w\s\-çğıöşüÇĞİÖŞÜ]", "", name_raw)[:20].strip() or f"Çırak {1 if p_id == 'player_1' else 2}"
                    emblem = str(message.get("emblem", "☿"))[:4]
                    if p_id in room.get("players", {}):
                        room["players"][p_id]["name"] = name
                        room["players"][p_id]["emblem"] = emblem

                    player_summary = {pid: {"name": p["name"], "emblem": p["emblem"], "ready": p["ready"]} for pid, p in room["players"].items()}
                    if isinstance(desktop, WebSocket):
                        await safe_send(desktop, {
                            "type": "player_updated",
                            "player_id": p_id,
                            "name": name,
                            "emblem": emblem,
                            "players": player_summary,
                        })
                    for other_id, other_p in room["players"].items():
                        if other_id != p_id and other_p.get("ws"):
                            await safe_send(other_p["ws"], {
                                "type": "opponent_updated",
                                "player_id": p_id,
                                "name": name,
                                "emblem": emblem,
                                "players": player_summary,
                            })

                # 3. Hazır durumu
                elif msg_type == "player_ready":
                    ready_val = bool(message.get("ready", True))
                    if p_id in room.get("players", {}):
                        room["players"][p_id]["ready"] = ready_val

                    player_summary = {pid: {"name": p["name"], "emblem": p["emblem"], "ready": p["ready"]} for pid, p in room["players"].items()}
                    if isinstance(desktop, WebSocket):
                        await safe_send(desktop, {
                            "type": "player_ready",
                            "player_id": p_id,
                            "ready": ready_val,
                            "players": player_summary,
                        })
                    for other_id, other_p in room["players"].items():
                        if other_id != p_id and other_p.get("ws"):
                            await safe_send(other_p["ws"], {
                                "type": "opponent_ready",
                                "player_id": p_id,
                                "ready": ready_val,
                                "players": player_summary,
                            })
            else:
                await broadcast_mobile(room, message)

    except WebSocketDisconnect:
        pass
    finally:
        if role == "desktop":
            if room.get("desktop") is websocket:
                room["desktop"] = None
                log.info("Desktop disconnected — room %s", clean_id)
        else:
            p_id = room.get("sockets", {}).pop(websocket, None)
            if p_id and "players" in room:
                room["players"].pop(p_id, None)
            log.info("Mobile %s disconnected — room %s", p_id, clean_id)
            desktop = room.get("desktop")
            rem_players = room.get("players", {})
            player_summary = {pid: {"name": p["name"], "emblem": p["emblem"], "ready": p["ready"]} for pid, p in rem_players.items()}
            if isinstance(desktop, WebSocket):
                await safe_send(desktop, {
                    "type": "player_disconnected",
                    "player_id": p_id,
                    "player_count": len(rem_players),
                    "players": player_summary,
                })
            for rem_id, rem_p in rem_players.items():
                if rem_p.get("ws"):
                    await safe_send(rem_p["ws"], {
                        "type": "opponent_left",
                        "player_id": p_id,
                        "player_count": len(rem_players),
                        "players": player_summary,
                    })

        # Boşalan odayı temizle
        if room.get("desktop") is None and not room.get("players"):
            rooms.pop(clean_id, None)
            log.info("Room %s cleaned up", clean_id)
