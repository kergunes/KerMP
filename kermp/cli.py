from __future__ import annotations

import argparse
import asyncio
import json

from .identity import IdentityStore
from .net import KerMPHost, KerMPClient
from .protocol import Envelope
from .runtime import HostRuntime, ClientRuntime


async def run_host(args) -> None:
    ident = IdentityStore().load_or_create(args.name)
    host = KerMPHost(ident.player_id, ident.display_name, args.bind, args.port)
    runtime = HostRuntime(host, args.bridge_port)
    await runtime.start()
    sockets = ", ".join(str(s.getsockname()) for s in host._server.sockets) if host._server else "?"
    print(f"KerMP host ready: {sockets}")
    print(f"Sims bridge: 127.0.0.1:{args.bridge_port}")
    print(f"player: {ident.display_name} ({ident.player_id})")
    await host.serve_forever()


async def run_client(args) -> None:
    ident = IdentityStore().load_or_create(args.name)
    client = KerMPClient(ident.player_id, ident.display_name, args.host, args.port)
    runtime = ClientRuntime(client, args.bridge_port)
    welcome = await runtime.start()
    print("Connected:", json.dumps(welcome.payload, ensure_ascii=False))
    print(f"Sims bridge: 127.0.0.1:{args.bridge_port}")
    await client.listen()


def main() -> None:
    p = argparse.ArgumentParser(prog="kermp")
    sub = p.add_subparsers(dest="mode", required=True)
    h = sub.add_parser("host")
    h.add_argument("--name", default="Host")
    h.add_argument("--bind", default="0.0.0.0")
    h.add_argument("--port", type=int, default=17653)
    h.add_argument("--bridge-port", type=int, default=17654)
    c = sub.add_parser("join")
    c.add_argument("host")
    c.add_argument("--name", default="Client")
    c.add_argument("--port", type=int, default=17653)
    c.add_argument("--bridge-port", type=int, default=17654)
    args = p.parse_args()
    try:
        asyncio.run(run_host(args) if args.mode == "host" else run_client(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
