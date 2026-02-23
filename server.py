"""
Scope API Test Server
- Serves index.html on port 8080
- WebSocket endpoint at ws://localhost:8080/ws  (browser connects here)
- OSC listener on port 9000                      (TouchDesigner/Max sends here)
- Proxies /api/* to Scope engine (avoids CORS when Scope is remote)

OSC message format:

  Prompt pipelines (streamdiffusionv2, longlive, krea-realtime-video, etc.):
  /prompt          "Hello World"  - set prompt slot 1 text
  /prompt2         "Another"      - set prompt slot 2 text
  /prompt_weight   70             - weight for slot 1 (0–100), only when slot 2 is enabled
  /prompt2_weight  30             - weight for slot 2 (0–100)
  /prompt2_toggle  1              - enable (1) or disable (0) prompt slot 2

  text-display pipeline only:
  /text       "Hello World"   - text to display
  /text_r     0.8             - red   (0.0 - 1.0)
  /text_g     0.2             - green (0.0 - 1.0)
  /text_b     1.0             - blue  (0.0 - 1.0)
  /bg_opacity 0.5             - background opacity (0.0 - 1.0)

Run with:
  uv run --with "fastapi[standard]" --with python-osc --with httpx python server.py
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

OSC_PORT  = int(os.environ.get("OSC_PORT",  9000))
HTTP_PORT = int(os.environ.get("HTTP_PORT", 8080))

app = FastAPI()
_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None
_scope_host: str = os.environ.get("SCOPE_HOST", "localhost:8000")
_last_broadcast: dict = {}  # replayed to new viewers on connect


def _scope_url(path: str) -> str:
    """Build the full URL for a Scope API call.
    Uses https:// for remote hosts, http:// for localhost/127.x."""
    host = _scope_host.rstrip("/")
    is_local = host.startswith("localhost") or host.startswith("127.")
    proto = "http" if is_local else "https"
    return f"{proto}://{host}/{path.lstrip('/')}"


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


_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate"}


@app.get("/")
async def index():
    html = (Path(__file__).parent / "index.html").read_text()
    return HTMLResponse(html, headers=_NO_CACHE)


@app.get("/viewer")
async def viewer():
    html = (Path(__file__).parent / "viewer.html").read_text()
    return HTMLResponse(html, headers=_NO_CACHE)


@app.post("/config/scope")
async def config_scope(body: dict):
    """Let the browser tell us which Scope host to proxy to."""
    global _scope_host
    host = body.get("host", "").strip()
    # Strip any protocol prefix the user may have typed
    for prefix in ("https://", "http://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
    if host:
        _scope_host = host
        print(f"[CONFIG] Scope host → {_scope_host}")
    return {"host": _scope_host}


@app.get("/params")
async def get_params():
    """Return the last known parameter state sent to Scope."""
    return _last_broadcast


@app.post("/broadcast")
async def broadcast_params(body: dict):
    """Push parameter updates from the control UI to all connected browser clients.
    The viewer receives these and forwards them to Scope via its own data channel."""
    global _last_broadcast
    # Mirror viewer.html logic: replace on full pipeline state, merge on partial updates.
    # Partial broadcasts (e.g. reset_cache:true) must not wipe pipeline_ids — otherwise
    # _current_pipeline() returns None and subsequent OSC /prompt messages are dropped.
    if "pipeline_ids" in body:
        _last_broadcast = body
    else:
        _last_broadcast = {**_last_broadcast, **body}
    await _broadcast({**body, "_mapped": True, "_from_ui": True})
    return {"ok": True}


_SKIP_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "content-encoding"}

@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_scope(path: str, request: Request):
    """Forward /api/* requests to the Scope engine, bypassing browser CORS."""
    url = _scope_url(f"api/{path}")
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _SKIP_HEADERS}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            content=body,
            headers=headers,
            params=dict(request.query_params),
        )
    print(f"[PROXY] {request.method} {url} → {resp.status_code}")

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _SKIP_HEADERS}
    resp_headers["cache-control"] = "no-store"
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    _clients.add(websocket)
    print(f"[WS] Browser connected  ({len(_clients)} total)")
    # Immediately replay last known state so new viewers don't wait for the next update
    if _last_broadcast:
        try:
            await websocket.send_text(json.dumps({**_last_broadcast, "_mapped": True, "_from_ui": True}))
        except Exception:
            pass
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        _clients.discard(websocket)
        print(f"[WS] Browser disconnected ({len(_clients)} total)")


# --- OSC ---

# Pipelines that accept a text prompt (matches PROMPT_PIPELINES in index.html).
_PROMPT_PIPELINES = {"streamdiffusionv2", "longlive", "krea-realtime-video", "reward-forcing", "memflow"}

# text-display params: OSC address → (param key, type)
_PARAM_MAP = {
    "/text":       ("text",       str),
    "/text_r":     ("text_r",     float),
    "/text_g":     ("text_g",     float),
    "/text_b":     ("text_b",     float),
    "/bg_opacity": ("bg_opacity", float),
}

