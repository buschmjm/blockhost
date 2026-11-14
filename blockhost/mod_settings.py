"""Mod settings: simple forms over the JSON data inside Bedrock behavior packs.

Edits are stored as patches per pack in LIBRARY/tweaks/<uuid>.json, shaped
{"<file>": {"<json path>": value}}. The library copy of each pack stays
pristine; patches are re-applied whenever a pack is copied into a world, so
they survive mod re-assignment and are easy to revert.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading

CATEGORIES = {
    "spawns": ("Spawns", "spawn_rules"),
    "mobs": ("Mobs", "entities"),
    "items": ("Items", "items"),
    "recipes": ("Recipes", "recipes"),
    "loot": ("Loot", "loot_tables"),
    "trades": ("Trades", "trading"),
}
MAX_FILE_BYTES = 512 * 1024

_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], tuple[float, list[dict]]] = {}


# ------------------------------------------------------------------ json ---

def loose_loads(text: str):
    """Bedrock JSON allows comments and trailing commas; strip them outside strings."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        out.append(c)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned.lstrip("﻿"), strict=False)


def load_file(path: str):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return loose_loads(fh.read())
    except (OSError, ValueError):
        return None


def get_at(doc, path: list):
    cur = doc
    for k in path:
        if isinstance(cur, dict) and isinstance(k, str) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            raise KeyError(k)
    return cur


def set_at(doc, path: list, value) -> bool:
    try:
        parent = get_at(doc, path[:-1])
        get_at(parent, path[-1:])
    except KeyError:
        return False
    parent[path[-1]] = value
    return True


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def short(ident) -> str:
    s = str(ident or "")
    return s.split(":", 1)[1] if ":" in s else s


def pretty(ident) -> str:
    return short(ident).replace("_", " ").strip().capitalize() or "?"


# --------------------------------------------------------------- scanners ---

class _Fields:
    def __init__(self):
        self.items: list[dict] = []

    def num(self, path, label, value, *, minimum=0):
        if is_num(value):
            self.items.append({"p": list(path), "label": label, "kind": "num", "orig": value, "min": minimum})

    def num_or_range(self, path, label, value, *, minimum=0):
        if is_num(value):
            self.num(path, label, value, minimum=minimum)
        elif isinstance(value, dict):
            self.num(path + ["min"], label + " (min)", value.get("min"), minimum=minimum)
            self.num(path + ["max"], label + " (max)", value.get("max"), minimum=minimum)
        elif isinstance(value, list) and len(value) == 2 and all(is_num(x) for x in value):
            self.num(path + [0], label + " (min)", value[0], minimum=minimum)
            self.num(path + [1], label + " (max)", value[1], minimum=minimum)

    def toggle(self, path, label, off_value):
        self.items.append({"p": list(path), "label": label, "kind": "toggle", "off": off_value})


def scan_spawns(doc) -> tuple[str, str, list[dict]] | None:
    root = doc.get("minecraft:spawn_rules") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return None
    ident = (root.get("description") or {}).get("identifier", "")
    f = _Fields()
    conds = root.get("conditions") or []
    for i, c in enumerate(conds if isinstance(conds, list) else []):
        if not isinstance(c, dict):
            continue
        tag = f" #{i + 1}" if len(conds) > 1 else ""
        base = ["minecraft:spawn_rules", "conditions", i]
        w = c.get("minecraft:weight")
        if isinstance(w, dict):
            f.num(base + ["minecraft:weight", "default"], f"Spawn weight{tag} (0 = never)", w.get("default"))
        herd = c.get("minecraft:herd")
        herds = herd if isinstance(herd, list) else [herd]
        for h_i, h in enumerate(herds):
            if not isinstance(h, dict):
                continue
            hp = base + ["minecraft:herd"] + ([h_i] if isinstance(herd, list) else [])
            f.num(hp + ["min_size"], f"Group size{tag} (min)", h.get("min_size"), minimum=1)
            f.num(hp + ["max_size"], f"Group size{tag} (max)", h.get("max_size"), minimum=1)
        dl = c.get("minecraft:density_limit")
        if isinstance(dl, dict):
            f.num(base + ["minecraft:density_limit", "surface"], f"Max nearby on surface{tag}", dl.get("surface"))
            f.num(base + ["minecraft:density_limit", "underground"], f"Max nearby underground{tag}", dl.get("underground"))
    return pretty(ident), ident, f.items


