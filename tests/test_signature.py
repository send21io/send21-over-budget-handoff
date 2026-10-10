import hashlib
import hmac

from handoff.webhook import sign, verify_signature

BODY = b'{"event":"test","data":{"message":"Test event"}}'
SECRET = "whsec_test"


def test_known_vector():
    expected = "sha256=" + hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert sign(BODY, SECRET) == expected
    assert expected == expected.lower()


def test_valid():
    assert verify_signature(BODY, sign(BODY, SECRET), SECRET)


def test_wrong_secret():
    assert not verify_signature(BODY, sign(BODY, "other"), SECRET)


def test_missing_header_or_secret():
    assert not verify_signature(BODY, None, SECRET)
    assert not verify_signature(BODY, sign(BODY, SECRET), "")


def test_tampered_body():
    assert not verify_signature(BODY + b" ", sign(BODY, SECRET), SECRET)


def test_without_prefix_and_bad_length():
    bare = sign(BODY, SECRET).removeprefix("sha256=")
    assert not verify_signature(BODY, bare, SECRET)
    assert not verify_signature(BODY, "sha256=abc", SECRET)
