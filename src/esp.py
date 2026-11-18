"""esp.py - one-line client for espfast.py (127.0.0.1:9998).

Deliberately tiny: this runs once per command, so its own startup cost matters.
Python interpreter startup is ~0.15 s, versus ~5 s for a fresh BLE connect.

usage:
    python esp.py ping
    python esp.py key w
    python esp.py key w 150 w 150 w 150
    python esp.py click 1500 300
    python esp.py move 60 0 200
    python esp.py state
"""
import socket
import sys

PORT = 9998

s = socket.create_connection(("127.0.0.1", PORT), timeout=15)
s.sendall((" ".join(sys.argv[1:]) + "\n").encode("utf-8"))
buf = b""
while not buf.endswith(b"\n"):
    chunk = s.recv(4096)
    if not chunk:
        break
    buf += chunk
sys.stdout.write(buf.decode("utf-8", "replace"))
