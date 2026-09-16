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


def test_object_operations_are_validated_and_ids_are_preserved_as_strings():
    a = BuildAuthority()
    assert a.request_lock("p1")
    op = a.submit("p1", "object.move", {"object_id": 123, "transform": {"location": [1, 2, 3]}})
    assert op.data["object_id"] == "123"
    with pytest.raises(ValueError):
        a.submit("p1", "object.move", {})
    with pytest.raises(ValueError):
        a.submit("p1", "unsupported", {})


def test_funds_requires_household_and_numeric_amount():
    a = BuildAuthority(); assert a.request_lock("p1")
    assert a.submit("p1", "funds.modify", {"household_id": "h1", "amount": -50, "reason": "buy"}).seq == 1
    with pytest.raises(ValueError):
        a.submit("p1", "funds.modify", {"household_id": "h1", "amount": "-50"})
