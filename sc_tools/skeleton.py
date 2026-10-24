#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чтение скелета Star Conflict — файлы `.mdl-skl`.

Размер файла сходится точно:
`32 + N * 224 == len(файл)`, где `N` — первое поле заголовка, поэтому
раскладка записи кости читается без догадок.

Заголовок (32 байта):

    @0   u32   число костей
    @4   u32   версия/флаги (0x00010001 у корпуса, 0x00010000 у частей)
    @8   u64   0
    @16  u32   1
    @20  12 Б  нули

Запись кости (224 байта):

    @0    32 Б  имя (ASCII, добито нулями)
    @32   i32   индекс родителя (-1 = корень)
    @36   28 Б  нули
    @64   f32×3 позиция кости
    @76   f32   0
    @80   f32×4 кватернион поворота (x, y, z, w); у недеформированных костей w≈0
    @96   f32×12 матрица поворота 3×4
    @144  f32×16 матрица привязки 4×4 в порядке «колонками»: третья колонка —
                перенос (Tx, Ty, Tz, 1)
    @208  f32×4 обратный перенос

Использование:

    from sc_tools import skeleton as SK
    skl = SK.parse_skl(r"...\\race1_l_t5_sniper.mdl-skl")
    print(skl["count"], [b["name"] for b in skl["bones"]][:5])
    tree = SK.bone_tree(skl["bones"])
"""
import os
import struct

HEADER = 32
BONE = 224


def parse_skl_bytes(data):
    """Разобрать содержимое `.mdl-skl`. Возвращает словарь."""
    if len(data) < HEADER:
        raise ValueError("файл скелета короче заголовка")
    count = struct.unpack_from("<I", data, 0)[0]
    flags = struct.unpack_from("<I", data, 4)[0]
    # У форматов с флагом 0x00020001 (версия 2) после записей костей лежит
    # ЕЩЁ 64 байта — габарит модели (min xyz, 0, max xyz, 0). Таких файлов
    # 2 из 3837: ship_ufo.mdl-skl и veteran_rclaw.mdl-skl. Поэтому хвост
    # допускаем, но требуем, чтобы записи костей целиком помещались.
    trailer = len(data) - (HEADER + count * BONE)
    if count == 0 or trailer not in (0, 64):
        raise ValueError("не сходится размер: костей %d, файл %d байт "
                         "(ожидалось %d)" % (count, len(data), HEADER + count * BONE))
    bones = []
    for i in range(count):
        o = HEADER + i * BONE
        name = data[o:o + 32].split(b"\0")[0].decode("latin1")
        parent = struct.unpack_from("<i", data, o + 32)[0]
        pos = struct.unpack_from("<3f", data, o + 64)
        quat = struct.unpack_from("<4f", data, o + 80)
        rot = struct.unpack_from("<12f", data, o + 96)
        bind = struct.unpack_from("<16f", data, o + 144)
        inv = struct.unpack_from("<4f", data, o + 208)
        bones.append({
            "index": i,
            "name": name,
            "parent": parent,
            "position": pos,
            "rotation": quat,          # (x, y, z, w)
            "pad_raw": data[o + 36:o + 64],       # неизвестные 28 Б
            "position_raw": data[o + 64:o + 76],  # байты позиции как есть
            "quat_raw": data[o + 80:o + 96],      # байты кватерниона как есть
            "rot_matrix": rot,         # 3 строки по 4 значения
            "bind_matrix": bind,       # 4×4, колонками: [col0][col1][col2][col3]
            "bind_translation": bind[12:15],
            "inverse_translation": inv[:3],
            "root": parent < 0,
        })
    return {"count": count, "flags": flags, "bones": bones, "size": len(data),
            "trailer": trailer}


def parse_skl(path):
    """Прочитать `.mdl-skl` с диска."""
    with open(path, "rb") as fh:
        data = fh.read()
    info = parse_skl_bytes(data)
    info["path"] = path
    return info


def bone_tree(bones):
    """Дерево костей: список корней, у каждой кости — дети."""
    by_index = {b["index"]: dict(b, children=[]) for b in bones}
    roots = []
    for b in by_index.values():
        p = b["parent"]
        if p in by_index:
            by_index[p]["children"].append(b)
        else:
            roots.append(b)
    return roots


def find_bone(bones, name):
    """Кость по имени (без учёта регистра) или None."""
    low = str(name).lower()
    for b in bones:
        if b["name"].lower() == low:
            return b
    return None


def walk(bones):
    """Обход костей в порядке «родитель раньше ребёнка»."""
    by_index = {b["index"]: b for b in bones}
    out = []
    seen = set()

    def visit(b):
        if b["index"] in seen:
            return
        seen.add(b["index"])
        out.append(b)
        for c in b["children"] if "children" in b else []:
            visit(c)

    for r in bone_tree(bones):
        visit(r)
    for b in bones:                     # осиротевшие (битый родитель)
        if b["index"] not in seen:
            out.append(b)
            seen.add(b["index"])
    return out


def dump(skl):
    """Текстовое представление для CLI."""
    lines = ["скелет: костей %d, флаги 0x%08x, размер %d байт" %
             (skl["count"], skl["flags"], skl["size"])]
    for b in skl["bones"]:
        px, py, pz = b["position"]
        qx, qy, qz, qw = b["rotation"]
        lines.append("  %3d %-24s родитель %4d  поз (%8.4f %8.4f %8.4f)  q (%7.4f %7.4f %7.4f %7.4f)"
                     % (b["index"], b["name"], b["parent"], px, py, pz, qx, qy, qz, qw))
    return "\n".join(lines)


def find_skeletons(root):
    """Все `.mdl-skl` в дереве каталогов."""
    res = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".mdl-skl"):
                res.append(os.path.join(dp, f))
    return sorted(res)
