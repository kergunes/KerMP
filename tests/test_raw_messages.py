import asyncio
import base64

from kermp.bridge import BridgeEvent
from kermp.net import KerMPHost, KerMPClient
from kermp.protocol import MessageType, MAX_GAME_MESSAGE_BYTES
from kermp.runtime import HostRuntime, ClientRuntime


def test_raw_message_base64_guard_and_host_only_forward():
    async def run():
        host = KerMPHost('h', 'Host', '127.0.0.1', 0)
        runtime = HostRuntime(host, bridge_port=0)
        forwarded = []
        async def seen(*args):
            forwarded.append(args[-1])
        host.broadcast = seen
        payload = base64.b64encode(b'view-update').decode('ascii')
        await runtime._on_game_event(BridgeEvent(MessageType.GAME_RAW_MESSAGE.value,
            {'msg_id': 42, 'payload_b64': payload}))
        assert forwarded[0]['msg_id'] == 42
        assert base64.b64decode(forwarded[0]['payload_b64']) == b'view-update'
        assert runtime.view_updates_sent == 1
        await runtime._on_game_event(BridgeEvent(MessageType.GAME_RAW_MESSAGE.value,
            {'msg_id': 43, 'payload_b64': base64.b64encode(b'x' * (MAX_GAME_MESSAGE_BYTES + 1)).decode('ascii')}))
        assert runtime.view_updates_sent == 1
    asyncio.run(run())


def test_client_preserves_sequence_and_buffers_by_epoch():
    async def run():
        client = KerMPClient('c', 'Client', '127.0.0.1')
        runtime = ClientRuntime(client, bridge_port=0)
        sent = []
        runtime.bridge.send = lambda typ, payload: sent.append((typ, payload)) or True
        runtime.buffer_view_updates = True
        runtime.travel_epoch = 7
        raw = base64.b64encode(b'a').decode('ascii')
        from kermp.protocol import Envelope
        await runtime._on_network_message(Envelope.make(MessageType.GAME_RAW_MESSAGE,
            {'msg_id': 1, 'sequence': 2, 'payload_b64': raw, 'epoch': 7}, 'h', seq=2))
        await runtime._on_network_message(Envelope.make(MessageType.GAME_RAW_MESSAGE,
            {'msg_id': 2, 'sequence': 1, 'payload_b64': raw, 'epoch': 7}, 'h', seq=1))
        assert [x['msg_id'] for x in runtime.buffered_view_updates] == [1]
        runtime.buffer_view_updates = False
        runtime._apply_game_message({'msg_id': 3, 'sequence': 3, 'payload_b64': raw})
        assert runtime.view_updates_received == 1
        assert any(x[0] == MessageType.GAME_RAW_MESSAGE.value for x in sent)
        await runtime._on_network_message(Envelope.make(MessageType.GAME_RAW_MESSAGE,
            {'msg_id': 4, 'sequence': 4, 'payload_b64': raw, 'epoch': 6}, 'h', seq=4))
        assert runtime.view_updates_received == 1
        assert any('stale_travel_epoch' in str(x) for x in sent)
    asyncio.run(run())
