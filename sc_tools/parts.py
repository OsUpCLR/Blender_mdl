# -*- coding: utf-8 -*-
"""Части корабля: кто «свой», какая категория и в какие кости ставится.

Логика перенесена из SDK-вьюпорта (`SCSDK\\SCToolKit\\Inner_plugins\\sc.view.model\\parts.py`)
1:1 — это канон, по которому аддон и вьюпорт должны собирать корабли одинаково:

1. «свой» — модель в ТОМ ЖЕ каталоге, у которой совпали последние два токена
   имени после срезания суффикса части (`r4_l_phoenix_boost` ~ `race4_l_phoenix`);
2. дроны/обломки/`_damage` не берём;
3. категория — по корневой кости скелета части (`root_engine` → `engine`),
   иначе по суффиксу имени; `cap`/`capacitor` приводятся к одному имени;
4. место — кости категории в скелете корпуса (`boost_1..4`, `engine_1`,
   `capacitor_1`), но не `*_anim_jnt*`; `bind` = перенос + 3×3 поворот.

Модуль не зависит от bpy и от вьюпорта: индекс и чтение файлов передаются
снаружи (`index`, `extract(pak, names) -> {name: path}`).
"""
import os
import re

SKIP_PARTS = ("_drone", "_drone_orbit", "_drone_hangar", "_wreck", "_damage")
PART_SUFFIXES = ("_boost", "_boosts", "_eng", "_engine", "_cap", "_capacitor",
                 "_capasitor", "_damage", "_wreck", "_drone_orbit", "_drone",
                 "_hull", "_cab", "_part")
ROOT_CATEGORIES = {"eng": "engine", "engine": "engine", "capasitor": "capacitor"}
CATEGORY_CANON = {"cap": "cap", "capacitor": "cap", "capasitor": "cap",
                  "eng": "engine", "engine": "engine"}

SIDE_TAILS = ("_l", "_r", "_left", "_right", "_s1", "_s2", "_s3", "_s4")
_SUFFIX_RE = re.compile(
    r"(?P<suf>_capasitor|_capacitor|_engine|_boosts|_boost|_eng|_cap|_hull|_cab"
    r"|_part|_damage|_wreck|_drone_orbit|_drone)"
    r"(?P<num>\d*)"
    r"(?P<tail>_(?:l|r|left|right|s\d))?$",
    re.IGNORECASE,
)

CATEGORY_ALIASES = {
    "boost": ("boost", "booster"),
    "engine": ("engine", "eng"),
    "cap": ("cap", "capacitor", "capasitor"),
    "capacitor": ("capacitor", "cap", "capasitor"),
    "wings": ("wings", "wing"),
}


def canonical_category(value):
    return CATEGORY_CANON.get((value or "").lower(), (value or "").lower())


# ------------------------------------------------------------------- имена

def strip_part_suffix(stem):
    m = _SUFFIX_RE.search(stem or "")
    return stem[:m.start()] if m else stem


def part_suffix_of(stem):
    m = _SUFFIX_RE.search(stem or "")
    return m.group("suf").lower() if m else ""


def is_part_name(stem):
    return strip_part_suffix(stem or "") != (stem or "")


def ship_tokens(stem):
    tokens = [t for t in strip_part_suffix(stem or "").split("_") if t]
    return tokens[-2:] if len(tokens) >= 2 else tokens


def same_ship(stem_a, stem_b):
    tokens = ship_tokens(stem_a)
    return bool(tokens) and tokens == ship_tokens(stem_b)


def folder_prefixes(folder):
    parts = [c for c in (folder or "").replace("\\", "/").split("/") if c]
    if len(parts) < 2:
        return set()
    race, cls = parts[-2], parts[-1]
    out = {("%s_%s" % (race, cls)).lower(),
           ("%s_%s" % (race.replace("_", ""), cls)).lower()}
    digits = "".join(ch for ch in race if ch.isdigit())
    if digits:
        out.add(("r%s_%s" % (digits, cls)).lower())
    return out


def is_generic_part(name, prefixes):
    if not prefixes:
        return False
    return strip_part_suffix(name or "").lower() in prefixes


def is_skipped(name):
    low = (name or "").lower()
    return any(skip in low for skip in SKIP_PARTS)


def category_from_name(name):
    low = (name or "").lower().split(".mdl-msh")[0]
    suffix = part_suffix_of(low)
    return canonical_category({"_capacitor": "capacitor", "_capasitor": "capacitor",
                               "_cap": "cap", "_boost": "boost", "_boosts": "boost",
                               "_engine": "engine", "_eng": "engine"}.get(suffix, ""))


# ----------------------------------------------------------------- скелет

def category_from_skeleton(skeleton):
    if not skeleton:
        return ""
    bones = [b for b in (skeleton.get("bones") or []) if isinstance(b, dict)]
    root = next((b for b in bones if b.get("parent", -1) < 0), None)
    if root is None:
        return ""
    name = (root.get("name") or "").lower()
    if not name.startswith("root"):
        return ""
    cat = name[4:].lstrip("_").rstrip("0123456789").rstrip("_")
    if cat in ("l", "r", "left", "right"):
        return ""
    # хвост стороны/скина: root_cap_l -> cap, root_engine_r -> engine
    for tail in ("_left", "_right", "_l", "_r", "_s1", "_s2", "_s3", "_s4"):
        if cat.endswith(tail) and len(cat) > len(tail):
            cat = cat[:-len(tail)]
            break
    return canonical_category(ROOT_CATEGORIES.get(cat, cat))