# Two-prompt state — persists across OSC messages so both slots are always known.
# weight1/weight2 are 0–100 (Scope scale). prompt2_enabled gates whether slot 2 is sent.
_prompt_state: dict = {
    "prompt1": "",
    "weight1": 100,
    "prompt2": "",
    "weight2": 0,
    "prompt2_enabled": False,
}


def _build_prompts() -> list[dict]:
    """Build the Scope prompts array from current _prompt_state."""
    if _prompt_state["prompt2_enabled"] and _prompt_state["prompt2"]:
        return [
            {"text": _prompt_state["prompt1"], "weight": _prompt_state["weight1"]},
            {"text": _prompt_state["prompt2"], "weight": _prompt_state["weight2"]},
        ]
    return [{"text": _prompt_state["prompt1"], "weight": 100}]


async def _save_and_broadcast(data: dict):
    """Merge data into _last_broadcast and push to all WebSocket clients."""
    global _last_broadcast
    _last_broadcast = {**_last_broadcast, **data}
    await _broadcast({**data, "_mapped": True})


def _current_pipeline() -> str | None:
    """Return the first pipeline_id from the last known broadcast, or None."""
    ids = _last_broadcast.get("pipeline_ids")
    return ids[0] if ids else None


def _osc_handler(address: str, *args):
    if not args:
        return
    raw = args[0]

    # /prompt, /prompt2 — set text for slot 1 or slot 2.
    # /prompt_weight, /prompt2_weight — set blend weights (0–100).
    # /prompt2_toggle — enable/disable slot 2 (send 1 to enable, 0 to disable).
    # All ignored if the current pipeline doesn't accept prompts.
    if address in ("/prompt", "/prompt2", "/prompt_weight", "/prompt2_weight", "/prompt2_toggle"):
        pipeline = _current_pipeline()
        if pipeline not in _PROMPT_PIPELINES:
            print(f"[OSC] {address} ignored — current pipeline {pipeline!r} doesn't accept prompts")
            return

        if address == "/prompt":
            _prompt_state["prompt1"] = str(raw)
            print(f"[OSC] /prompt → {_prompt_state['prompt1']!r}")
        elif address == "/prompt2":
            _prompt_state["prompt2"] = str(raw)
            print(f"[OSC] /prompt2 → {_prompt_state['prompt2']!r}")
        elif address == "/prompt_weight":
            try:
                _prompt_state["weight1"] = int(float(raw))
            except (ValueError, TypeError):
                print(f"[OSC] Bad value for /prompt_weight: {raw}")
                return
            print(f"[OSC] /prompt_weight → {_prompt_state['weight1']}")
        elif address == "/prompt2_weight":
            try:
                _prompt_state["weight2"] = int(float(raw))
            except (ValueError, TypeError):
                print(f"[OSC] Bad value for /prompt2_weight: {raw}")
                return
            print(f"[OSC] /prompt2_weight → {_prompt_state['weight2']}")
        elif address == "/prompt2_toggle":
            try:
                _prompt_state["prompt2_enabled"] = bool(int(float(raw)))
            except (ValueError, TypeError):
                print(f"[OSC] Bad value for /prompt2_toggle: {raw}")
                return
            print(f"[OSC] /prompt2_toggle → {_prompt_state['prompt2_enabled']}")

        # Send only prompts — no pipeline_ids so viewer doesn't treat this as a
        # full pipeline state change (which would replace lastKnownParams and risk
        # triggering an unnecessary reconnect).
        params = {
            "prompts": _build_prompts(),
            "prompt_interpolation_method": "linear",
        }
        if _loop:
            asyncio.run_coroutine_threadsafe(_save_and_broadcast(params), _loop)
        return

    # text-display params — only forward when text-display is the active pipeline.
    if address in _PARAM_MAP:
        pipeline = _current_pipeline()
        if pipeline != "text-display":
            print(f"[OSC] {address} ignored — current pipeline is {pipeline!r}, not text-display")
            return
        key, cast = _PARAM_MAP[address]
        try:
            value = cast(raw)
        except (ValueError, TypeError):
            print(f"[OSC] Bad value for {address}: {raw}")
            return
        print(f"[OSC] {address} → {key} = {value!r}")
        if _loop:
            asyncio.run_coroutine_threadsafe(
                _save_and_broadcast({key: value}),
                _loop,
            )
        return

    # Unknown address — log as unmapped.
    print(f"[OSC] Received (unmapped): {address} {list(args)}")
    if _loop:
        asyncio.run_coroutine_threadsafe(
            _broadcast({"_osc_address": address, "_osc_value": str(raw), "_mapped": False}),
            _loop,
        )


def _run_osc_server():
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(_osc_handler)
    for address in ("/prompt", "/prompt2", "/prompt_weight", "/prompt2_weight", "/prompt2_toggle"):
        dispatcher.map(address, _osc_handler)
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
