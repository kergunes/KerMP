from kermp.travel import TravelCoordinator, TravelPhase


def test_barrier():
    c = TravelCoordinator()
    t = c.propose("zone-2", ["sim-a", "sim-b"], ["a", "b"])
    assert not c.mark_ready("a", t.txn_id)
    assert c.mark_ready("b", t.txn_id)
    assert t.phase == TravelPhase.COMMITTED
    c.begin_zone_wait(t.txn_id)
    assert not c.mark_zone_ready("b", t.txn_id)
    assert c.mark_zone_ready("a", t.txn_id)
    assert t.phase == TravelPhase.COMPLETE
