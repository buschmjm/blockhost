from conftest import needs_posix


@needs_posix
def test_auth_gate_and_csrf(running_app):
    app, c, cfg = running_app
    assert c.req("GET", "/auth/status")[1]["needs_setup"] is True
    assert c.req("GET", "/api/state")[0] == 401
    assert c.req("POST", "/auth/setup", {"code": app.AUTH.setup_token, "password": "longenough"}, header=False)[0] == 403
    assert c.req("POST", "/auth/setup", {"code": "wrong", "password": "longenough"})[0] == 400
    assert c.req("POST", "/auth/setup", {"code": app.AUTH.setup_token, "password": "longenough"})[0] == 200
    status, state = c.req("GET", "/api/state")
    assert status == 200 and state["server"]["ready"] == 1
    # authenticated but missing the CSRF header
    assert c.req("POST", "/world/console", {"cmd": "say hi"}, header=False)[0] == 403


@needs_posix
def test_console_allowlist_and_settings(running_app):
    app, c, cfg = running_app
    c.req("POST", "/auth/setup", {"code": app.AUTH.setup_token, "password": "longenough"})
    status, out = c.req("POST", "/world/console", {"cmd": "say hi"})
    assert status == 200 and "ran: say hi" in out["lines"]
    assert c.req("POST", "/world/console", {"cmd": "stop"})[0] == 400
    assert c.req("POST", "/allowlist/add", {"name": "Alex"})[0] == 200
    assert "Alex" in c.req("GET", "/api/state")[1]["allowlist"]
    assert c.req("POST", "/allowlist/add", {"name": "bad;name"})[0] == 400
    assert c.req("POST", "/world/gamemode", {"value": "creative"})[0] == 200
    assert "gamemode=creative" in open(cfg.server_dir + "/server.properties").read()
    dash = c.req("GET", "/api/dashboard")[1]
    assert dash["player_count"] == 1 and dash["players"] == ["Steve"]
