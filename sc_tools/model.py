#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.model — Star Conflict .mdl-msh### -> Wavefront OBJ (с нормалями и UV)

Формат восстановлен по декомпиляции game.exe (Ghidra: FUN_140787340 — чтение
мешей, FUN_140787d40 — разбор формата вершин, таблица DAT_140c76c18) и
подтверждён на всём корпусе: 12 298 из 12 298 мешей дают точное совпадение
размера файла.

Заголовок .mdl-msh### (68 байт):
    @0x00 u32  флаг
    @0x04 u32  битфилд формата вершин (5 потоков по 5 бит; биты 26..28 —
               первый активный поток, 29..31 — последний)
    @0x08 u32  stride вершины
    @0x0C u32  n_vtx
    @0x10 u32  n_idx
    @0x14 u32  1
    @0x18 u32  n_vtx (повтор)
    @0x1C..0x43  описатели/выравнивание

Данные:
    вершины: n_vtx записей по stride байт с offset 68
    индексы: n_idx значений u16 после вершин, выравнивание до 4 байт
    размер:  68 + n_vtx*stride + pad4(n_idx*2) == len(файла)

Вершина: последовательность элементов по таблице форматов. Элемент задаётся
парой (usage, type):
    usage: 0 POSITION, 1 BLENDWEIGHT, 2 BLENDINDICES, 3 NORMAL,
           4 TEXCOORD, 5 TANGENT, 6 BINORMAL, 7 COLOR
    type : код D3DDECLTYPE (2 = FLOAT3, 1 = FLOAT2, 8 = UBYTE4N, 9 = SHORT2N,
           10 = SHORT4N, 15 = FLOAT16_2, 13 = UDEC3, 14 = DEC3N и т. д.)

