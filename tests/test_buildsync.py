import pytest
from kermp.buildsync import BuildAuthority


def test_authority_and_sequence():
    a = BuildAuthority()
    assert a.request_lock("p1")
    assert not a.request_lock("p2")
    one = a.submit("p1", "wall.create", {"x": 1})
    two = a.submit("p1", "wall.delete", {"id": "x"})
    assert (one.seq, two.seq) == (1, 2)
    with pytest.raises(PermissionError):
        a.submit("p2", "wall.create", {})
    assert a.release_lock("p1")
    assert a.request_lock("p2")
