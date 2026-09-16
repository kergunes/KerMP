import asyncio

from kermp.net import KerMPHost, KerMPClient
from kermp.runtime import HostRuntime, ClientRuntime
from kermp.session import HostSession


def test_player_sim_selection_and_validation():
    session = HostSession()
    session.add_player('host', 'Host')
    session.add_player('p2', 'Player2')
    session.update_sims([{'sim_id': '9007199254740993', 'name': 'Alice'}, {'sim_id': '2', 'name': 'Bob'}])
    assert session.select_sim('host', '9007199254740993')['name'] == 'Alice'
    assert session.select_sim('p2', '2')['name'] == 'Bob'
    request = session.validate_interaction('r1', 'p2', {'affordance_id': '123', 'target_id': '456'})
    assert request['sim_id'] == '2'
    try:
        session.validate_interaction('r1', 'p2', {'affordance_id': '123'})
        assert False
    except ValueError as exc:
        assert str(exc) == 'duplicate_request_id'
    assert session.select_sim('host', '2')['controllers'] == ['host', 'p2']


def test_disconnect_releases_sim_and_snapshot_persists_mapping():
    session = HostSession()
    session.add_player('p2', 'Player2')
    session.update_sims([{'sim_id': '99', 'name': 'Bob'}])
    session.select_sim('p2', '99')
    assert session.snapshot()['players'][0]['active_sim_id'] == '99'
    session.remove_player('p2')
    assert session.sims['99']['controllers'] == []
    session.add_player('p2', 'Player2')
    assert session.players['p2'].active_sim_id == '99'


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
