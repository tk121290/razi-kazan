from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Razi's Cauldron Controller")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

rooms: dict[str, dict[str, set[WebSocket] | WebSocket | None]] = defaultdict(
    lambda: {"desktop": None, "mobile": set()}
)


async def send_json(socket: WebSocket, message: dict[str, Any]) -> None:
    try:
        await socket.send_json(message)
    except Exception:
        pass


@app.get("/play/{room_id}", response_class=HTMLResponse)
async def play(request: Request, room_id: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="play.html",
        context={"room_id": room_id.upper()},
    )


@app.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: str) -> None:
    room_id = room_id.upper()
    role = websocket.query_params.get("role", "mobile")
    await websocket.accept()
    room = rooms[room_id]

    if role == "desktop":
        old_desktop = room["desktop"]
        if isinstance(old_desktop, WebSocket):
            await send_json(old_desktop, {"type": "error", "message": "Replaced by a new game."})
        room["desktop"] = websocket
        await send_json(websocket, {"type": "desktop_connected", "room_id": room_id})
    else:
        mobile_sockets = room["mobile"]
        assert isinstance(mobile_sockets, set)
        mobile_sockets.add(websocket)
        desktop = room["desktop"]
        if isinstance(desktop, WebSocket):
            await send_json(desktop, {"type": "player_connected"})
        await send_json(websocket, {"type": "connected", "room_id": room_id})

    try:
        while True:
            message = await websocket.receive_json()
            desktop = room["desktop"]
            if role != "desktop" and isinstance(desktop, WebSocket):
                await send_json(desktop, {"type": "button", "button": message.get("button")})
            elif role == "desktop":
                mobile_sockets = room["mobile"]
                assert isinstance(mobile_sockets, set)
                for mobile in list(mobile_sockets):
                    await send_json(mobile, message)
    except WebSocketDisconnect:
        pass
    finally:
        if role == "desktop":
            if room["desktop"] is websocket:
                room["desktop"] = None
        else:
            mobile_sockets = room["mobile"]
            assert isinstance(mobile_sockets, set)
            mobile_sockets.discard(websocket)
            desktop = room["desktop"]
            if isinstance(desktop, WebSocket):
                await send_json(desktop, {"type": "player_disconnected"})
        if room["desktop"] is None and not room["mobile"]:
            rooms.pop(room_id, None)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
