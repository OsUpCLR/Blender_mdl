#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.decals — база декалей Star Conflict. Две РАЗНЫЕ таблицы:

1) `data\\gamedata\\decals.dat` — ПРОЕКЦИОННЫЕ декали (следы попаданий, `decals.fx`,
   шаблон `materials\\decals.mmp` с техниками `DECAL … PROJS … Spotlights`):

    asteroids_crystal
    {
        diffuse "textures\\decals\\crystal_decal"
        glow    "textures\\decals\\crystal_decal"
        uv ( 0.0000 0.0010 0.4316 0.3457 )     <- прямоугольник в атласе
        blend alpha_glow
        spec_color ( 0 0 0 )
    }
    ...
    UV в мешах таких декалей — ЛОКАЛЬНЫЕ (0..1), движок отображает их в прямоугольник `uv`:
        u_атласа = x0 + u_лок * (x1 - x0)
        v_атласа = y0 + v_лок * (y1 - y0)

2) `data\\gamedata\\ship_decals.lua` — ЗНАКИ НА КОРАБЛЕ (таблица `ShipDecals`):

    ShipDecals.Token.Billiard_01 = { albedo = "textures/ship_decals/tokens/free/billiard_01",
                                     spec = "textures/ship_decals/matted_sc",
                                     bump = "textures/nm", glow = "textures/black" }

   У каждого знака СВОЯ текстура, прямоугольников нет: движок сэмплит её третьим набором
   UV как есть (`object.fx:135` `Tex2 : VTEXCOORD2; // decals-UV`, ветка
   `PD_OCCL && SHIP_DECAL` → `baseUv = i.uv1.zw`; включается пином `User7 1` в .mdf).
   Поэтому map_uv к знакам корабля не применяется (см. docs/verification.md).
