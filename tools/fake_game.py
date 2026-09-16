"""Fake Sims client for testing KerMP sidecar without launching The Sims 4."""
from __future__ import annotations

import argparse
import json
import socket
import threading


def send(sock, typ, payload=None):
    wire = json.dumps({"type": typ, "payload": payload or {}}, separators=(",", ":")) + "\n"
    sock.sendall(wire.encode("utf-8"))


def reader(sock):
    f = sock.makefile("rb")
    for line in f:
        msg = json.loads(line.decode("utf-8"))
        print("[SIDECAR]", msg)
        if msg.get("type") == "travel.prepare":
            send(sock, "travel.ready", {"txn_id": msg["payload"]["txn_id"]})
        elif msg.get("type") == "travel.commit":
            print("Pretending target zone loaded...")
            send(sock, "travel.zone_ready", {"txn_id": msg["payload"]["txn_id"], "zone_id": msg["payload"]["zone_id"]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=17654)
    args = ap.parse_args()
    with socket.create_connection(("127.0.0.1", args.port)) as s:
        send(s, "game.hello", {"fake": True})
        t = threading.Thread(target=reader, args=(s,), daemon=True)
        t.start()
        print("Commands: travel <zone>; wall x1 y1 x2 y2 level; del <wall_id>; release; quit")
        while True:
            parts = input("fake-game> ").split()
            if not parts:
                continue
            if parts[0] == "quit":
                return
            if parts[0] == "travel" and len(parts) == 2:
                send(s, "travel.request", {"zone_id": parts[1], "actor_ids": []})
            elif parts[0] == "wall" and len(parts) == 6:
                send(s, "build.operation", {"op": "wall.create", "data": {
                    "x1": float(parts[1]), "y1": float(parts[2]),
                    "x2": float(parts[3]), "y2": float(parts[4]), "level": int(parts[5]),
                }})
            elif parts[0] == "del" and len(parts) == 2:
                send(s, "build.operation", {"op": "wall.delete", "data": {"wall_id": parts[1]}})
            elif parts[0] == "release":
                send(s, "build.lock_release", {})
            else:
                print("bad command")


if __name__ == "__main__":
    main()
