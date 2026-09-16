from kermp.protocol import Envelope, MessageType


def test_roundtrip():
    e = Envelope.make(MessageType.PING, {"x": 1}, "abc", seq=7)
    d = Envelope.from_line(e.to_line())
    assert d.type == "ping"
    assert d.payload == {"x": 1}
    assert d.seq == 7
