#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.vertex_fmt — семантика элементов вершины Star Conflict.

Формат вершины описывается таблицей (usage, type, offset, size) — её даёт
`sc_tools.model.vertex_elements(fmt)`. Смысл usage установлен по реальным
данным (21 меш корабля, 5 форматов: 11, 20, 21, 27, 28):

    usage 0, type 2, 12 байт  — позиция,   3 × float32
    usage 3, type 8,  4 байта — нормаль,   упакованная (по 0x80 на компоненту)
    usage 5, type 8,  4 байта — касательная/бинормаль, упакованная
    usage 4, type 1,  8 байт  — UV0,       2 × float32
    usage 4, type 9,  4 байта — UV1,       упакованная
    usage 2, type 5,  4 байта — ИНДЕКС КОСТИ (uint32, значения 0..30 при 48
                                костях; есть только в форматах 21 и 28)

Проверки, на которых это установлено:

* `usage 2` встречается только в форматах 21 (hull, LOD) и 28 (детали), его
  значения строго меньше числа костей скелета (0..30 при 48 костях, 0..18 при
  меньших мешах) — единственный элемент с таким поведением;
* `usage 0` — три float32, совпадающие с габаритами модели из OBJ;
* `usage 3` — четыре байта около 0x80 на компоненту (нормаль в упакованном
  виде), `usage 4/type 1` — UV в диапазоне 0..1;
* в `.mdf` указано `InfluenceBones 1`, а после индексов файл заканчивается —
  отдельного массива весов нет, вес каждой вершины равен 1.0.

У форматов 11, 20, 27 элемента индекса кости нет: это жёстко привязанные
детали (корпус кабины и т.п.), их вершины не скинятся.
"""
import struct

POSITION = 0
BONE_INDEX = 2
NORMAL = 3
TEXCOORD = 4
TANGENT = 5

# usage -> (смысл, как декодировать)
SEMANTICS = {
    POSITION:   ("позиция",            "f32x3"),
    BONE_INDEX: ("индекс кости",       "u32"),
    NORMAL:     ("нормаль (упакована)", "bytes4"),
    TEXCOORD:   ("UV",                 "uv"),
    TANGENT:    ("касательная",        "bytes4"),
}


def describe(info, elements):
    """Человекочитаемая расшифровка формата вершины."""
    out = []
    for (usage, typ, off, size) in elements:
        name, how = SEMANTICS.get(usage, ("неизвестно", "?"))
        out.append("u%d t%d +%d (%d Б) — %s [%s]" % (usage, typ, off, size, name, how))
    return out


def position(data, base, stride, elements):
    """Позиция вершины по смещению из таблицы (не по фиксированному +0)."""
    for (usage, typ, off, size) in elements:
        if usage == POSITION and size >= 12:
            return struct.unpack_from("<3f", data, base + off)
    return (0.0, 0.0, 0.0)


def bone_index(data, base, elements):
    """Индекс кости или None, если формат его не содержит (жёсткая деталь)."""
    for (usage, typ, off, size) in elements:
        if usage == BONE_INDEX and size == 4:
            return struct.unpack_from("<I", data, base + off)[0]
    return None


def uv0(data, base, elements):
    """UV0 (2 × float32) или (0, 0)."""
    for (usage, typ, off, size) in elements:
        if usage == TEXCOORD and typ == 1 and size == 8:
            return struct.unpack_from("<2f", data, base + off)
    return (0.0, 0.0)


def has_skinning(elements):
    return bone_index(b"\0" * 64, 0, elements) is None and any(
        u == BONE_INDEX for (u, _t, _o, _s) in elements)


def table(elements):
    """dict: смысл -> (usage, type, offset, size)."""
    res = {}
    for (usage, typ, off, size) in elements:
        name = SEMANTICS.get(usage, ("unknown", "?"))[0]
        res.setdefault(name, (usage, typ, off, size))
    return res
