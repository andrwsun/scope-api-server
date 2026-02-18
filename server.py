"""
Scope API Test Server
- Serves index.html on port 8080
- WebSocket endpoint at ws://localhost:8080/ws  (browser connects here)
- OSC listener on port 9000                      (TouchDesigner/Max sends here)

OSC message format:
  /text       "Hello World"   - text to display
  /text_r     0.8             - red   (0.0 - 1.0)
  /text_g     0.2             - green (0.0 - 1.0)
  /text_b     1.0             - blue  (0.0 - 1.0)
  /bg_opacity 0.5             - background opacity (0.0 - 1.0)

Run with:
  uv run --with "fastapi[standard]" --with python-osc python server.py
"""

import asyncio
import json
import os
import threading
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer

OSC_PORT   = int(os.environ.get("OSC_PORT",  9000))
HTTP_PORT  = int(os.environ.get("HTTP_PORT", 8080))

# Where Scope is running. Override with SCOPE_HOST env var or via POST /config/scope.
# Examples: "localhost:8000"  or  "abc123-8000.proxy.runpod.net"
_scope_host: str = os.environ.get("SCOPE_HOST", "localhost:8000")

# Last-known pipeline parameter state — updated by OSC and by POST /params.
# Sent to new WebSocket clients on connect so the viewer initialises in sync.
_params: dict = {}

app = FastAPI()
_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(data: dict):
    """Push a JSON message to all connected browser clients."""
    if not _clients:
        return
    message = json.dumps(data)
    dead = set()
    for ws in _clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


@app.get("/")
async def index():
    html = (Path(__file__).parent / "index.html").read_text()
    return HTMLResponse(html)


@app.get("/viewer")
async def viewer():
    html = (Path(__file__).parent / "viewer.html").read_text()
    return HTMLResponse(html)


@app.post("/config/scope")
async def config_scope(request: Request):
    """Let the browser tell the bridge which Scope instance to proxy to."""
    global _scope_host
    body = await request.json()
    host = body.get("host", "").strip()
    if not host:
        return Response(status_code=400, content="missing host")
    _scope_host = host
    print(f"[CONFIG] Scope host set to: {_scope_host}")
    return {"host": _scope_host}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def proxy_scope(path: str, request: Request):
    """Proxy all /api/* requests to the configured Scope instance.
    This avoids CORS — the browser always talks to the bridge (same origin)."""
    proto = "https" if _scope_host.endswith(".runpod.net") else "http"
    url = f"{proto}://{_scope_host}/api/{path}"
    body = await request.body()
    headers = {}
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(request.method, url, content=body, headers=headers)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type"))


@app.post("/params")
async def update_params(request: Request):
    """Control UI calls this when it manually sends params.
    Server caches the state and broadcasts to all clients so the viewer stays in sync."""
    global _params
    body = await request.json()
    _params.update({k: v for k, v in body.items() if not k.startswith("_")})
    await _broadcast({**body, "_mapped": True})
    return {"ok": True}


@app.get("/params")
async def get_params():
    """Return the current cached param state."""
    return _params


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    print(f"[WS] Browser connected  ({len(_clients)} total)")
    # Send current param state immediately so new viewers initialise in sync
    if _params:
        try:
            await websocket.send_text(json.dumps({**_params, "_mapped": True}))
        except Exception:
            pass
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _clients.discard(websocket)
        print(f"[WS] Browser disconnected ({len(_clients)} total)")


# --- OSC ---

_PARAM_MAP = {
    "/text":       ("text",       str),
    "/text_r":     ("text_r",     float),
    "/text_g":     ("text_g",     float),
    "/text_b":     ("text_b",     float),
    "/bg_opacity": ("bg_opacity", float),
}


def _osc_handler(address: str, *args):
    if not args:
        return
    raw = args[0]

    if address not in _PARAM_MAP:
        print(f"[OSC] Received (unmapped): {address} {list(args)}")
        if _loop:
            asyncio.run_coroutine_threadsafe(
                _broadcast({"_osc_address": address, "_osc_value": str(raw), "_mapped": False}),
                _loop,
            )
        return

    key, cast = _PARAM_MAP[address]
    try:
        value = cast(raw)
    except (ValueError, TypeError):
        print(f"[OSC] Bad value for {address}: {raw}")
        return
    print(f"[OSC] {address} → {key} = {value!r}")
    _params[key] = value
    if _loop:
        asyncio.run_coroutine_threadsafe(
            _broadcast({key: value, "_mapped": True}),
            _loop,
        )


def _run_osc_server():
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(_osc_handler)
    for address in _PARAM_MAP:
        dispatcher.map(address, _osc_handler)

    server = BlockingOSCUDPServer(("0.0.0.0", OSC_PORT), dispatcher)
    print(f"[OSC] Listening on port {OSC_PORT}")
    server.serve_forever()


@app.on_event("startup")
async def startup():
    global _loop
    _loop = asyncio.get_running_loop()

    osc_thread = threading.Thread(target=_run_osc_server, daemon=True)
    osc_thread.start()

    print(f"[HTTP] Serving on http://localhost:{HTTP_PORT}")
    print(f"[WS]  WebSocket at  ws://localhost:{HTTP_PORT}/ws")
    print(f"[SCOPE] Proxying to {_scope_host}  (override: SCOPE_HOST env var or POST /config/scope)")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
