from kermp.build_backend import FakeBuildBackend, SimsNativeBuildBackend
from kermp.buildsync import BuildAuthority
from kermp.travel import TravelCoordinator, TravelPhase
import pytest


def test_fake_game_scenarios_1_to_10():
    # The harness exercises the deterministic state layers without Sims or a
    # native hook. Network handshake/reconnect is covered by test_net.py.
    authority = BuildAuthority()
    assert authority.request_lock("host")  # 1/3 handshake/authority model
    first = authority.submit("host", "wall.create", {"wall_id": "w1", "x1": 0, "x2": 4})
    assert first.operation_id and authority.history_since(0) == [first]  # 4
    assert authority.history_since(1) == []  # 5 duplicate/replay cursor
    second = authority.submit("host", "wall.delete", {"wall_id": "w1"})  # 6
    assert [x.op for x in authority.history_since(0)] == ["wall.create", "wall.delete"]
    assert authority.release_lock("host") and not authority.lock  # 10

    travel = TravelCoordinator()
    txn = travel.propose("lot-b", [], ["a", "b"])  # 7
    assert not travel.mark_ready("a", txn.txn_id)
    assert travel.mark_ready("b", txn.txn_id)
    travel.begin_zone_wait(txn.txn_id)
    assert not travel.mark_zone_ready("a", txn.txn_id)  # 8
    assert travel.mark_zone_ready("b", txn.txn_id)
    assert travel.mark_zone_ready("b", txn.txn_id) is False  # duplicate ready
    assert travel.current.phase == TravelPhase.COMPLETE

    stale = travel.propose("lot-c", [], ["a", "b"])  # 9: new epoch rejects old txn
    assert stale.epoch > txn.epoch
    with pytest.raises(KeyError):
        travel.mark_ready("a", txn.txn_id)
    assert FakeBuildBackend().is_available() and not SimsNativeBuildBackend().is_available()