(b/255)*2-1; UV типа FLOAT2 лежат в [0,1]; TANGENT тоже единичной длины.
"""
import os
import struct

from . import material as matmod

MSH_HEADER = 68

# ---------------------------------------------------------------- таблицы
# код D3DDECLTYPE -> размер в байтах
TYPE_SIZE = {
    0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4, 6: 4, 7: 8, 8: 4,
    9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 14: 4, 15: 4, 16: 8,
}

# код формата -> [(usage, type), ...]  (таблица DAT_140c76c18)
FORMAT_TABLE = {
    1: [(0, 2)],
    2: [(0, 2), (4, 1)],
    3: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9), (4, 9), (2, 5), (1, 10)],
    4: [(0, 2), (3, 10)],
    5: [(0, 2), (3, 10), (4, 1)],
    6: [(0, 2), (7, 4)],
    7: [(0, 2), (7, 4), (4, 1)],
    8: [(0, 1), (2, 6)],
    9: [(0, 2), (4, 1), (2, 6)],
    10: [(0, 1), (4, 1), (7, 4)],
    11: [(0, 2), (3, 8), (4, 1)],
    12: [(0, 2), (3, 8), (4, 1), (2, 5)],
    13: [(0, 2), (3, 8), (4, 1), (2, 5), (1, 10)],
    14: [(0, 2), (3, 8), (4, 1), (4, 9)],
    15: [(0, 2), (3, 8), (4, 1), (4, 9), (2, 5)],
    16: [(0, 2), (3, 8), (4, 1), (4, 9), (2, 5), (1, 10)],
    17: [(0, 2), (3, 8), (5, 8), (4, 1)],
    18: [(0, 2), (3, 8), (5, 8), (4, 1), (2, 5)],
    19: [(0, 2), (3, 8), (5, 8), (4, 1), (2, 5), (1, 10)],
    20: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9)],
    21: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9), (2, 5)],
    22: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9), (2, 5), (1, 10)],
    23: [(0, 2), (3, 8), (5, 8), (6, 8), (4, 1), (4, 9)],
    24: [(0, 2), (3, 8), (4, 9)],
    25: [(0, 2), (3, 8), (5, 8), (4, 9)],
    26: [(0, 2), (3, 8), (7, 4)],
    27: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9), (4, 9)],
    28: [(0, 2), (3, 8), (5, 8), (4, 1), (4, 9), (4, 9), (2, 5)],
    29: [(7, 4), (0, 0), (4, 0), (0, 0), (4, 6)],
    30: [(3, 8), (5, 8)],
    31: [(4, 8), (7, 8)],
}

USAGE_NAMES = {0: "POSITION", 1: "BLENDWEIGHT", 2: "BLENDINDICES", 3: "NORMAL",
               4: "TEXCOORD", 5: "TANGENT", 6: "BINORMAL", 7: "COLOR"}

# обратная совместимость: код формата -> список размеров элементов
FORMAT_SIZES = {c: [TYPE_SIZE[t] for (u, t) in els] for c, els in FORMAT_TABLE.items()}

STREAM_SHIFTS = (0, 5, 10, 16, 21)


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def f32(b, o):
    return struct.unpack_from("<f", b, o)[0]


def stream_codes(fmt):
    """Коды активных потоков из битфилда формата."""
    first = (fmt >> 26) & 7
    last = (fmt >> 29) & 7
    return [(fmt >> STREAM_SHIFTS[i]) & 0x1F
            for i in range(first, min(last, 4) + 1)]


def vertex_elements(fmt):
    """[(usage, type, offset, size), ...] для кода формата."""
    els = FORMAT_TABLE.get(fmt)
    if not els:
        return []
    out = []
    off = 0
    for usage, typ in els:
        size = TYPE_SIZE.get(typ, 4)
        out.append((usage, typ, off, size))
        off += size
    return out


# ------------------------------------------------------- декодеры элементов
def _clamp(v, lo=-1.0, hi=1.0):
    return lo if v < lo else (hi if v > hi else v)


def dec_vec(data, o, typ):
    """Вектор (x, y, z [, w]) из элемента по его типу."""
    if typ == 2:                      # FLOAT3
        return struct.unpack_from("<3f", data, o)
    if typ == 3:                      # FLOAT4
        return struct.unpack_from("<4f", data, o)
    if typ == 8:                      # UBYTE4N
        return tuple((data[o + i] / 255.0) * 2 - 1 for i in range(3))
    if typ == 7:                      # SHORT4 (знаковый, не нормированный)
        s = struct.unpack_from("<4h", data, o)
        return tuple(s[i] / 32767.0 for i in range(3))
    if typ == 9:                      # SHORT2N
        s = struct.unpack_from("<2h", data, o)
        x, y = s[0] / 32767.0, s[1] / 32767.0
        z = max(0.0, 1.0 - x * x - y * y) ** 0.5
        return (x, y, z)
    if typ == 10:                     # SHORT4N
        s = struct.unpack_from("<4h", data, o)
        return tuple((s[i] / 32767.0) * 2 - 1 for i in range(3))
    if typ == 11:                     # USHORT2N
        s = struct.unpack_from("<2H", data, o)
        x, y = s[0] / 32767.5 - 1.0, s[1] / 32767.5 - 1.0
        z = max(0.0, 1.0 - x * x - y * y) ** 0.5
        return (x, y, z)
    if typ == 12:                     # USHORT4N
        s = struct.unpack_from("<4H", data, o)
        return tuple((s[i] / 32767.5) - 1.0 for i in range(3))
    if typ in (13, 14):               # UDEC3 / DEC3N
        v = u32(data, o)
        comp = [(v >> 22) & 0x3FF, (v >> 12) & 0x3FF, (v >> 2) & 0x3FF]
        if typ == 14:
            comp = [c - 1024 if c >= 512 else c for c in comp]
            return tuple(c / 511.0 for c in comp)
        return tuple((c / 1023.0) * 2 - 1 for c in comp)
    if typ == 15:                     # FLOAT16_2
        h = struct.unpack_from("<2e", data, o)
        x, y = float(h[0]), float(h[1])
        z = max(0.0, 1.0 - x * x - y * y) ** 0.5
        return (x, y, z)
    if typ == 16:                     # FLOAT16_4
        h = struct.unpack_from("<4e", data, o)
        return (float(h[0]), float(h[1]), float(h[2]))
    if typ == 4:                      # D3DCOLOR (BGRA)
        return (data[o + 2] / 255.0 * 2 - 1, data[o + 1] / 255.0 * 2 - 1,
                data[o] / 255.0 * 2 - 1)
    if typ == 5:                      # UBYTE4
        return (data[o] / 255.0, data[o + 1] / 255.0, data[o + 2] / 255.0)
    if typ == 6:                      # SHORT2
        s = struct.unpack_from("<2h", data, o)
        return (s[0] / 32767.0, s[1] / 32767.0, 0.0)
    if typ == 0:                      # FLOAT1
        return (struct.unpack_from("<f", data, o)[0], 0.0, 0.0)
    if typ == 1:                      # FLOAT2
        x, y = struct.unpack_from("<2f", data, o)
        return (x, y, 0.0)
    return (0.0, 0.0, 1.0)


def dec_uv(data, o, typ):
    """UV-пара (u, v) из элемента по его типу."""
    if typ == 1:                      # FLOAT2
        return struct.unpack_from("<2f", data, o)
    if typ in (6, 9):                 # SHORT2 / SHORT2N -> 0..1
        s = struct.unpack_from("<2h", data, o)
        return (s[0] / 32767.0, s[1] / 32767.0)
    if typ == 11:                     # USHORT2N
        s = struct.unpack_from("<2H", data, o)
        return (s[0] / 65535.0, s[1] / 65535.0)
    if typ == 15:                     # FLOAT16_2
        h = struct.unpack_from("<2e", data, o)
        return (float(h[0]), float(h[1]))
    if typ == 5:                      # UBYTE4
        return (data[o] / 255.0, data[o + 1] / 255.0)
    if typ == 0:                      # FLOAT1
        return (struct.unpack_from("<f", data, o)[0], 0.0)
    if typ == 2:                      # FLOAT3 (изредка)
        x, y, z = struct.unpack_from("<3f", data, o)
        return (x, y)
    return (0.0, 0.0)


# ------------------------------------------------------------- разбор меша
def parse_msh(data):
    """Разбор заголовка .mdl-msh### -> dict с параметрами меша."""
    if len(data) < MSH_HEADER:
        raise ValueError("файл меша меньше %d байт" % MSH_HEADER)
    flag, fmt, stride, n_vtx, n_idx = struct.unpack_from("<5I", data, 0)
    elems = vertex_elements(fmt)
    vtx_off = MSH_HEADER
    idx_off = vtx_off + n_vtx * stride
    pad = ((n_idx * 2 + 3) // 4) * 4
    return dict(flag=flag, fmt=fmt, stride=stride, n_vtx=n_vtx, n_idx=n_idx,
                streams=stream_codes(fmt), elements=elems,
                elements_sum=sum(e[3] for e in elems),
                vtx_off=vtx_off, idx_off=idx_off, expect=idx_off + pad,
                size=len(data), exact=(idx_off + pad == len(data)),
                known_format=bool(elems))


def _find_element(info, usage, index=0):
    """Смещение и тип элемента с данным usage (index — какой по счёту)."""
    seen = 0
    for u, t, off, size in info["elements"]:
        if u == usage:
            if seen == index:
                return off, t
            seen += 1
    return None, None


def read_msh(path, want_uv=True, want_normals=True, uv_index=0):
    """
    Чтение меша: позиции, индексы, а также нормали и UV (если есть).

    uv_index — какой набор TEXCOORD брать (0 — первый, обычно основной;
    1 — второй, обычно lightmap). У мешей бывает до двух наборов.
    """
    data = open(path, "rb").read()
    info = parse_msh(data)
    info["has_normals"] = _find_element(info, 3)[0] is not None
    info["has_uv"] = _find_element(info, 4)[0] is not None
    info["uv_count"] = sum(1 for e in info["elements"] if e[0] == 4)

    off, stride, n = info["vtx_off"], info["stride"], info["n_vtx"]
    pos = []
    nrm = []
    uvs = []
    tan = []
    inten = []
    bone = []
    bsets = []
    wsets = []
    noff, ntyp = _find_element(info, 3)
    boff = None
    for (u, t, o_, s) in info["elements"]:
        if u == 2 and s == 4:
            boff = o_
            break
    # веса скиннинга: элемент usage 1 (4 × u16, 0x7FFF = 1.0). Есть только у
    # форматов с InfluenceBones > 1 (например fmt 22 у race2_m_t5_baphomet)
    woff = None
    for (u, t, o_, s) in info["elements"]:
        if u == 1 and s >= 8:
            woff = o_
            break
    info["has_bone_weights"] = woff is not None
    info["has_bone_index"] = boff is not None
    uoff, utyp = _find_element(info, 4, uv_index)
    # Степень подмешивания `_s1` берём из четвёртого компонента TANGENT
    # (у NORMAL он всегда 0).
    toff, ttyp = _find_element(info, 5)

    for i in range(n):
        o = off + i * stride
        if o + 12 > len(data):
            break
        if info["elements"] and info["elements"][0][0] == 0:
            pos.append(dec_vec(data, o, info["elements"][0][1]))
        else:
            x, y, z = struct.unpack_from("<3f", data, o)
            pos.append((x, y, z) if x == x and y == y and z == z else (0.0, 0.0, 0.0))
        if want_normals and noff is not None and ntyp not in (None, 4):
            nrm.append(dec_vec(data, o + noff, ntyp))
        if want_uv and uoff is not None:
            uvs.append(dec_uv(data, o + uoff, utyp))
        if toff is not None and ttyp not in (None, 4):
            tv = dec_vec(data, o + toff, ttyp)
            if ttyp == 8 and o + toff + 3 < len(data):
                # 4-й байт UBYTE4N у тангенса — знак бинормали
                tv = tuple(tv) + (1.0 if data[o + toff + 3] else -1.0,)
            tan.append(tv)
        if toff is not None and ttyp == 8:            # UBYTE4N: 4-й байт
            p = o + toff + 3
            inten.append((data[p] / 255.0) if p < len(data) else 0.0)
        else:
            inten.append(0.0)
        if boff is not None:
            # ВАЖНО: элемент usage 2 — это ЧЕТЫРЕ байта, до четырёх индексов
            # костей (u8 × 4), а не один u32. Если читать как u32, у вершин с
            # двумя-тремя костями выходят «индексы» 8192, 7680, 256 и т.п. —
            # это второй/третий байт в старших разрядах.
            if o + boff + 4 <= len(data):
                b4 = data[o + boff: o + boff + 4]
            else:
                b4 = b"\x00\x00\x00\x00"
            bone.append(b4[0])
            bsets.append((b4[0], b4[1], b4[2], b4[3]))
            if woff is not None and o + woff + 8 <= len(data):
                ws = struct.unpack_from("<4H", data, o + woff)
                wsets.append(tuple(w / 32767.0 for w in ws))
            else:
                wsets.append((1.0, 0.0, 0.0, 0.0))
        else:
            bsets.append(None)
            wsets.append(None)

    idx = []
    for i in range(info["n_idx"]):
        o = info["idx_off"] + i * 2
        if o + 2 > len(data):
            break
        v = u16(data, o)
        if v >= n:
            break
        idx.append(v)

    info["positions"] = pos
    info["indices"] = idx
    info["normals"] = nrm if len(nrm) == len(pos) else []
    info["uvs"] = uvs if len(uvs) == len(pos) else []
    info["tangents"] = tan if len(tan) == len(pos) else []
    info["intensity"] = inten if len(inten) == len(pos) else []
    # Скиннинг. Элемент usage 2 — 4 байта, до четырёх индексов костей; у
    # форматов с InfluenceBones > 1 рядом лежат веса (usage 1, 4 × u16 / 32767).
    #   bone_index       — первая (главная) кость вершины, для совместимости
    #   bone_influences  — [(кость, вес), …] только с ненулевым весом
    # У формата 21 (Gungnir, Phoenix) элемента весов нет: одна кость, вес 1.0.
    info["bone_index"] = bone if (bone and len(bone) == len(pos)) else []
    influences = []
    for i in range(len(pos)):
        bset = bsets[i] if i < len(bsets) else None
        wset = wsets[i] if i < len(wsets) else None
        if not bset:
            continue
        if wset is None:
            influences.append([(int(bset[0]), 1.0)])
        else:
            pairs = [(int(b), float(w)) for b, w in zip(bset, wset) if w > 0.0]
            influences.append(pairs or [(int(bset[0]), 1.0)])
    info["bone_influences"] = influences if len(influences) == len(pos) else []
    return info


def parse_mdl_hdr(data):
    """Bounding box из .mdl-hdr (min @0, max @0x10)."""
    if len(data) < 28:
        return None
    return dict(bbox_min=(f32(data, 0), f32(data, 4), f32(data, 8)),
                bbox_max=(f32(data, 16), f32(data, 20), f32(data, 24)))


# ---------------------------------------------------------------- запись OBJ
def write_obj(path, meshes, swap_yz=False, scale=1.0, flip_v=True,
              normals=True, uvs=True, mtllib=None, uv_scale=1.0):
    """
    Запись OBJ. meshes = [(имя, positions, indices, normals, uvs[, материал]), ...]
    Нормали и UV берутся по тем же индексам, что и позиции (v/vt/vn).
    Если задан mtllib, в файл пишется ссылка на .mtl и usemtl для каждой группы.
    """
    lines = ["# Star Conflict (Hammer Engine) - sc_tools.model"]
    if mtllib:
        lines.append("mtllib %s" % mtllib)
    base = 1
    vbase = 1        # база индексов вершин
    tbase = 1        # база индексов UV (считается отдельно!)
    nbase = 1        # база индексов нормалей (тоже отдельно)
    nv = nt = 0
    for item in meshes:
        name, pos, idx, nrm, uv = item[:5]
        mat = item[5] if len(item) > 5 else None
        vcol = item[6] if len(item) > 6 else None
        lines.append("g %s" % name)
        if mat:
            lines.append("usemtl %s" % mat)
        for i, (x, y, z) in enumerate(pos):
            if swap_yz:
                x, y, z = x, z, -y
            # Цвет вершины = intensity (маска подмешивания _s1). Blender читает
            # расширение OBJ "v x y z r g b" как цветовой атрибут.
            if vcol and i < len(vcol):
                c = vcol[i]
                lines.append("v %.6f %.6f %.6f %.4f %.4f %.4f" %
                             (x * scale, y * scale, z * scale, c, c, c))
            else:
                lines.append("v %.6f %.6f %.6f" % (x * scale, y * scale, z * scale))
        if uvs and uv:
            for u, v in uv:
                if flip_v:
                    v = 1.0 - v
                lines.append("vt %.6f %.6f" % (u * uv_scale, v * uv_scale))
        if normals and nrm:
            for i, (x, y, z) in enumerate(nrm):
                if swap_yz:
                    x, y, z = x, z, -y
                lines.append("vn %.6f %.6f %.6f" % (x, y, z))

        use_uv = bool(uvs and uv)
        use_n = bool(normals and nrm)
        for i in range(0, len(idx) - 2, 3):
            a, b, c = idx[i], idx[i + 1], idx[i + 2]
            if a == b or b == c or a == c:
                continue
            # D3D считает лицевой гранью обход ПО часовой стрелке, OBJ/OpenGL —
            # против. Поэтому порядок вершин разворачиваем (a, c, b), иначе
            # нормали граней смотрят внутрь модели.
            a, b, c = a, c, b
            # Индексы v, vt и vn считаются от разных баз.
            if use_uv and use_n:
                lines.append("f %d/%d/%d %d/%d/%d %d/%d/%d" %
                             (vbase + a, tbase + a, nbase + a,
                              vbase + b, tbase + b, nbase + b,
                              vbase + c, tbase + c, nbase + c))
            elif use_n:
                lines.append("f %d//%d %d//%d %d//%d" %
                             (vbase + a, nbase + a, vbase + b, nbase + b,
                              vbase + c, nbase + c))
            elif use_uv:
                lines.append("f %d/%d %d/%d %d/%d" %
                             (vbase + a, tbase + a, vbase + b, tbase + b,
                              vbase + c, tbase + c))
            else:
                lines.append("f %d %d %d" % (vbase + a, vbase + b, vbase + c))
            nt += 1
        vbase += len(pos)
        if use_uv:
            tbase += len(uv)
        if use_n:
            nbase += len(nrm)
        nv += len(pos)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return nv, nt


def mesh_files(base):
    """Все .mdl-msh### для базового пути модели (без расширения)."""
    d = os.path.dirname(base) or "."
    stem = os.path.basename(base)
    return [os.path.join(d, f) for f in sorted(os.listdir(d))
            if f.startswith(stem + ".mdl-msh")]


def _bbox_of_path(mp):
    """Габариты меша по позициям (быстро, без нормалей и UV)."""
    data = open(mp, "rb").read()
    info = parse_msh(data)
    n, off, stride = info["n_vtx"], info["vtx_off"], info["stride"]
    mn = [1e30] * 3
    mx = [-1e30] * 3
    for i in range(n):
        o = off + i * stride
        if o + 12 > len(data):
            break
        x, y, z = struct.unpack_from("<3f", data, o)
        if x != x or y != y or z != z:
            continue
        for k, v in enumerate((x, y, z)):
            if v < mn[k]:
                mn[k] = v
            if v > mx[k]:
                mx[k] = v
    if mn[0] > 1e29:
        return None
    return (mn[0], mn[1], mn[2], mx[0], mx[1], mx[2])


def lod_groups(base, verify=True):
    """Разбиение мешей модели по уровням детализации — по спеке формата.

    `.mdl-hdr` +0x20: четыре u16, где lod[k] = ИНДЕКС ПОСЛЕДНЕЙ детали уровня k
    (включительно), уровни вложенные:
        LOD0 = детали 0..lod[0], LOD1 = 0..lod[1], ...
    Все нули — один LOD. Если lod[0] < parts-1, детали после lod[0] —
    всегда рисуемые attachments (они попадают в каждую группу).

    Возвращает [[путь, ...], ...] — группы от самого детального уровня.
    """
    paths = mesh_files(base)
    hdr = base + ".mdl-hdr"
    if not os.path.exists(hdr):
        return [paths]
    try:
        raw = open(hdr, "rb").read()
        vals = list(struct.unpack_from("<4H", raw, 0x20))
    except Exception:
        return [paths]
    last = [v for v in vals if 0 < v < len(paths)]
    if not last:
        return [paths]                     # один LOD
    if last != sorted(last, reverse=True):
        return [paths]                     # не монотонно — считаем одним LOD
    # детали после lod[0] — attachments: рисуются всегда, входят в каждый уровень
    base = max(last)
    tail = paths[base + 1:]
    groups = [paths[:v + 1] + tail for v in last]
    return groups
def compose_paint(tex_dir, mats, paint, tex_index=None):
    """
    Покраска корпуса: `_s1` (маска слоёв) x цвета красителей -> «покрашенные» текстуры.

    Формула — ветка DYEING шейдера (object.fx:435-458), её и использует ship_hull
    (шаблон ship_hull.mmp:56 -> OBJECT_CRYSTAL -> OBJECT_GAME_EFFECTS DYEING):
        layersMask   = tex2D( ColormapSampler, baseUv )      // baseUv = uv0, без масштаба
        dyedAlbedo   = c1*mask.r + c2*mask.g + c3*mask.b     // c_i = UserParam3_Float4[1/4/7].rgb
        dyeInfluence = saturate( mask.r + mask.g + mask.b )
        albedo.rgb   = lerp( albedo.rgb, dyedAlbedo, dyeInfluence )
    ВАЖНО: вершинный множитель (TANGENT.w) в этой ветке НЕ участвует — там
    `o.normalWS.w = GlobalTime.y / 10` (object.fx:200-202); он нужен только BLEND.

    paint — три красителя в порядке слоёв R, G, B: имя из ShipDyes (`default_craft_r1_3`)
    или кортеж RGB 0..255. Текстуры пишутся в tex_dir как `<имя>_dyed.png`, а материалы
    начинают ссылаться на них — MTL соберётся уже с покраской.
    """
    import numpy as np
    from PIL import Image
    from . import dyes as _dyes
    if tex_index is None:
        tex_index = matmod.build_texture_index(tex_dir)

    table = _dyes.parse_ship_dyes()
    cols = []
    for p in list(paint)[:3]:
        if p is None or (isinstance(p, str) and p.strip().lower() in ("none", "-", "", "нет")):
            # Слой не красим: на его месте остаётся исходное альбедо `_d`.
            # В движке «некрашеный» вид — это краситель, совпадающий с базой,
            # поэтому из формулы покраски маску такого слоя надо исключить
            # (иначе чёрный/чужой цвет уводит корпус в темноту).
            cols.append(None)
            print("   слой %d -> не красим (остаётся _d)" % (len(cols) - 1))
            continue
        if isinstance(p, str):
            key, entry = _dyes.find_dye(table, p)
            if entry is None:
                raise ValueError("краситель не найден в ShipDyes: %r" % p)
            cols.append(np.array(entry["albedo"], np.float32) / 255.0)
            print("   слой %s -> %s" % (key, tuple(int(v) for v in entry["albedo"])))
        else:
            cols.append(np.array(p, np.float32) / 255.0)
    while len(cols) < 3:
        cols.append(cols[-1] if cols else None)

    def lin(a):
        a = np.asarray(a, np.float32)
        return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)

    def srgb(a):
        a = np.clip(np.asarray(a, np.float32), 0.0, 1.0)
        return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)

    made = []
    for m in mats:
        cm = m["samplers"].get("ColormapSampler")
        if not cm:
            continue
        mstem = os.path.basename(str(cm).replace("/", "\\"))
        mpath = tex_index.get(mstem.lower())
        if not mpath:
            continue
        mask = np.asarray(Image.open(mpath).convert("RGBA"), np.float32) / 255.0
        R, G, B = mask[:, :, 0], mask[:, :, 1], mask[:, :, 2]
        masks = (R, G, B)
        dyed = np.zeros(R.shape, np.float32)[..., None]
        influ = np.zeros(R.shape, np.float32)
        for _i, col in enumerate(cols):
            if col is None:                       # слой не красим — маску не учитываем
                continue
            dyed = dyed + masks[_i][..., None] * lin(col)
            influ = influ + masks[_i]
        influ = np.clip(influ, 0.0, 1.0)[..., None]

        for slot, parts in (("DiffuseSampler", ("r", "g", "b", "a")),
                            ("SpecularColorSampler", ("r", "g", "b", "a"))):
            epath = m["samplers"].get(slot)
            if not epath:
                continue
            stem = os.path.basename(str(epath).replace("/", "\\"))
            src = tex_index.get(stem.lower())
            if not src:
                continue
            img = np.asarray(Image.open(src).convert("RGBA"), np.float32) / 255.0
            out = srgb(lin(img[:, :, :3]) * (1.0 - influ) + dyed * influ)
            dst = os.path.join(tex_dir, stem + "_dyed.png")
            Image.fromarray(np.dstack([(out * 255.0).astype(np.uint8),
                                       (img[:, :, 3] * 255).astype(np.uint8)]), "RGBA").save(dst)
            m["samplers"][slot] = stem + "_dyed"
            # Индекс текстур строится до покраски: новый файл надо добавить в него самому,
            # иначе write_mtl не найдёт текстуру.
            tex_index[(stem + "_dyed").lower()] = dst
            made.append(dst)
    return made


