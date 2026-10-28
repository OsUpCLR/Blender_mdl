# -*- coding: utf-8 -*-
"""Пины материалов: правила из .mmp и проверка .mdf.

Зачем: техника шейдера выбирается по ПИНАМ экземпляра (conditions в .mmp).
Если пин недопустим для класса или отсутствует, а ноль не разрешён — материал
не получает проход и движок пишет «not bound».

Правило (проверено на 17 580 материалах игры, 0 нарушений):
  1) каждый указанный пин (UserN, значение) должен встречаться в условиях техник класса;
  2) для каждой переменной UserN, которую класс использует, отсутствующий пин
     трактуется как 0, и значение 0 тоже должно быть допустимо.
"""
from __future__ import annotations

import os
import re

USER_RE = re.compile(r"\bUser(\d+)\s+(-?\d+)\b")
MAT_RE = re.compile(r"\bmaterial\s+([A-Za-z_]\w*)\s*\{")


def collect_conditions(root):
    """class -> {(UserN, value)} плюс общие условия из .mmh (Base.mmh)."""
    classes = {}
    base = set()
    for dp, _dn, fs in os.walk(root):
        for f in fs:
            low = f.lower()
            if low.endswith(".mmp"):
                cls = f[:-4]
                target = classes.setdefault(cls, set())
            elif low.endswith(".mmh"):
                target = base
            else:
                continue
            try:
                text = open(os.path.join(dp, f), encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for m in USER_RE.finditer(text):
                target.add((int(m.group(1)), int(m.group(2))))
    return classes, base


def allowed_for(cls, classes, base):
    return set(classes.get(cls, set())) | set(base)


def default_pins(cls, classes, base):
    """Пины по умолчанию: ПУСТО.

    В игре большинство материалов вообще не имеет блока pins (пины = 0).
    Генератор не должен выдумывать значения: блок добавляется только когда
    нужно включить состояние (например User0 1 — AlphaTest).
    """
    return {}


def parse_mdf(path):
    """[(class, {UserN: value})] для каждого material в файле."""
    text = open(path, encoding="latin1", errors="replace").read()
    out = []
    for m in MAT_RE.finditer(text):
        cls = m.group(1)
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(text):
            ch = text[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[i + 1:j]
        pins = {}
        pm = re.search(r"\bpins\b\s*\{([^}]*)\}", body)
        if pm:
            for num, val in USER_RE.findall(pm.group(1)):
                pins[int(num)] = int(val)
        out.append((cls, pins))
    return out


def validate_mdf(path, classes, base):
    """Список проблем; пустой список — всё корректно."""
    problems = []
    for cls, pins in parse_mdf(path):
        if cls not in classes:
            problems.append("класс %r отсутствует среди .mmp" % cls)
            continue
        allowed = allowed_for(cls, classes, base)
        for num, val in pins.items():
            if (num, val) not in allowed:
                problems.append("%s: пин User%d %d недопустим для класса" % (cls, num, val))
        used = {n for n, _v in allowed}
        for num in sorted(used - set(pins)):
            if (num, 0) not in allowed:
                problems.append("%s: пин User%d отсутствует, а 0 не разрешён (нужно %s)"
                                % (cls, num, sorted(v for n, v in allowed if n == num)))
    return problems


def render_pins(pins):
    """Текст блока pins для .mdf (или пустая строка)."""
    if not pins:
        return ""
    body = "\n".join("\t\tUser%d %d" % (n, v) for n, v in sorted(pins.items()))
    return "\tpins\n\t{\n%s\n\t}\n" % body

# Типовые значения по классам — посчитаны по 5004 игровым .mdf (мода).
# pins: какие состояния включены у большинства материалов класса;
# up2:  UserParam2_Float4, если встречается у большинства.
#
# Смысл UserParam2_Float4 ЗАВИСИТ ОТ КЛАССА (object.fx, per_material в .mmp):
#   dyn_object_norm / object_norm : (tiling base, tiling detail, uniform fog, glow)
#   dyn_object / object           : (—, —, uniform fog, glow)
#   *_blend                        : (contrast, brightness, —, glow)
#   ship_hull                      : (armor tiling xy, —, —)
#   dyn_drone                      : (tiling base, tiling detail, —, glow)
#   fresnel / dyn_fresnel          : (fresnel color rgb, transparency a)
#   animated_mock                  : (UV offset xy, auto-speed, twist speed)
#   decals                         : (stay time, inverse falloff time, —, —)
#   grassbase                      : (wind bend factor, —, —, —)
#   sky                            : (layer0 v offset, layer1 v offset, —, —)
#   skybackground / planets        : (layer0 v offset, —, —, —)
# Общее: .z — uniform fog (fog = 1 - z, только при UNIFORM_FOG);
#         .w — добавка к свечению glow * (w + 1); при 0 не влияет.
CLASS_DEFAULTS = {
    "ship_hull":           {"pins": [(1, 1)], "up2": (1.0, 1.0, 0.0, 0.0)},
    "ship_decals":         {"pins": [(7, 1)], "up2": None},
    "ship_static_decals":  {"pins": [(7, 1)], "up2": None},
    "dyn_glass":           {"pins": [], "up2": None},
    "blank_object":        {"pins": [], "up2": None},
    "dyn_blank_object":    {"pins": [], "up2": None},
    "dyn_object":          {"pins": [], "up2": None},
    "dyn_object_norm":     {"pins": [(1, 1)], "up2": (1.0, 1.0, 0.0, 0.0)},
    "object_norm":         {"pins": [(1, 1)], "up2": (1.0, 1.0, 0.0, 0.0)},
    "dyn_animated_mock":   {"pins": [(1, 1), (2, 1)], "up2": None},
    "animated_mock":       {"pins": [(1, 1), (2, 1)], "up2": None},
    "skybackground":       {"pins": [(0, 1), (1, 1)], "up2": None},
}


def class_defaults(cls, classes=None, base=None):
    """Типовые pins/UserParam2 для класса (из игровой статистики).

    Если класса нет в таблице — возвращаем пусто: лучше не выдумывать значения,
    а дать валидатору сообщить о неизвестном классе.
    """
    return dict(CLASS_DEFAULTS.get(cls, {"pins": [], "up2": None}))

# Двусторонность в игре задаётся состоянием техники (Cull None), а не геометрией.
# Проверено по .mmp: у части классов есть пин, включающий Cull None.
TWO_SIDED_PINS = {
    # двусторонняя непрозрачная поверхность (OBJECT_LIGHTS ... Cull None, User8 1)
    "object_norm": [(8, 1)],
    # двусторонняя светящаяся: AM_CULL -> AM_BASE(sts Cull None, addPins User2 1)
    # (в .mmp прямо: "// User2 - cull CF/cull None")
    "dyn_animated_mock": [(2, 1)],
    # аддитивная двусторонняя (Blend ColorAdd Cull None) — для «энергетических» панелей
    "spaceship_shield": [],
}


def two_sided_pins(cls):
    """Пины, включающие двусторонний рендер для класса (None — класс так не умеет)."""
    return list(TWO_SIDED_PINS.get(cls)) if cls in TWO_SIDED_PINS else None
