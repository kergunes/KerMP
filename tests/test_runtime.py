import asyncio

from kermp.net import KerMPHost, KerMPClient
from kermp.runtime import HostRuntime, ClientRuntime


def test_runtime_handshake_without_game_bridge_client():
    async def run():
        host = KerMPHost("h", "Host", "127.0.0.1", 0)
        hr = HostRuntime(host, bridge_port=0)
        # Avoid starting bridge with port 0 here; transport integration is already
        # covered by net tests. This verifies runtime wiring does not alter handshake.
        await host.start()
        port = host._server.sockets[0].getsockname()[1]
        c = KerMPClient("c", "Client", "127.0.0.1", port)
        welcome = await c.connect()
        assert welcome.payload["host_id"] == "h"
        c.writer.close()
        await c.writer.wait_closed()
        host._server.close()
        await host._server.wait_closed()
    asyncio.run(run())