def compose_rough(tex_dir, mats, tex_index=None):
    """
    Построить карту шероховатости из АЛЬФЫ блика (`_sc.a` = glossFactor).

    Глянец в движке задаёт степень блика Блинна-Фонга:
    `specPower = exp2(9 * glossFactor + 2)` (object.fx:683). В Blender
    шероховатость связана со степенью как `roughness = sqrt(2 / (specPower + 2))`,
    поэтому переносим именно так, а не как «1 - gloss»: иначе резкие блики
    становятся матовыми, а матовые — зеркальными.

    Пишет `<имя>_rough.png` и подставляет сэмплер `RoughnessSampler`
    (в MTL это `map_Pr`). Возвращает список созданных файлов.
    """
    import numpy as np
    from PIL import Image
    if tex_index is None:
        tex_index = matmod.build_texture_index(tex_dir)

    made = []
    for m in mats:
        ep = m["samplers"].get("SpecularColorSampler")
        if not ep:
            continue
        stem = os.path.basename(str(ep).replace("/", "\\"))
        src = tex_index.get(stem.lower())
        if not src:
            continue
        img = np.asarray(Image.open(src).convert("RGBA"), np.float32) / 255.0
        gloss = np.clip(img[:, :, 3], 0.0, 1.0)
        spec_power = np.power(2.0, 9.0 * gloss + 2.0)
        rough = np.sqrt(2.0 / (spec_power + 2.0))
        rough = np.clip(rough, 0.03, 1.0)
        dst = os.path.join(tex_dir, stem + "_rough.png")
        Image.fromarray((rough * 255.0).astype(np.uint8), "L").convert("RGBA").save(dst)
        m["samplers"]["RoughnessSampler"] = stem + "_rough"
        tex_index[(stem + "_rough").lower()] = dst
        made.append(dst)
    return made


