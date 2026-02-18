"""
Interactive OSC sender. Type messages like:
  /text_r 0.8
  /text Hello World
  /bg_opacity 0.5

Ctrl+C or 'q' to quit.
"""
from pythonosc.udp_client import SimpleUDPClient

HOST = "127.0.0.1"
PORT = 9000

client = SimpleUDPClient(HOST, PORT)
print(f"OSC → {HOST}:{PORT}  (Ctrl+C to quit)\n")

while True:
    try:
        line = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        break

    if not line or line in ("q", "quit", "exit"):
        break

    parts = line.split(None, 1)
    if len(parts) < 2:
        print("Usage: /address value")
        continue

    address, raw = parts
    try:
        value = float(raw)
    except ValueError:
        value = raw  # send as string

    client.send_message(address, value)
    print(f"  sent {address} → {value!r}")
