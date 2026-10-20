from handoff.send21_client import explain_send21_error


def test_explains_approval_errors():
    assert "not approved" in explain_send21_error('{"error":"address_not_approved"}')
    assert "taken over" in explain_send21_error("403 owner_not_confirmed")
    assert explain_send21_error("500 boom") is None