_AO_CACHE = {}


def all_parts(folder):
    """Все части модели в папке: базовые пути без `.mdl-msh000`."""
    parts = []
    if os.path.isdir(folder):
        for f in sorted(os.listdir(folder)):
            if f.lower().endswith(".mdl-msh000"):
                parts.append(os.path.join(folder, f[:-len(".mdl-msh000")]))
    return parts


def union_ao_map(folder, pdo_path, uv_ao=1, uv_albedo=0, size=2048):
    """Карта AO по всем частям папки (кэшируется) — атлас у частей общий."""
    from . import bake as _bake
    import numpy as np
    key = (folder.lower(), size, uv_ao, uv_albedo, pdo_path)
    if key in _AO_CACHE:
        return _AO_CACHE[key]
    vmap = np.zeros((size, size), np.float32)
    cov = np.zeros((size, size), np.float32)
    n_tri = 0
    for base in all_parts(folder):
        groups = lod_groups(base)
        for mp in (groups[0] if groups else []):
            try:
                ia = read_msh(mp, want_uv=True, want_normals=False, uv_index=uv_albedo)
                ib = read_msh(mp, want_uv=True, want_normals=False, uv_index=uv_ao)
            except Exception:
                continue
            if not ia.get("uvs") or not ib.get("uvs") or not ia.get("indices"):
                continue
            ao = _bake.ao_from_pdo(pdo_path, ib["uvs"], channel=0)
            vm, cv = _bake.rasterize_uv(ia["indices"], ia["uvs"], ao, size)
            m = cv > 0.5
            vmap[m] = vm[m]
            cov[m] = 1.0
            n_tri += len(ia["indices"]) // 3
    _AO_CACHE[key] = (vmap, cov)
    return vmap, cov


