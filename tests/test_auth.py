from blockhost.auth import Auth, hash_password, read_cookie, verify_password
from blockhost.config import Config


def test_hash_roundtrip():
    h = hash_password("correct horse", iterations=1000)
    assert verify_password("correct horse", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


def test_setup_requires_code_and_only_once(tmp_path):
    a = Auth(Config(str(tmp_path)))
    assert a.needs_setup() and a.setup_token
    assert a.set_password("longenough", setup_token="nope")
    assert a.set_password("short", setup_token=a.setup_token)
    code = a.setup_token
    assert a.set_password("longenough", setup_token=code) is None
    assert not a.needs_setup() and a.setup_token == ""
    # the old code can't be reused to take over
    assert a.set_password("takeover123", setup_token=code)


def test_login_sessions_and_rate_limit(tmp_path):
    a = Auth(Config(str(tmp_path)))
    a.set_password("longenough", setup_token=a.setup_token)
    tok, err = a.login("longenough", "1.2.3.4")
    assert tok and err is None and a.check(tok)
    for _ in range(3):
        a.login("bad", "5.6.7.8")
    tok2, err = a.login("longenough", "5.6.7.8")
    assert tok2 is None and "too many" in err
    a.logout(tok)
    assert not a.check(tok)


def test_password_change_signs_everyone_out(tmp_path):
    a = Auth(Config(str(tmp_path)))
    a.set_password("longenough", setup_token=a.setup_token)
    tok, _ = a.login("longenough", "c")
    assert a.set_password("newpassword", current="wrong")
    assert a.set_password("newpassword", current="longenough") is None
    assert not a.check(tok)


def test_read_cookie():
    assert read_cookie("a=1; blockhost_session=abc; b=2") == "abc"
    assert read_cookie(None) is None