def scan_mobs(doc):
    root = doc.get("minecraft:entity") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return None
    ident = (root.get("description") or {}).get("identifier", "")
    comps = root.get("components")
    if not isinstance(comps, dict):
        return None
    f = _Fields()
    base = ["minecraft:entity", "components"]
    h = comps.get("minecraft:health")
    if isinstance(h, dict):
        f.num_or_range(base + ["minecraft:health", "value"], "Health", h.get("value"), minimum=1)
        f.num(base + ["minecraft:health", "max"], "Max health", h.get("max"), minimum=1)
    a = comps.get("minecraft:attack")
    if isinstance(a, dict):
        f.num_or_range(base + ["minecraft:attack", "damage"], "Attack damage", a.get("damage"))
    m = comps.get("minecraft:movement")
    if isinstance(m, dict):
        f.num(base + ["minecraft:movement", "value"], "Move speed", m.get("value"))
    kb = comps.get("minecraft:knockback_resistance")
    if isinstance(kb, dict):
        f.num(base + ["minecraft:knockback_resistance", "value"], "Knockback resistance (0-1)", kb.get("value"))
    return pretty(ident), ident, f.items


def scan_items(doc):
    root = doc.get("minecraft:item") if isinstance(doc, dict) else None
    if not isinstance(root, dict):
        return None
    ident = (root.get("description") or {}).get("identifier", "")
    comps = root.get("components")
    if not isinstance(comps, dict):
        return None
    f = _Fields()
    base = ["minecraft:item", "components"]

    def val_or_obj(key, label, sub="value", minimum=0):
        v = comps.get(key)
        if is_num(v):
            f.num(base + [key], label, v, minimum=minimum)
        elif isinstance(v, dict):
            f.num(base + [key, sub], label, v.get(sub), minimum=minimum)

    val_or_obj("minecraft:max_stack_size", "Stack size (1-64)", minimum=1)
    val_or_obj("minecraft:damage", "Damage")
    d = comps.get("minecraft:durability")
    if isinstance(d, dict):
        f.num(base + ["minecraft:durability", "max_durability"], "Durability", d.get("max_durability"), minimum=1)
    food = comps.get("minecraft:food")
    if isinstance(food, dict):
        f.num(base + ["minecraft:food", "nutrition"], "Food points", food.get("nutrition"))
        f.num(base + ["minecraft:food", "saturation_modifier"], "Saturation", food.get("saturation_modifier"))
    val_or_obj("minecraft:use_duration", "Use time (seconds)")
    return pretty(ident), ident, f.items


def _recipe_result(obj) -> str:
    r = obj.get("result") or obj.get("output")
    if isinstance(r, list) and r:
        r = r[0]
    if isinstance(r, dict):
        r = r.get("item")
    return pretty(r) if r else ""


def scan_recipes(doc):
    if not isinstance(doc, dict):
        return None
    for key, obj in doc.items():
        if key.startswith("minecraft:recipe_") and isinstance(obj, dict):
            tags = obj.get("tags")
            if not isinstance(tags, list) or not tags:
                return None
            ident = (obj.get("description") or {}).get("identifier", "")
            res = _recipe_result(obj)
            kind = key.replace("minecraft:recipe_", "").replace("_", " ")
            f = _Fields()
            f.toggle([key, "tags"], f"Enabled ({kind}, {', '.join(map(str, tags))})", [])
            return (res or pretty(ident)), ident, f.items
    return None


def scan_loot(doc, rel: str):
    pools = doc.get("pools") if isinstance(doc, dict) else None
    if not isinstance(pools, list):
        return None
    f = _Fields()
    for pi, pool in enumerate(pools):
        if not isinstance(pool, dict):
            continue
        tag = f"Pool {pi + 1}: " if len(pools) > 1 else ""
        f.num_or_range(["pools", pi, "rolls"], f"{tag}Rolls", pool.get("rolls"))
        for ei, e in enumerate(pool.get("entries") or []):
            if not isinstance(e, dict) or e.get("type") not in ("item", "loot_table"):
                continue
            name = pretty(e.get("name"))
            f.num(["pools", pi, "entries", ei, "weight"], f"{tag}{name} weight", e.get("weight"))
            for fi, fn in enumerate(e.get("functions") or []):
                if isinstance(fn, dict) and str(fn.get("function", "")).replace("minecraft:", "") == "set_count":
                    f.num_or_range(["pools", pi, "entries", ei, "functions", fi, "count"], f"{tag}{name} count", fn.get("count"))
    title = rel.split("loot_tables/", 1)[-1].rsplit(".json", 1)[0]
    return title.replace("_", " "), rel, f.items