def compose_ao(tex_dir, mats, base, tex_index=None, uv_ao=1, uv_albedo=0, size=None,
               strength=0.5):
    """
    Запечь AO из `_pdo` (LightmapSampler) в альбедо.

    `strength` ослабляет эффект: множитель = `1 - strength * (1 - AO)`. При 1.0
    альбедо умножается на AO целиком — это даёт слишком глубокие тени. В движке AO
    влияет на подсветку/отражения, а не затемняет саму текстуру вдвое, поэтому
    по умолчанию 0.5.

    Зачем: `_pdo` сэмплится ВТОРЫМ набором UV, а OBJ несёт только один, поэтому
    отдельной картой AO в Blender не подключить. Запекаем: AO берётся из красного
    канала `_pdo` по второму набору UV (object.fx:614-629, `globalOcclusion =
    pdoTex.x`), растеризуется в пространство ПЕРВОГО набора UV и умножается на
    альбедо. Работает модуль `sc_tools.bake`.

    Возвращает список созданных файлов.
    """
    from . import bake as _bake
    if tex_index is None:
        tex_index = matmod.build_texture_index(tex_dir)

    pdo_path = None
    for m in mats:
        ep = m["samplers"].get("LightmapSampler")
        if ep:
            pdo_path = tex_index.get(os.path.basename(str(ep).replace("/", "\\")).lower())
            if pdo_path:
                break
    if not pdo_path:
        return []

    # UV и индексы всех мешей LOD0 этой части
    import numpy as np
    from PIL import Image
    albedo_path = None
    for m in mats:
        ep = m["samplers"].get("DiffuseSampler")
        if ep:
            albedo_path = tex_index.get(os.path.basename(str(ep).replace("/", "\\")).lower())
            if albedo_path:
                break
    if not albedo_path:
        return []
    if size is None:
        size = Image.open(albedo_path).size[0]

    # ВАЖНО: атлас общий для всех частей корабля, поэтому AO собирается один раз
    # по ВСЕМ частям папки (иначе последняя часть — обычно крошечная — перезапишет
    # общий файл своим почти пустым покрытием). Карта кэшируется на папку.
    vmap, cov = union_ao_map(os.path.dirname(base) or ".", pdo_path,
                             uv_ao=uv_ao, uv_albedo=uv_albedo, size=size)
    if strength < 1.0:
        # ослабляем AO: множитель 1 - strength*(1-AO)
        vmap = 1.0 - strength * (1.0 - vmap)

    made = []
    for m in mats:
        # Декaльные материалы не трогаем: их альбедо — атлас знака, а не корпус,
        # и AO туда запекать не нужно (атлас к тому же общий для многих кораблей).
        if "decal" in m["name"].lower():
            continue
        ep = m["samplers"].get("DiffuseSampler")
        if not ep:
            continue
        stem = os.path.basename(str(ep).replace("/", "\\"))
        src = tex_index.get(stem.lower())
        if not src:
            continue
        dst = os.path.join(tex_dir, stem + "_ao.png")
        _bake.multiply_texture(src, vmap, cov, dst, size=size)
        m["samplers"]["DiffuseSampler"] = stem + "_ao"
        tex_index[(stem + "_ao").lower()] = dst
        made.append(dst)
    return made