"""
import os
import re

# строка свойства:  uv ( 0.0000 0.0010 0.4316 0.3457 )
_RE_UV = re.compile(r"^\s*uv\s*\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)")
_RE_TEX = re.compile(r'^\s*(diffuse|glow|normal|specular)\s+"([^"]+)"', re.I)
_RE_BLEND = re.compile(r"^\s*blend\s+(\S+)", re.I)
_RE_PROP = re.compile(r"^\s+[A-Za-z_]")

# --- ship_decals.lua ---
_RE_LUA_ENTRY = re.compile(r"^\s*ShipDecals\.(Token|Vinyl)\.([A-Za-z0-9_\-]+)\s*=\s*$")
_RE_LUA_PROP = re.compile(r'^\s*(albedo|spec|bump|glow)\s*=\s*"([^"]+)"', re.I)
_RE_LUA_FLAG = re.compile(r"^\s*([A-Za-z_]+)\s*=\s*(true|false)\s*$")

DEFAULT_DAT = r"C:\Program Files (x86)\Steam\steamapps\common\star conflict\data\gamedata\decals.dat"
DEFAULT_SHIP_DECALS = (r"C:\Program Files (x86)\Steam\steamapps\common\star conflict"
                       r"\data\gamedata\ship_decals.lua")

# где искать таблицы, если путь не задан (в порядке приоритета)
DAT_CANDIDATES = [
    r"E:\starconflict\data\gamedata\decals.dat",
    DEFAULT_DAT,
]
SHIP_DECALS_CANDIDATES = [
    r"E:\starconflict\data\gamedata\ship_decals.lua",
    DEFAULT_SHIP_DECALS,
]


def decals_path(path=None):
    """Путь к decals.dat: заданный или первый существующий из известных установок."""
    if path:
        return path
    for c in DAT_CANDIDATES:
        if os.path.exists(c):
            return c
    return DEFAULT_DAT


def ship_decals_path(path=None):
    """Путь к ship_decals.lua: заданный или первый существующий из известных установок."""
    if path:
        return path
    for c in SHIP_DECALS_CANDIDATES:
        if os.path.exists(c):
            return c
    return DEFAULT_SHIP_DECALS



def parse_decals(path=None):
    """
    Разбор decals.dat -> {имя: {'uv': (x0,y0,x1,y1), 'diffuse': str, 'blend': str}}.
    Имена приводятся к нижнему регистру для удобства поиска.
    """
    path = decals_path(path)
    if not os.path.exists(path):
        raise FileNotFoundError("нет decals.dat: %s" % path)
    out = {}
    cur = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            raw = line.rstrip("\r\n")
            if not raw.strip():
                continue
            m = _RE_UV.match(raw)
            if m and cur:
                out[cur]["uv"] = tuple(float(x) for x in m.groups())
                continue
            t = _RE_TEX.match(raw)
            if t and cur:
                out[cur][t.group(1).lower()] = t.group(2)
                continue
            b = _RE_BLEND.match(raw)
            if b and cur:
                out[cur]["blend"] = b.group(1)
                continue
            if _RE_PROP.match(raw):
                continue                      # прочее свойство текущей декали
            name = raw.strip()
            if name in ("decals", "{", "}"):
                continue
            cur = name.lower()
            out[cur] = {"uv": None, "name": name}
    return out


def find_decal(decals, name):
    """
    Поиск декали по имени: точное совпадение, затем по началу имени
    (например "empire_signs01" -> первая empire_signs01*), иначе None.
    """
    n = (name or "").strip().lower()
    if not n:
        return None, None
    if n in decals:
        return n, decals[n]
    for k in sorted(decals):
        if k.startswith(n):
            return k, decals[k]
    for k in sorted(decals):
        if n in k:
            return k, decals[k]
    return None, None


def parse_ship_decals(path=None):
    """
    Разбор ship_decals.lua -> {имя_в_нижнем_регистре: {'albedo','spec','bump','glow','kind'}}.

    Это таблица `ShipDecals` (знаки на корабле), а не `decals.dat`: у каждого знака
    СВОЯ текстура, прямоугольников в атласе нет, UV берутся из меша как есть.
    """
    p = ship_decals_path(path)
    if not os.path.exists(p):
        raise FileNotFoundError("нет ship_decals.lua: %s" % p)
    out = {}
    cur = None
    for line in open(p, "r", encoding="utf-8", errors="replace"):
        raw = line.rstrip("\r\n")
        if not raw.strip():
            continue
        m = _RE_LUA_ENTRY.match(raw)
        if m:
            cur = {"name": m.group(2), "kind": m.group(1).lower()}
            out[m.group(2).lower()] = cur
            continue
        if cur is None:
            continue
        t = _RE_LUA_PROP.match(raw)
        if t:
            cur[t.group(1).lower()] = t.group(2)
            continue
        f = _RE_LUA_FLAG.match(raw)
        if f:
            cur[f.group(1).lower()] = (f.group(2) == "true")
            continue
        if raw.strip() == "}":
            cur = None
    return out


def find_ship_decal(decals, name):
    """
    Поиск знака в таблице ShipDecals: точное совпадение, затем по началу имени,
    затем по вхождению. Возвращает (ключ, запись) или (None, None).
    """
    n = (name or "").strip().lower()
    if not n:
        return None, None
    if n in decals:
        return n, decals[n]
    for k in sorted(decals):
        if k.startswith(n):
            return k, decals[k]
    for k in sorted(decals):
        if n in k:
            return k, decals[k]
    return None, None


def textures_of(entry):
    """Слоты текстур записи знака по шаблону ship_decals.mmp: albedo/spec/bump/glow."""
    if not entry:
        return {}
    return dict((k, entry[k]) for k in ("albedo", "spec", "bump", "glow") if entry.get(k))


def map_uv(u, v, rect):
    """
    Локальные UV (0..1) -> координаты в атласе по прямоугольнику декали.

    ВНИМАНИЕ: в decals.dat прямоугольник записан как (x0, y1, x1, y0) —
    вертикаль перевёрнута (например у `empire_signs01red` это
    0.3486 0.6353 0.6484 0.0205, то есть диапазон по V = 0.0205..0.6353).
    Поэтому границы берём через min/max, а не по порядку.
    """
    if not rect:
        return u, v
    x0, ya, x1, yb = rect
    xl, xh = min(x0, x1), max(x0, x1)
    yl, yh = min(ya, yb), max(ya, yb)
    return xl + u * (xh - xl), yl + v * (yh - yl)


def texture_name(entry):
    """Имя файла текстуры записи без пути и расширения (для привязки в MTL)."""
    for key in ("diffuse", "albedo", "normal", "bump", "specular", "spec", "glow"):
        p = entry.get(key)
        if p:
            return os.path.basename(p.replace("\\", "/"))
    return None