def scan_trades(doc, rel: str):
    tiers = doc.get("tiers") if isinstance(doc, dict) else None
    if not isinstance(tiers, list):
        return None
    f = _Fields()
    for ti, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            continue
        groups = [(["tiers", ti], tier)]
        if isinstance(tier.get("groups"), list):
            groups = [(["tiers", ti, "groups", gi], g) for gi, g in enumerate(tier["groups"]) if isinstance(g, dict)]
        for gp, g in groups:
            for xi, t in enumerate(g.get("trades") or []):
                if not isinstance(t, dict):
                    continue
                tp = gp + ["trades", xi]
                gives = t.get("gives") or []
                gname = pretty(gives[0].get("item")) if gives and isinstance(gives[0], dict) else "?"
                label = f"Level {ti + 1}, {gname}"
                for wi, w in enumerate(t.get("wants") or []):
                    if isinstance(w, dict):
                        f.num_or_range(tp + ["wants", wi, "quantity"], f"{label}: price ({pretty(w.get('item'))})", w.get("quantity"), minimum=1)
                for gi2, gv in enumerate(gives):
                    if isinstance(gv, dict):
                        f.num_or_range(tp + ["gives", gi2, "quantity"], f"{label}: amount given", gv.get("quantity"), minimum=1)
                f.num(tp + ["max_uses"], f"{label}: uses before restock", t.get("max_uses"), minimum=1)
    title = rel.split("trading/", 1)[-1].rsplit(".json", 1)[0]
    return title.replace("_", " "), rel, f.items


def scan_doc(cat: str, doc, rel: str):
    if cat == "spawns":
        return scan_spawns(doc)
    if cat == "mobs":
        return scan_mobs(doc)
    if cat == "items":
        return scan_items(doc)
    if cat == "recipes":
        return scan_recipes(doc)
    if cat == "loot":
        return scan_loot(doc, rel)
    if cat == "trades":
        return scan_trades(doc, rel)
    return None


def category_of(rel: str) -> str | None:
    top = rel.split("/", 1)[0]
    for cat, (_label, folder) in CATEGORIES.items():
        if top == folder:
            return cat
    return None


def scan_pack(pack_dir: str, cat: str) -> list[dict]:
    """[{file, title, sub, fields}] for one category, cached on pack mtime."""
    folder = CATEGORIES[cat][1]
    root = os.path.join(pack_dir, folder)
    try:
        stamp = os.path.getmtime(os.path.join(pack_dir, "manifest.json"))
    except OSError:
        return []
    key = (pack_dir, cat)
    hit = _CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.lower().endswith(".json"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, pack_dir).replace(os.sep, "/")
            doc = load_file(full)
            res = scan_doc(cat, doc, rel) if doc is not None else None
            if res and res[2]:
                title, sub, fields = res
                out.append({"file": rel, "title": title, "sub": sub, "fields": fields})
    out.sort(key=lambda e: e["title"].lower())
    _CACHE[key] = (stamp, out)
    return out


# ----------------------------------------------------------------- tweaks ---