def model_to_obj(path_or_base, out_dir=None, swap_yz=False, merge=True,
                 scale=1.0, flip_v=True, normals=True, uvs=True,
                 materials=True, tex_dir=None, lod=0, uv_index=0, uv_scale=1.0,
                 decal=None, decals_dat=None, ship_decals_lua=None, paint=None, ao=False,
                 rough=True, prefer="png"):
    """
    Конвертация модели (или одного меша) в OBJ.
    lod — какой уровень детализации брать (0 = самый подробный).
    По умолчанию берётся только LOD0: иначе несколько копий корабля
    накладываются друг на друга.
    materials=True — разобрать .mdf, записать .mtl и привязать текстуры.
    decal — имя знака: ищется в `ShipDecals` (ship_decals.lua, знаки на корабле) и,
    если там нет, в `decals.dat` (проекционные декали). Для знака корабля текстуры
    подставляются в материал декали, UV берутся третьим набором как есть; для
    проекционной декали UV переносятся в её прямоугольник.
    Возвращает (список_файлов, сводка).
    """
    p = str(path_or_base)
    for ext in (".mdl-msh", ".mdl-hdr"):
        if ext in p.lower():
            p = p[:p.lower().index(ext)]
            break
    groups = lod_groups(p)
    if not groups:
        raise FileNotFoundError("не найдено .mdl-msh### для %s" % p)
    if lod < 0 or lod >= len(groups):
        lod = 0
    paths = groups[lod]
    base_name = os.path.basename(p)
    out_dir = out_dir or os.path.dirname(p) or "."
    os.makedirs(out_dir, exist_ok=True)

    # --- декаль ---
    # Знак на корабле сэмплится третьим набором UV как есть
    # (object.fx, ветка PD_OCCL && SHIP_DECAL). Прямоугольники из decals.dat —
    # механизм проекционных декалей, к знакам не относятся.
    decal_entry, decal_kind, decal_key = None, None, None
    if decal:
        try:
            from . import decals as _dec
        except ImportError:
            import decals as _dec
        _tokens = _dec.parse_ship_decals(ship_decals_lua)
        decal_key, decal_entry = _dec.find_ship_decal(_tokens, decal)
        if decal_entry is not None:
            decal_kind = "ship"
            print("декаль: %s (знак на корабле, ShipDecals) -> albedo=%s" %
                  (decal_key, decal_entry.get("albedo")))
        else:
            _all = _dec.parse_decals(decals_dat)
            decal_key, decal_entry = _dec.find_decal(_all, decal)
            if decal_entry is None:
                raise ValueError("декаль не найдена ни в ShipDecals (ship_decals.lua), "
                                 "ни в decals.dat: %r" % decal)
            decal_kind = "projected"
            print("декаль: %s (проекционная, decals.dat) -> uv=%s tex=%s; "
                  "UV будут перенесены в этот прямоугольник" %
                  (decal_key, decal_entry.get("uv"), _dec.texture_name(decal_entry)))

    # --- материалы ---
    mats, mdf_path = ([], None)
    if materials:
        mats, mdf_path = matmod.load_materials(p)
    if mats and decal_kind == "ship" and decal_entry:
        # У материала ship_decals в .mdf диффуза нет (текстуру подставляет рантайм) —
        # подставляем текстуры выбранного знака по слотам из шаблона ship_decals.mmp:
        # albedo -> DiffuseSampler, spec -> SpecularColorSampler,
        # bump -> DetailSampler, glow -> Diffuse2Sampler.
        slots = (("albedo", "DiffuseSampler"), ("spec", "SpecularColorSampler"),
                 ("bump", "DetailSampler"), ("glow", "Diffuse2Sampler"))
        for m in mats:
            if "decal" in m["name"].lower() and "static" not in m["name"].lower():
                for src, slot in slots:
                    val = decal_entry.get(src)
                    if val:
                        m["samplers"][slot] = val
    mtl_file = None
    n_mat = n_tex = 0
    if mats:
        mtl_file = base_name + ".mtl"
        td = tex_dir or os.path.join(out_dir, "textures")
        tex_index = matmod.build_texture_index(td, prefer=prefer)
        if paint:
            made = compose_paint(td, mats, paint, tex_index=tex_index)
            if made:
                print("покраска: %s" % ", ".join(os.path.basename(x) for x in made))
        if ao:
            made = compose_ao(td, mats, p, tex_index=tex_index)
            if made:
                print("AO из _pdo запечён в: %s" % ", ".join(os.path.basename(x) for x in made))
        if rough and str(prefer).lower() != "dds":
            # В режиме prefer="dds" никаких производных PNG не создаём: материал
            # строится в Blender прямо из DDS (шероховатость считается в нодах
            # из альфы _sc).
            made = compose_rough(td, mats, tex_index=tex_index)
            if made:
                print("шероховатость из альфы блика: %s" % ", ".join(os.path.basename(x) for x in made))
        # В режиме prefer="dds" отдельную карту прозрачности не пишем: альфа
        # берётся из собственного альфа-канала DDS знака — ровно как в движке
        # (object.fx: alphaValue = albedo.a). Иначе в MTL попадал бы * PNG.
        alpha_mats = () if str(prefer).lower() == "dds" else ("decal",)
        n_mat, n_tex = matmod.write_mtl(os.path.join(out_dir, mtl_file), mats,
                                        tex_dir=td, tex_index=tex_index,
                                        alpha_materials=alpha_mats)

    loaded = []
    skipped = []
    tv = tt = 0
    n_norm = n_uv = 0
    for mp in paths:
        try:
            info = read_msh(mp, want_uv=uvs, want_normals=normals, uv_index=uv_index)
        except Exception as e:
            skipped.append((os.path.basename(mp), str(e)))
            continue
        name = os.path.basename(mp).split(".mdl-msh")[-1]
        mat = None
        samplers = {}
        if mats:
            i = matmod.material_index(mp)
            if 0 <= i < len(mats):
                mat = mats[i]["name"]
                samplers = mats[i].get("samplers") or {}
        # UV декалей: при PD_OCCL берётся третий набор (uv1.zw), иначе второй (uv1.xy).
        if mat and "decal" in mat.lower() and info.get("uv_count", 0) > 2 and uv_index != 2:
            try:
                info2 = read_msh(mp, want_uv=uvs, want_normals=normals, uv_index=2)
                if info2.get("uvs"):
                    info = info2
            except Exception:
                pass

        # Проекционные декали: UV в мешах локальные (0..1), движок отображает их
        # в прямоугольник записи decals.dat.
        if decal_kind == "projected" and mat and "decal" in mat.lower():
            rect = (decal_entry or {}).get("uv")
            if rect and info.get("uvs"):
                try:
                    from . import decals as _dec
                except ImportError:
                    import decals as _dec
                info = dict(info)
                info["uvs"] = [_dec.map_uv(u, v, rect) for u, v in info["uvs"]]
        loaded.append(("%s_msh%s" % (base_name, name), info["positions"],
                       info["indices"], info["normals"], info["uvs"], mat,
                       info.get("intensity") or None))
        tv += len(info["positions"])
        tt += len(info["indices"]) // 3
        n_norm += len(info["normals"])
        n_uv += len(info["uvs"])

    hdr = None
    hp = p + ".mdl-hdr"
    if os.path.exists(hp):
        hdr = parse_mdl_hdr(open(hp, "rb").read())

    results = []
    if merge:
        out = os.path.join(out_dir, base_name + ".obj")
        write_obj(out, loaded, swap_yz=swap_yz, scale=scale, flip_v=flip_v, uv_scale=uv_scale,
                  normals=normals, uvs=uvs, mtllib=mtl_file)
        results.append(out)
    else:
        for item in loaded:
            name = item[0]
            out = os.path.join(out_dir, name + ".obj")
            write_obj(out, [item], swap_yz=swap_yz, scale=scale, flip_v=flip_v, uv_scale=uv_scale,
                      normals=normals, uvs=uvs, mtllib=mtl_file)
            results.append(out)

    return results, dict(base=base_name, meshes=len(loaded), vertices=tv,
                         triangles=tt, with_normals=n_norm, with_uv=n_uv,
                         materials=n_mat, textures_bound=n_tex, mtl=mtl_file,
                         lods=len(groups), lod=lod,
                         files=results, bbox=hdr, skipped=skipped)


