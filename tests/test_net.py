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


def test_remote_player_selects_sim_and_requests_authoritative_interaction():
    async def run():
        host = KerMPHost('host', 'Host', '127.0.0.1', 0)
        host.session.update_sims([{'sim_id': '9007199254740993', 'name': 'Sim B'}])
        await host.start()
        port = host._server.sockets[0].getsockname()[1]
        server_task = asyncio.create_task(host._server.serve_forever())
        client = KerMPClient('player2', 'Player2', '127.0.0.1', port)
        received = []
        async def got(env): received.append(env)
        client.on_message = got
        await client.connect()
        listen = asyncio.create_task(client.listen())
        await client.send(MessageType.SIM_SELECT, {'sim_id': '9007199254740993'})
        await asyncio.sleep(0.05)
        await client.send(MessageType.INTERACTION_REQUEST, {'request_id': 'req-1', 'affordance_id': '123', 'target_id': '456'})
        await asyncio.sleep(0.05)
        assert host.session.players['player2'].active_sim_id == '9007199254740993'
        assert any(e.type == MessageType.INTERACTION_ACCEPTED.value and e.payload['sim_id'] == '9007199254740993' for e in received)
        client.writer.close(); await client.writer.wait_closed()
        listen.cancel(); server_task.cancel(); host._server.close(); await host._server.wait_closed()
    asyncio.run(run())
