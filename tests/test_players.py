import json
import os
import time

from conftest import needs_posix

from blockhost.players import PlayerTracker


def test_tracker_records_logins_and_grief(tmp_path):
    t = PlayerTracker(str(tmp_path))
    t.on_line("[2026-11-22 14:00:00:100 INFO] Player connected: Foo Bar, xuid: 123, pfid: x")
    t.on_line("[2026-11-22 14:10:00:100 INFO] Player disconnected: Foo Bar, xuid: 123, pfid: x")
    t.on_line("[2026-11-22 14:20:00:100 INFO] Player connected: Foo Bar, xuid: 123, pfid: x")
    t.on_line("[INFO] Player Spawned: Foo Bar xuid: 123")  # ignored
    t.on_line("[Scripting] [GRIEF] lava | Foo Bar | overworld -140 64 36")
    t.on_line("[Scripting] [GRIEF] nonsense")
    (rec,) = t.seen()
    assert rec["name"] == "Foo Bar" and rec["xuid"] == "123" and rec["joins"] == 2 and rec["last_join"]
    assert t.grief() == [{"time": t.grief()[0]["time"], "action": "lava", "player": "Foo Bar",
                          "dim": "overworld", "pos": [-140, 64, 36]}]
    # survives a restart of Blockhost
    assert PlayerTracker(str(tmp_path)).seen()[0]["joins"] == 2


def test_block_kicks_now_and_on_join(tmp_path):
    sent = []
    t = PlayerTracker(str(tmp_path), kick=sent.append, kick_delays=(0,))
    t.on_line("Player connected: Griefer, xuid: 999, pfid: x")
    assert t.block("bad;name") == "invalid gamertag"
    assert t.block("Griefer") is None
    assert sent == ['kick "Griefer" You are blocked from this server.']
    assert t.blocked() == ["Griefer"] and t.is_blocked("griefer") and t.is_blocked("NewName", "999")
    t.on_line("Player connected: Griefer, xuid: 999, pfid: x")
    for _ in range(50):
        if len(sent) == 2:
            break
        time.sleep(0.02)
    assert len(sent) == 2
    assert t.unblock("Griefer") is None and t.unblock("Griefer")
    assert t.blocked() == []


def test_grief_addon_is_valid():
    from blockhost import app
    with open(os.path.join(app.GRIEF_ADDON_DIR, "manifest.json")) as fh:
        man = json.load(fh)
    assert {m["type"] for m in man["modules"]} == {"data", "script"}
    assert os.path.isfile(os.path.join(app.GRIEF_ADDON_DIR, "scripts", "main.js"))
    assert app.grief_addon_uuid() == man["header"]["uuid"]


@needs_posix
def test_players_api(running_app):
    app, c, cfg = running_app
    c.req("POST", "/auth/setup", {"code": app.AUTH.setup_token, "password": "longenough"})
    # the fake server echoes commands, which lets the test inject log lines
    app.SERVER.send("x Player connected: Steve, xuid: 42, pfid: y")
    for _ in range(50):
        if app.PLAYERS.seen():
            break
        time.sleep(0.05)
    st = c.req("GET", "/api/state")[1]
    assert [r["name"] for r in st["seen"]] == ["Steve"] and st["roles"]["Steve"] == "member"
    assert st["blocked"] == [] and st["grief"] == [] and st["grief_logger"] is False
    assert c.req("POST", "/players/block", {"name": "Steve"})[0] == 200
    assert c.req("GET", "/api/state")[1]["blocked"] == ["Steve"]
    assert c.req("POST", "/players/unblock", {"name": "Steve"})[0] == 200
    assert c.req("POST", "/players/unblock", {"name": "Steve"})[0] == 400
    assert c.req("POST", "/players/block", {"name": "x\"; stop"})[0] == 400