def find_models(root):
    """Все модели (базовые пути без расширения) в дереве каталогов."""
    seen = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            low = f.lower()
            if ".mdl-msh" in low:
                seen[os.path.join(dp, f[:low.index(".mdl-msh")])] = True
    return sorted(seen)


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Star Conflict .mdl-msh -> OBJ")
    ap.add_argument("input", help="папка с моделями, база модели или файл .mdl-msh###")
    ap.add_argument("-o", "--out", help="папка для OBJ")
    ap.add_argument("--split", action="store_true", help="отдельный OBJ на каждый меш")
    ap.add_argument("--swap-yz", action="store_true", help="поменять Y/Z (Z-up -> Y-up)")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--keep-v", action="store_true", help="не переворачивать V в UV")
    ap.add_argument("--no-normals", action="store_true")
    ap.add_argument("--no-uv", action="store_true")
    a = ap.parse_args()

    bases = find_models(a.input) if os.path.isdir(a.input) else [a.input]
    print("моделей: %d" % len(bases))
    ok = 0
    for b in bases:
        try:
            files, s = model_to_obj(b, a.out, swap_yz=a.swap_yz, merge=not a.split,
                                    scale=a.scale, flip_v=not a.keep_v,
                                    normals=not a.no_normals, uvs=not a.no_uv)
            ok += 1
            print("  %-38s мешей=%-2d вершин=%-7d треуг.=%-7d норм.=%-7d uv=%-7d" %
                  (s["base"], s["meshes"], s["vertices"], s["triangles"],
                   s["with_normals"], s["with_uv"]))
        except Exception as e:
            print("  %-38s ОШИБКА: %s" % (os.path.basename(b), e))
    print("готово: %d моделей" % ok)