def _mount_bone(name, alias):
    low = (name or "").lower()
    if low == alias:
        return True
    if not low.startswith(alias + "_"):
        return False
    tail = low[len(alias) + 1:]
    return tail.isdigit()


def bones_for_category(skeleton, category):
    """Кости-точки крепления: boost_1..4, engine_1, capacitor_1 (не *_anim_jnt)."""
    if not skeleton or not category:
        return []
    bones = [b for b in (skeleton.get("bones") or []) if isinstance(b, dict)]
    aliases = CATEGORY_ALIASES.get(category.lower(), (category.lower(),))
    found = [b for b in bones
             if any(_mount_bone(b.get("name") or "", alias) for alias in aliases)]
    if found:
        found.sort(key=lambda b: (b.get("name") or "").lower())
        return found
    low = category.lower()
    return [b for b in bones if (b.get("name") or "").lower().startswith(low)]


def bind_of(bone):
    """(перенос, 3×3 поворот) из `bind`: столбцы (b4,b5,b6), (b8,b9,b10), (b12,b13,b14)."""
    bind = bone.get("bind") or bone.get("bind_matrix") or ()
    if len(bind) < 16:
        return (0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    translation = (float(bind[0]), float(bind[1]), float(bind[2]))
    rot = ((float(bind[4]), float(bind[8]), float(bind[12])),
           (float(bind[5]), float(bind[9]), float(bind[13])),
           (float(bind[6]), float(bind[10]), float(bind[14])))
    return translation, rot


# ------------------------------------------------------------------- поиск

class Finder(object):
    """Поиск частей: индекс `sc_index.json`, затем паки (через колбэк extract).

    index   — словарь sc_index.json (или только его раздел "models")
    extract — extract(pak_name, [имена файлов]) -> {имя: путь_на_диске}
    """

    def __init__(self, index, extract=None, unpacked_root=""):
        self.index = index or {}
        self.extract = extract
        self.unpacked_root = unpacked_root or ""
        self._models = (self.index.get("models") or self.index or {})

    def models(self):
        return self._models

    # --- кандидаты
    def find(self, base):
        """[(rel_base, pak)] — части того же корабля в том же каталоге."""
        base = (base or "").replace("\\", "/").strip("/")
        folder = os.path.dirname(base).lower()
        stem = os.path.basename(base)
        out = []
        for path, info in self.models().items():
            rel = path.replace("/", "\\").replace("\\\\", "\\")
            rel_norm = rel.replace("\\", "/")
            if os.path.dirname(rel_norm).lower() != folder:
                continue
            other = os.path.basename(rel_norm)
            if other.lower() == stem.lower():
                continue
            if not same_ship(stem, other) or is_skipped(other):
                continue
            out.append((rel_norm, (info or {}).get("pak") or ""))
        out.sort(key=lambda item: item[0])
        return out

    def generic(self, base):
        """[(rel_base, pak)] — общие детали класса (r4_l_eng, r4_l_boost...)."""
        base = (base or "").replace("\\", "/").strip("/")
        folder = os.path.dirname(base).lower()
        prefs = folder_prefixes(folder)
        out = []
        for path, info in self.models().items():
            rel_norm = path.replace("\\", "/")
            if os.path.dirname(rel_norm).lower() != folder:
                continue
            name = os.path.basename(rel_norm)
            if not is_generic_part(name, prefs) or is_skipped(name):
                continue
            out.append((rel_norm, (info or {}).get("pak") or ""))
        out.sort(key=lambda item: item[0])
        return out

    def entry(self, rel):
        key = (rel or "").replace("/", "\\")
        return self.models().get(key) or self.models().get(key.lower()) or {}

    def ensure(self, rel, out_dir):
        """Локальная база модели: из распаковки, иначе из пака по индексу."""
        if not rel:
            return ""
        if self.unpacked_root:
            local = os.path.join(self.unpacked_root, rel.replace("/", os.sep))
            if os.path.exists(local + ".mdl-hdr"):
                return local
        info = self.entry(rel)
        names = list(info.get("parts") or [])
        if info.get("mdf"):
            names.append(info["mdf"])
        # .mdl-skl в индекс не попадает — добавляем по имени
        names.append(rel.replace("/", "\\") + ".mdl-skl")
        names.append(rel.replace("/", "\\") + ".mdl-hdr")
        pak = info.get("pak") or ""
        if not pak or not self.extract:
            return ""
        got = self.extract(pak, names)
        for name, path in (got or {}).items():
            if name.lower().endswith(".mdl-hdr"):
                return path[:-len(".mdl-hdr")]
        return ""


def plan(finder, hull_base, out_dir=""):
    """План сборки: [(rel, pak, 'own'|'class')], свои раньше классовых."""
    own = [(rel, pak, "own") for rel, pak in finder.find(hull_base)]
    gen = [(rel, pak, "class") for rel, pak in finder.generic(hull_base)]
    seen, out = set(), []
    for rel, pak, kind in own + gen:
        key = os.path.basename(rel).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((rel, pak, kind))
    return out


