import io
import os
import zipfile

from blockhost import bds
from conftest import install_fake_server, needs_posix


def test_properties_update_keeps_comments(tmp_path):
    p = tmp_path / "server.properties"
    p.write_text("# hello\nlevel-name=level\ngamemode=survival\n")
    props = bds.Properties(str(tmp_path))
    props.update({"gamemode": "creative", "level-seed": "42"})
    text = p.read_text()
    assert "# hello" in text and "gamemode=creative" in text and "level-seed=42" in text
    assert props.read()["gamemode"] == "creative"


def test_properties_reject_newlines(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        bds.Properties(str(tmp_path)).update({"server-name": "a\nallow-cheats=true"})


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_install_preserves_user_data_and_blocks_zip_slip(tmp_path, monkeypatch):
    server = tmp_path / "server"
    archive = _zip_bytes({
        bds.EXE: "new-binary", "server.properties": "level-name=default\n",
        "worlds/level/db.txt": "fresh world", "behavior_packs/vanilla/x.json": "{}",
        "../evil.txt": "nope",
    })

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(archive))}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(bds, "_get", lambda url, timeout=30: Resp(archive))
    bds.install(str(server), "https://example/x.zip", "1.0")
    assert (server / "server.properties").read_text().count("level-name=level") == 1
    assert not (tmp_path / "evil.txt").exists()
    # user changes survive an update
    (server / "worlds/level/db.txt").write_text("my world")
    bds.Properties(str(server)).update({"gamemode": "creative"})
    bds.install(str(server), "https://example/x.zip", "1.1")
    assert (server / "worlds/level/db.txt").read_text() == "my world"
    assert "gamemode=creative" in (server / "server.properties").read_text()
    assert bds.installed_version(str(server)) == "1.1"


@needs_posix
def test_process_start_command_stop(tmp_path):
    install_fake_server(str(tmp_path))
    proc = bds.BedrockProcess(str(tmp_path), str(tmp_path / "log.txt"))
    assert proc.start(wait_seconds=10) is None
    mark = proc.seq
    assert proc.send("say hi") is None
    import time
    time.sleep(0.5)
    assert any("ran: say hi" in ln for ln in proc.since(mark))
    assert proc.send("two\ncommands")
    assert proc.stop() is None and not proc.running()
    assert os.path.exists(tmp_path / "log.txt")