class Store:
    def __init__(self, library: str, packs_dir: str, uid: int | None = None, gid: int | None = None):
        self.dir = os.path.join(library, "tweaks")
        self.packs_dir = packs_dir
        self.uid, self.gid = uid, gid

    def _path(self, uuid_: str) -> str:
        return os.path.join(self.dir, os.path.basename(uuid_.lower()) + ".json")

    def load(self, uuid_: str) -> dict[str, dict[str, object]]:
        try:
            with open(self._path(uuid_)) as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, uuid_: str, data: dict) -> None:
        os.makedirs(self.dir, exist_ok=True)
        data = {f: p for f, p in data.items() if p}
        path = self._path(uuid_)
        if not data:
            try:
                os.unlink(path)
            except OSError:
                pass
            return
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)

    def counts(self) -> dict[str, int]:
        out = {}
        if os.path.isdir(self.dir):
            for name in os.listdir(self.dir):
                if name.endswith(".json"):
                    n = sum(len(p) for p in self.load(name[:-5]).values())
                    if n:
                        out[name[:-5]] = n
        return out

    # -- reading ---------------------------------------------------------

    def settings(self, uuid_: str, cat: str) -> list[dict]:
        pack_dir = os.path.join(self.packs_dir, os.path.basename(uuid_.lower()))
        tweaks = self.load(uuid_)
        out = []
        for entry in scan_pack(pack_dir, cat):
            ft = tweaks.get(entry["file"], {})
            fields = []
            for fd in entry["fields"]:
                k = json.dumps(fd["p"])
                fd = dict(fd, key=k)
                if fd["kind"] == "toggle":
                    fd["val"] = k not in ft
                else:
                    fd["val"] = ft.get(k, fd["orig"])
                fd["changed"] = k in ft
                fields.append(fd)
            out.append(dict(entry, fields=fields, changed=sum(f["changed"] for f in fields)))
        return out

    def summary(self, uuid_: str) -> dict:
        pack_dir = os.path.join(self.packs_dir, os.path.basename(uuid_.lower()))
        tweaks = self.load(uuid_)
        cats = {}
        for cat, (label, _folder) in CATEGORIES.items():
            n = len(scan_pack(pack_dir, cat))
            if n:
                changed = sum(len(p) for f, p in tweaks.items() if category_of(f) == cat)
                cats[cat] = {"label": label, "count": n, "changed": changed}
        return {"categories": cats}

    # -- writing ---------------------------------------------------------

    def update(self, uuid_: str, changes: list[dict]) -> tuple[list[str], str | None]:
        """Validate and store changes. Returns (touched files, error)."""
        pack_dir = os.path.join(self.packs_dir, os.path.basename(uuid_.lower()))
        if not os.path.isfile(os.path.join(pack_dir, "manifest.json")):
            return [], "mod not found in library"
        with _LOCK:
            tweaks = self.load(uuid_)
            touched: set[str] = set()
            allowed_cache: dict[str, dict[str, dict]] = {}
            for ch in changes:
                rel = str(ch.get("file", ""))
                key = str(ch.get("key", ""))
                cat = category_of(rel)
                if not cat:
                    return [], f"not an editable file: {rel}"
                if rel not in allowed_cache:
                    entry = next((e for e in scan_pack(pack_dir, cat) if e["file"] == rel), None)
                    allowed_cache[rel] = {json.dumps(f["p"]): f for f in (entry or {}).get("fields", [])}
                field = allowed_cache[rel].get(key)
                if not field:
                    return [], f"not an editable setting: {rel} {key}"
                ft = tweaks.setdefault(rel, {})
                if field["kind"] == "toggle":
                    if ch.get("value") is False:
                        ft[key] = field["off"]
                    else:
                        ft.pop(key, None)
                else:
                    v = ch.get("value")
                    if v is None or v == field["orig"]:
                        ft.pop(key, None)
                    elif not is_num(v):
                        return [], f"{field['label']}: must be a number"
                    elif v < field.get("min", 0):
                        return [], f"{field['label']}: must be at least {field.get('min', 0)}"
                    else:
                        ft[key] = int(v) if isinstance(field["orig"], int) and float(v).is_integer() else v
                touched.add(rel)
            self.save(uuid_, tweaks)
        return sorted(touched), None

    def render_file(self, uuid_: str, rel: str, dest_pack: str, tweaks: dict | None = None) -> None:
        """Write rel into dest_pack: the library original plus any patches."""
        src_pack = os.path.join(self.packs_dir, os.path.basename(uuid_.lower()))
        src = os.path.join(src_pack, rel)
        dst = os.path.join(dest_pack, rel)
        real_dst = os.path.realpath(dst)
        if not real_dst.startswith(os.path.realpath(dest_pack) + os.sep) or not os.path.isfile(src):
            return
        patches = (tweaks if tweaks is not None else self.load(uuid_)).get(rel) or {}
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not patches:
            shutil.copyfile(src, dst)
        else:
            doc = load_file(src)
            if doc is None:
                return
            for k, v in patches.items():
                set_at(doc, json.loads(k), v)
            tmp = dst + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, dst)
        if self.uid is not None and hasattr(os, "chown"):
            try:
                os.chown(dst, self.uid, self.gid)
            except OSError:
                pass

    def apply_all(self, uuid_: str, dest_pack: str) -> None:
        tweaks = self.load(uuid_)
        for rel in tweaks:
            self.render_file(uuid_, rel, dest_pack, tweaks)


def world_copies(world_dirs: list[str], uuid_: str) -> list[str]:
    """Folders inside the given worlds that hold an embedded copy of this pack."""
    out = []
    for wd in world_dirs:
        for sub in ("behavior_packs", "resource_packs"):
            base = os.path.join(wd, sub)
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                man = load_file(os.path.join(base, name, "manifest.json"))
                if isinstance(man, dict) and str((man.get("header") or {}).get("uuid", "")).lower() == uuid_.lower():
                    out.append(os.path.join(base, name))
    return out
