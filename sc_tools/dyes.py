#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.dyes — таблица покрасок Star Conflict (`data\\gamedata\\ship_dyes.lua`).

Формат (таблица `ShipDyes`, 2 759 записей в текущей версии игры):

    ShipDyes =
    {
        Default_Bronze =
        {
            albedo = "108 72 57",
            gloss = 155,
            glossInfluence = 255,
            glossPower = 0,
            spec = "196 150 91",
            specInfluence = 255,
            specPower = 0,
        },
        ...
    }

Семь полей записи — ровно то, что шейдер читает в `UserParam3_Float4`
(`object.fx:447-459`), по три `float4` на каждый из трёх слоёв покраски:

    слой R: [0] = (spec.rgb, gloss)   [1] = (albedo.rgb, —)   [2] = (glossInfluence, specInfluence, glossPower, specPower)
    слой G: [3], [4], [5]                                    — то же
    слой B: [6], [7], [8]                                    — то же
    [9]  = цвет и глянец брони, [10] = тайлинги брони (см. `ship_hull.mmp:140-150`)

Слои берутся из текстуры `_s1` (ColormapSampler): R/G/B — маски трёх покрасок, в альфе —
маска кристаллов (`ship_hull.mmp:124-132`). Краситель можно надеть на каждый слой отдельно —
строки движка: `Couldn't equip dye "%s" on layer %d for weapon module "%s"`.

    from sc_tools import dyes
    table = dyes.parse_ship_dyes()
    key, dye = dyes.find_dye(table, "Matt_Red01")
    print(dye["albedo"], dye["spec"])
"""
import os
import re

_RE_ENTRY = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*=\s*$")
_RE_RGB = re.compile(r'^\s*(albedo|spec)\s*=\s*"([^"]+)"')
_RE_NUM = re.compile(r"^\s*(gloss|glossInfluence|specInfluence|glossPower|specPower)"
                     r"\s*=\s*([-\d.eE+]+)")

# канонические имена полей (как в файле) — регистр не должен зависеть от re-группы
_CANON = {
    "albedo": "albedo",
    "spec": "spec",
    "gloss": "gloss",
    "glossinfluence": "glossInfluence",
    "specinfluence": "specInfluence",
    "glosspower": "glossPower",
    "specpower": "specPower",
}

_ENV_DATA = os.environ.get("SC_DATA", "")
DEFAULT_DYES = (os.path.join(_ENV_DATA, "gamedata", "ship_dyes.lua") if _ENV_DATA else "",
                r"C:\Program Files (x86)\Steam\steamapps\common\star conflict"
                r"\data\gamedata\ship_dyes.lua")

# где искать таблицу, если путь не задан (в порядке приоритета)
DYES_CANDIDATES = [
    r"E:\starconflict\data\gamedata\ship_dyes.lua",
    DEFAULT_DYES,
]


def dyes_path(path=None):
    """Путь к ship_dyes.lua: заданный или первый существующий из известных установок."""
    if path:
        return path
    for c in DYES_CANDIDATES:
        if os.path.exists(c):
            return c
    return DEFAULT_DYES


def _rgb(text):
    """'108 72 57' -> (108.0, 72.0, 57.0)."""
    parts = [p for p in re.split(r"[ ,]+", text.strip()) if p]
    vals = [float(p) for p in parts[:3]]
    while len(vals) < 3:
        vals.append(0.0)
    return tuple(vals)


def parse_ship_dyes(path=None):
    """
    Разбор ship_dyes.lua -> {имя_в_нижнем_регистре: {...}}.

    Числовые поля — float, `albedo`/`spec` — кортежи (r, g, b) в 0..255.
    """
    p = dyes_path(path)
    if not os.path.exists(p):
        raise FileNotFoundError("нет ship_dyes.lua: %s" % p)
    out = {}
    cur = None
    depth = 0
    for line in open(p, "r", encoding="utf-8", errors="replace"):
        raw = line.rstrip("\r\n")
        s = raw.strip().rstrip(",")          # в файле закрывающие скобки идут как "},"
        if not s:
            continue
        if s == "{":
            depth += 1
            continue
        if s == "}":
            if depth <= 2:
                cur = None
            depth = max(0, depth - 1)
            continue
        if depth == 1:
            m = _RE_ENTRY.match(s)
            if m:
                cur = {"name": m.group(1)}
                out[m.group(1).lower()] = cur
            continue
        if depth == 2 and cur is not None:
            f = _RE_RGB.match(s)
            if f:
                cur[_CANON[f.group(1).lower()]] = _rgb(f.group(2))
                continue
            n = _RE_NUM.match(s)
            if n:
                cur[_CANON[n.group(1).lower()]] = float(n.group(2))
    return out


def find_dye(dyes, name):
    """
    Поиск покраски: точное совпадение, затем по началу имени, затем по вхождению.
    Возвращает (ключ, запись) или (None, None).
    """
    n = (name or "").strip().lower()
    if not n:
        return None, None
    if n in dyes:
        return n, dyes[n]
    for k in sorted(dyes):
        if k.startswith(n):
            return k, dyes[k]
    for k in sorted(dyes):
        if n in k:
            return k, dyes[k]
    return None, None


def layer_params(dye):
    """
    Три `float4`-тройки для шейдера из записи красителя (для одного слоя):

        (spec.rgb, gloss), (albedo.rgb, 0), (glossInfluence, specInfluence, glossPower, specPower)

    Значения — как в файле (0..255); делить на 255 должен вызывающий.
    """
    a = tuple(dye.get("albedo") or (0.0, 0.0, 0.0))
    s = tuple(dye.get("spec") or (0.0, 0.0, 0.0))
    return (
        (s[0], s[1], s[2], float(dye.get("gloss", 0.0))),
        (a[0], a[1], a[2], 0.0),
        (float(dye.get("glossInfluence", 0.0)), float(dye.get("specInfluence", 0.0)),
         float(dye.get("glossPower", 0.0)), float(dye.get("specPower", 0.0))),
    )
