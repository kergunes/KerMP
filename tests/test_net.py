import asyncio
from kermp.net import KerMPHost, KerMPClient
from kermp.protocol import MessageType


def test_handshake_and_build_stream():
    async def run():
        host = KerMPHost("host-id", "Host", "127.0.0.1", 0)
        seen = []
        async def host_seen(env):
            seen.append(env)
        host.on_message = host_seen
        await host.start()
        port = host._server.sockets[0].getsockname()[1]
        server_task = asyncio.create_task(host._server.serve_forever())

        c = KerMPClient("client-id", "Client", "127.0.0.1", port)
        received = []
        async def got(env):
            received.append(env)
        c.on_message = got
        welcome = await c.connect()
        assert welcome.type == "welcome"
        listen = asyncio.create_task(c.listen())
        await c.send(MessageType.BUILD_LOCK_REQUEST)
        await asyncio.sleep(0.05)
        await c.send(MessageType.BUILD_OPERATION, {"op": "wall.create", "data": {"x1": 0}})
        await asyncio.sleep(0.1)
        assert any(e.type == "build.apply" for e in received)
        assert any(e.type == "build.apply" for e in seen)

        c.writer.close()
        await c.writer.wait_closed()
        listen.cancel()
        server_task.cancel()
        host._server.close()
        await host._server.wait_closed()
    asyncio.run(run())
