from pm.core.errors import Conflict, Denied, Invalid, NotFound, PmError


def test_status_per_class_and_override():
    assert [c.status for c in (PmError, Invalid, Denied, NotFound, Conflict)] == [500, 400, 403, 404, 409]
    e = Invalid("bad name")
    assert isinstance(e, PmError) and str(e) == "bad name" and e.status == 400
    assert PmError("x", status=418).status == 418 and PmError.status == 500
    assert Denied().status == 403 and str(Denied()) == ""
