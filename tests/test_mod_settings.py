import json
import os

from blockhost import mod_settings as ms

PACK = "11111111-2222-3333-4444-555555555555"


def make_pack(root):
    pack = root / "packs" / PACK
    (pack / "entities").mkdir(parents=True)
    (pack / "recipes").mkdir()
    (pack / "manifest.json").write_text(json.dumps({"header": {"uuid": PACK, "name": "Test"}}))
    (pack / "entities" / "yeti.json").write_text("""{
      // comments and trailing commas are allowed in Bedrock JSON
      "minecraft:entity": {"description": {"identifier": "t:yeti"},
        "components": {"minecraft:health": {"value": 60, "max": 60}, "minecraft:attack": {"damage": [3, 7]},}}
    }""")
    (pack / "recipes" / "torch.json").write_text(json.dumps({"minecraft:recipe_shaped": {
        "description": {"identifier": "t:torch"}, "tags": ["crafting_table"], "result": {"item": "t:torch"}}}))
    return pack


def test_scan_and_patch_roundtrip(tmp_path):
    pack = make_pack(tmp_path)
    store = ms.Store(str(tmp_path), str(tmp_path / "packs"))
    mobs = store.settings(PACK, "mobs")
    assert mobs[0]["title"] == "Yeti"
    labels = {f["label"]: f for f in mobs[0]["fields"]}
    assert {"Health", "Max health", "Attack damage (min)", "Attack damage (max)"} <= set(labels)

    health = labels["Health"]
    touched, err = store.update(PACK, [{"file": "entities/yeti.json", "key": health["key"], "value": 120}])
    assert err is None and touched == ["entities/yeti.json"]

    world_copy = tmp_path / "world" / "behavior_packs" / "test"
    world_copy.mkdir(parents=True)
    store.apply_all(PACK, str(world_copy))
    doc = json.loads((world_copy / "entities/yeti.json").read_text())
    assert doc["minecraft:entity"]["components"]["minecraft:health"]["value"] == 120

    # revert restores the original file byte for byte
    store.update(PACK, [{"file": "entities/yeti.json", "key": health["key"], "value": None}])
    store.render_file(PACK, "entities/yeti.json", str(world_copy))
    assert (world_copy / "entities/yeti.json").read_bytes() == (pack / "entities/yeti.json").read_bytes()


def test_recipe_toggle(tmp_path):
    make_pack(tmp_path)
    store = ms.Store(str(tmp_path), str(tmp_path / "packs"))
    rec = store.settings(PACK, "recipes")[0]
    key = rec["fields"][0]["key"]
    assert rec["fields"][0]["val"] is True
    store.update(PACK, [{"file": rec["file"], "key": key, "value": False}])
    assert store.settings(PACK, "recipes")[0]["fields"][0]["val"] is False
    assert store.load(PACK)[rec["file"]][key] == []


def test_rejects_unknown_files_fields_and_bad_values(tmp_path):
    make_pack(tmp_path)
    store = ms.Store(str(tmp_path), str(tmp_path / "packs"))
    key = store.settings(PACK, "mobs")[0]["fields"][0]["key"]
    assert store.update(PACK, [{"file": "../manifest.json", "key": "[]", "value": 1}])[1]
    assert store.update(PACK, [{"file": "entities/yeti.json", "key": '["x"]', "value": 1}])[1]
    assert store.update(PACK, [{"file": "entities/yeti.json", "key": key, "value": "lots"}])[1]
    assert store.update(PACK, [{"file": "entities/yeti.json", "key": key, "value": -1}])[1]
    assert store.update("nope", [])[1]


def test_world_copies_found_by_manifest(tmp_path):
    w = tmp_path / "w" / "behavior_packs" / "whatever-name"
    w.mkdir(parents=True)
    (w / "manifest.json").write_text(json.dumps({"header": {"uuid": PACK.upper()}}))
    assert ms.world_copies([str(tmp_path / "w")], PACK) == [str(w)]
    assert os.path.isdir(ms.world_copies([str(tmp_path / "w")], PACK)[0])