if __name__ == "__main__":
    _main()


FVF_STRIDE = {0x09: 24, 0x0B: 24, 0x0C: 28, 0x0E: 28, 0x11: 28, 0x14: 32,
              0x15: 36, 0x16: 44, 0x1B: 36, 0x1C: 40, 0x03: 48}


def check_counts(base):
    """Проверка пакета модели по спеке формата.

    Возвращает (ok, [сообщения]). Проверяется:
      * parts из +0x38 == числу файлов .mdl-msh###;
      * materials из +0x38 == числу блоков material в .mdf;
      * у каждой детали FVF (+0x04) соответствует страйду (+0x08) по таблице.
    """
    import struct as _st
    msgs = []
    ok = True
    hdr = base + ".mdl-hdr"
    if not os.path.exists(hdr):
        return False, ["нет .mdl-hdr"]
    raw = open(hdr, "rb").read()
    packed = _st.unpack_from("<I", raw, 0x38)[0]
    parts, mats = packed & 0xFFFF, packed >> 16
    files = mesh_files(base)
    if parts != len(files):
        ok = False
        msgs.append("parts=%d, файлов .mdl-msh=%d" % (parts, len(files)))
    mdf = base + ".mdf"
    if os.path.exists(mdf):
        try:
            txt = open(mdf, "rb").read().decode("latin1", "replace")
            nmat = txt.count("material ")
            if nmat != mats:
                ok = False
                msgs.append("materials=%d, блоков в .mdf=%d" % (mats, nmat))
        except Exception as exc:
            msgs.append("mdf: %s" % exc)
    for path in files:
        try:
            head = open(path, "rb").read(0x44)
            fvf, stride = _st.unpack_from("<II", head, 0x04)
            want = FVF_STRIDE.get(fvf)
            if want is not None and want != stride:
                ok = False
                msgs.append("%s: FVF 0x%X должен быть stride %d, а стоит %d"
                            % (os.path.basename(path), fvf, want, stride))
        except Exception as exc:
            msgs.append("%s: %s" % (os.path.basename(path), exc))
    return ok, msgs
