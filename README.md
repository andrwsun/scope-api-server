# Scope Control Interface

A custom browser UI for controlling Scope's `text-display` pipeline in real-time, with TouchDesigner OSC support.

---

## First-time setup (do this once)

### 1. Install `uv`

`uv` is a Python tool manager. Open Terminal and run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then restart your Terminal.

### 2. Clone and install the Scope plugins

Clone each plugin repo next to this folder, then install them:

```bash
git clone https://github.com/andrwsun/scope-text-display ../scope-text-display
cd ../scope-text-display && uv pip install -e . && cd -
```

You only need to do this once. If a plugin is updated later, `cd` into it and run `git pull` — no reinstall needed.

---

## Starting the interface (every time)

You need **two things running** before opening the browser.

### Step 1 — Start Scope

```bash
cd "/Users/andrew/Desktop/scope"
uv run daydream-scope
```

Leave this Terminal window open.

### Step 2 — Start the control server

Open a **second** Terminal window and run:

```bash
cd "/Users/andrew/Desktop/scope local/scope api test"
uv run --with "fastapi[standard]" --with python-osc --with httpx python server.py
```

Leave this Terminal window open too.

### Step 3 — Open the browser

Go to: **http://localhost:8080**

Click **Connect** to link up to Scope. You should see the video stream appear.

---

## TouchDesigner setup

In TouchDesigner, add an **OSC Out CHOP** with these settings:

| Setting | Value |
|---------|-------|
| Host | `127.0.0.1` (same machine) or the server's IP (remote) |
| Port | `9000` |
| Protocol | UDP |

Send messages to these addresses:

| Address | Type | Effect |
|---------|------|--------|
| `/text` | string | Text to display |
| `/text_r` | float 0–1 | Text red |
| `/text_g` | float 0–1 | Text green |
| `/text_b` | float 0–1 | Text blue |
| `/bg_opacity` | float 0–1 | Background opacity |

---

## Testing OSC without TouchDesigner

```bash
uv run --with python-osc python osc_send.py
```

Then type messages like:
```
> /text_r 0.8
> /text Hello World
> /bg_opacity 0.5
```

---

## Stopping the servers

```bash
# Stop the bridge server (port 8080)
lsof -ti :8080 | xargs kill -9

# Stop Scope (port 8000)
lsof -ti :8000 | xargs kill -9
```

Or just hit `Ctrl+C` in whichever Terminal window is running them.

---

## Changing ports (if you have a conflict)

```bash
# Change OSC port (default 9000)
OSC_PORT=9001 uv run --with "fastapi[standard]" --with python-osc --with httpx python server.py

# Change web UI port (default 8080)
HTTP_PORT=8081 uv run --with "fastapi[standard]" --with python-osc --with httpx python server.py
```

---

## How it all fits together

```
TouchDesigner
    │ OSC/UDP → :9000
    ▼
server.py  (http://localhost:8080)
    │ WebSocket
    ▼
Your Browser
    │ WebRTC
    ▼
Scope  (http://localhost:8000)
    │ Video stream
    ▼
Your Browser (video display)
```
