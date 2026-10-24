#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запись мешей Star Conflict — файлы `.mdl-msh###`.

Заголовок 68 байт:

    @0x00 u32  флаг (у большинства 0, у части мешей — номер части)
    @0x04 u32  код формата вершин (индекс в таблице FORMAT_TABLE)
    @0x08 u32  stride вершины (постоянен для формата)
    @0x0C u32  число вершин
    @0x10 u32  число индексов
    @0x14 u32  всегда 1
    @0x18 u32  неизвестно (зависит от меша)
    @0x1C u32  зависит от формата, у части форматов от меша
    @0x20 u32  зависит от меша
    @0x24 u32  зависит от меша
    @0x28 24 Б всегда нули
    @0x40 u32  всегда равен коду формата

Данные: `n_vtx` записей по `stride` байт с 68, затем `n_idx` значений u16,
выравнивание блока индексов до 4 байт; `68 + n_vtx*stride + pad4(n_idx*2)`
совпадает с размером файла у всех проверенных мешей.

Поля `@0x18`, `@0x1C`, `@0x20`, `@0x24` вывести пока не удалось, поэтому
писатель их **сохраняет как есть** (для неизменённого меша это даёт побайтовое
совпадение). Для меша «с нуля» они берутся из образца (`template`) — меша того
же формата, — либо задаются явно через `opaque=`.

Использование:

    from sc_tools import msh
    info = msh.parse_file(r"...\\race1_l_t5_sniper.mdl-msh000")
    ok = msh.build(info) == open(path, "rb").read()      # побайтовое совпадение

    # правка геометрии: пересобираем блок вершин и индексов
    msh.set_vertices(info, positions=new_positions)
    msh.set_indices(info, new_indices)
    open("out.mdl-msh000", "wb").write(msh.build(info))
"""
import os
import struct

from . import model as M

HEADER = M.MSH_HEADER          # 68
# 0x18 — это ДУБЛЬ n_idx (проверено на всех мешах игры), поэтому он не «непрозрачный»:
# раньше он восстанавливался из шаблона и ломал чтение индексов у новых мешей.
OPAQUE_OFFSETS = (0x14, 0x1C, 0x20, 0x24)


DEFAULT_HEADERS = {
    32: bytes.fromhex("000000001400000020000000f70000004602000001000000460200000000000000000001000000000000000000000000000000000000000000000000000014000000"),
    36: bytes.fromhex("000000001500000024000000859a0000d6dd0000010000003003000001000000000100000021000000000000000000000000000000000000000000000000000015000000"),
}

DEFAULT_SCHEMES = {
    32: [(0, 2, 0, 12), (3, 8, 12, 4), (5, 8, 16, 4), (4, 1, 20, 8), (4, 9, 28, 4)],
    36: [(0, 2, 0, 12), (3, 8, 12, 4), (5, 8, 16, 4), (4, 1, 20, 8),
         (4, 9, 28, 4), (2, 5, 32, 4)],
}


def default_template(skinned=False):
    """Шаблон формата без донора: самый частый в корпусе (stride 32 / 36)."""
    stride = 36 if skinned else 32
    return {
        "hdr_raw": (DEFAULT_HEADERS[stride] + b"\0" * HEADER)[:HEADER],
        "fmt": 21 if skinned else 20,
        "stride": stride,
        "n_vtx": 0,
        "n_idx": 0,
        "vtx_off": HEADER,
        "elements": list(DEFAULT_SCHEMES[stride]),
        "opaque": {},
    }


def parse_bytes(data):
    """Разобрать меш целиком: заголовок, блок вершин, блок индексов."""
    if len(data) < HEADER:
        raise ValueError("файл меша меньше %d байт" % HEADER)
    info = M.parse_msh(data)
    info["hdr_raw"] = bytes(data[:HEADER])
    info["vtx_raw"] = bytes(data[info["vtx_off"]:info["idx_off"]])
    info["idx_end"] = info["idx_off"] + info["n_idx"] * 2
    info["idx_raw"] = bytes(data[info["idx_off"]:info["idx_end"]])
    info["tail_raw"] = bytes(data[info["idx_end"]:])       # выравнивание до 4 байт
    info["source_size"] = len(data)
    return info


def parse_file(path):
    with open(path, "rb") as fh:
        info = parse_bytes(fh.read())
    info["path"] = path
    return info


def opaque_fields(info):
    """Непонятные поля заголовка — как они лежат в исходнике."""
    h = info["hdr_raw"]
    return {off: struct.unpack_from("<I", h, off)[0] for off in OPAQUE_OFFSETS}


def header_bytes(info, opaque=None):
    """Собрать 68 байт заголовка: понятные поля пересчитываются, прочие — как были."""
    h = bytearray(info.get("hdr_raw") or bytes(HEADER))
    struct.pack_into("<I", h, 0x04, info["fmt"])
    struct.pack_into("<I", h, 0x08, info["stride"])
    struct.pack_into("<I", h, 0x0C, info["n_vtx"])
    struct.pack_into("<I", h, 0x10, info["n_idx"])
    struct.pack_into("<I", h, 0x18, info["n_idx"])          # дубль n_idx, не из шаблона
    if "material" in info:
        struct.pack_into("<I", h, 0x00, int(info["material"]))   # индекс материала в .mdf
    src = opaque if opaque is not None else opaque_fields(info)
    for off, val in src.items():
        struct.pack_into("<I", h, off, val)
    struct.pack_into("<I", h, 0x28, 0)      # 0x28..0x3F всегда нули
    h[0x2C:0x40] = b"\0" * 20
    struct.pack_into("<I", h, 0x40, info["fmt"])   # @0x40 всегда код формата
    return bytes(h)


def build(info, opaque=None):
    """Собрать файл меша из разобранного (или изменённого) описания."""
    hdr = header_bytes(info, opaque=opaque)
    vtx = info.get("vtx_raw") or b""
    idx = info.get("idx_raw") or b""
    need = info["n_vtx"] * info["stride"]
    if len(vtx) != need:
        raise ValueError("блок вершин %d байт, ожидалось %d (вершин %d, stride %d)"
                         % (len(vtx), need, info["n_vtx"], info["stride"]))
    if len(idx) != info["n_idx"] * 2:
        raise ValueError("блок индексов %d байт, ожидалось %d"
                         % (len(idx), info["n_idx"] * 2))
    tail = info.get("tail_raw") or b""
    pad = ((info["n_idx"] * 2 + 3) // 4) * 4 - info["n_idx"] * 2
    if len(tail) != pad:
        tail = b"\0" * pad
    return hdr + vtx + idx + tail


# ------------------------------------------------------- сборка блоков данных
def enc_vec(v, typ, w_byte=0, spare=b""):
    """
    Обратное к model.dec_vec: пишем только те компоненты, что хранит формат.

    `spare` — исходные байты этого же элемента. Компоненты, которых нет в
    декодере (например 4-й компонент SHORT4N/UBYTE4N), берутся из `spare`:
    в файлах они не всегда нулевые, а затирать их нельзя.
    """
    def keep(a, b):
        return bytes(a) + bytes(spare[len(a):len(a) + b])

    x = v[0] if len(v) > 0 else 0.0
    y = v[1] if len(v) > 1 else 0.0
    z = v[2] if len(v) > 2 else 0.0
    if typ == 2:
        return struct.pack("<3f", x, y, z)
    if typ == 3:
        return struct.pack("<4f", x, y, z, v[3] if len(v) > 3 else 0.0)
    if typ == 8:                                   # UBYTE4N
        b = bytes([max(0, min(255, int(round((c + 1.0) * 0.5 * 255)))) for c in (x, y, z)])
        if len(v) > 3:
            # у тангенса 4-й байт — знак бинормали
            return b + bytes([255 if v[3] > 0 else 0])
        return keep(b, 1)
    if typ == 7:                                   # SHORT4
        b = struct.pack("<3h", *[max(-32768, min(32767, int(round(c * 32767)))) for c in (x, y, z)])
        return keep(b, 2)
    if typ == 9:                                   # SHORT2N (z выводится при чтении)
        return struct.pack("<2h", int(round(x * 32767)), int(round(y * 32767)))
    if typ == 10:                                  # SHORT4N: 4-й компонент не наш
        b = struct.pack("<3h", *[max(-32768, min(32767, int(round((c + 1.0) * 0.5 * 32767))))
                                 for c in (x, y, z)])
        return keep(b, 2)
    if typ == 11:                                  # USHORT2N
        return struct.pack("<2H", int(round((x + 1.0) * 32767.5)), int(round((y + 1.0) * 32767.5)))
    if typ == 12:                                  # USHORT4N
        b = struct.pack("<3H", *[max(0, min(65535, int(round((c + 1.0) * 32767.5))))
                                 for c in (x, y, z)])
        return keep(b, 2)
    if typ in (13, 14):                            # UDEC3 / DEC3N — одно u32
        if typ == 14:
            comp = [max(-512, min(511, int(round(c * 511)))) & 0x3FF for c in (x, y, z)]
        else:
            comp = [max(0, min(1023, int(round((c + 1.0) * 0.5 * 1023)))) for c in (x, y, z)]
        return struct.pack("<I", (comp[0] << 22) | (comp[1] << 12) | (comp[2] << 2))
    if typ == 15:                                  # FLOAT16_2
        return struct.pack("<2e", x, y)
    if typ == 16:                                  # FLOAT16_4
        b = struct.pack("<3e", x, y, z)
        return keep(b, 2)
    if typ == 4:                                   # D3DCOLOR BGRA
        b = bytes([max(0, min(255, int(round((c + 1.0) * 0.5 * 255)))) for c in (z, y, x)])
        return keep(b, 1)
    if typ == 5:                                   # UBYTE4
        b = bytes([max(0, min(255, int(round(c * 255)))) for c in (x, y, z)])
        return keep(b, 1)
    if typ == 6:                                   # SHORT2
        return struct.pack("<2h", int(round(x * 32767)), int(round(y * 32767)))
    if typ == 0:                                   # FLOAT1
        return struct.pack("<f", x)
    if typ == 1:                                   # FLOAT2
        return struct.pack("<2f", x, y)
    raise ValueError("неизвестный тип элемента %d" % typ)


def enc_uv(uv, typ):
    """Обратное к model.dec_uv."""
    u = uv[0] if len(uv) > 0 else 0.0
    v = uv[1] if len(uv) > 1 else 0.0
    if typ == 1:
        return struct.pack("<2f", u, v)
    if typ == 6:
        return struct.pack("<2h", int(round(u * 32767)), int(round(v * 32767)))
    if typ == 9:
        return struct.pack("<2h", int(round(u * 32767)), int(round(v * 32767)))
    if typ == 11:
        return struct.pack("<2H", int(round(u * 65535)), int(round(v * 65535)))
    if typ == 15:
        return struct.pack("<2e", u, v)
    if typ == 5:
        return bytes([max(0, min(255, int(round(u * 255)))), max(0, min(255, int(round(v * 255))))])
    if typ == 0:
        return struct.pack("<f", u)
    if typ == 2:
        return struct.pack("<3f", u, v, 0.0)
    raise ValueError("неизвестный тип UV %d" % typ)


def rebuild_vertices(info, data=None, positions=None, normals=None, uvs=None,
                     uv_index=0, positions_only=False):
    """
    Пересобрать блок вершин.

    Без правок возвращает исходные байты; с правками — копирует исходную запись
    и заменяет только те элементы, которые заданы (остальные остаются как были,
    поэтому «неизменённые» атрибуты не портятся из-за потерь при декодировании).
    """
    raw = data if data is not None else open(info["path"], "rb").read()
    base = raw[info["vtx_off"]:info["idx_off"]]
    if positions is None and normals is None and uvs is None:
        return base
    stride, n = info["stride"], info["n_vtx"]
    out = bytearray(base)
    poff, ptyp = M._find_element(info, 0)
    noff, ntyp = M._find_element(info, 3)
    uoff, utyp = M._find_element(info, 4, uv_index)
    for i in range(n):
        o = i * stride
        if positions is not None and poff is not None:
            size = M.TYPE_SIZE[ptyp]
            src = base[o + poff:o + poff + size]
            enc = enc_vec(positions[i], ptyp, spare=src)
            out[o + poff:o + poff + len(enc)] = enc
        if normals is not None and noff is not None and ntyp not in (None, 4):
            size = M.TYPE_SIZE[ntyp]
            src = base[o + noff:o + noff + size]
            enc = enc_vec(normals[i], ntyp, spare=src)
            out[o + noff:o + noff + len(enc)] = enc
        if uvs is not None and uoff is not None:
            size = M.TYPE_SIZE[utyp]
            src = base[o + uoff:o + uoff + size]
            enc = enc_uv(uvs[i], utyp)
            out[o + uoff:o + uoff + len(enc)] = enc
    return bytes(out)


def set_vertices(info, block):
    """Подставить готовый блок вершин (число вершин берётся из длины блока)."""
    if len(block) % info["stride"]:
        raise ValueError("длина блока %d не кратна stride %d" % (len(block), info["stride"]))
    info["vtx_raw"] = bytes(block)
    info["n_vtx"] = len(block) // info["stride"]
    return info


def set_indices(info, indices):
    """Подставить индексы (u16). Соответственно обновляется счётчик и хвост."""
    info["idx_raw"] = struct.pack("<%dH" % len(indices), *indices)
    info["n_idx"] = len(indices)
    return info


def find_meshes(root):
    """Все `.mdl-msh*` в дереве каталогов (файлы мешей; `.mdl-hdr` не сюда)."""
    out = []
    if os.path.isfile(root):
        return [root]
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if ".mdl-msh" in f.lower():
                out.append(os.path.join(dp, f))
    return sorted(out)


def corpus_opaque(folder, limit=None):
    """
    Собрать значения непонятных полей по формату (для сборки меша «с нуля»).

    Возвращает {код формата: {смещение: значение}} — берётся самое частое
    сочетание для этого формата.
    """
    seen = {}
    n = 0
    for path in find_meshes(folder):
        try:
            info = parse_file(path)
        except Exception:
            continue
        key = info["fmt"]
        vals = tuple(opaque_fields(info)[off] for off in OPAQUE_OFFSETS)
        seen.setdefault(key, {})
        seen[key][vals] = seen[key].get(vals, 0) + 1
        n += 1
        if limit and n >= limit:
            break
    out = {}
    for fmt, counter in seen.items():
        best = max(counter.items(), key=lambda kv: kv[1])[0]
        out[fmt] = dict(zip(OPAQUE_OFFSETS, best))
    return out


# ------------------------------------------------- сборка «с нуля» (новая топология)

def build_block(info, positions, normals=None, uvs=None, bones=None, tangents=None):
    """Собрать блок вершин под формат `info` из готовых массивов.

    Число вершин берётся из len(positions) — топология может быть любой.
    `uvs` — список наборов: uvs[0][i] = (u, v), uvs[1][i] = ...;
    `bones` — индекс кости на вершину (u32) или None (жёсткая деталь).
    """
    import struct as _st
    stride = info["stride"]
    n = len(positions)
    out = bytearray(n * stride)
    els = info["elements"]
    uv_seen = 0
    uv_count = sum(1 for (u, _t, _o, _z) in els if u == 4)
    for i in range(n):
        base = i * stride
        uv_slot = 0
        for (usage, typ, off, size) in els:
            if usage == 0:
                enc = enc_vec(positions[i], typ)
            elif usage == 3:
                nrm = normals[i] if normals else (0.0, 0.0, 1.0)
                enc = enc_vec(nrm, typ)
            elif usage == 2:
                idx = int(bones[i]) if bones else 0
                enc = _st.pack("<I", idx & 0xFFFFFFFF)[:size]
            elif usage == 4:
                uu = (uvs[uv_slot][i] if uvs and uv_slot < len(uvs) else (0.0, 0.0))
                enc = enc_uv(uu, typ)
                uv_slot += 1
            elif usage == 5:
                tg = tangents[i] if tangents else (1.0, 0.0, 0.0)
                enc = enc_vec(tg, typ)
            else:
                enc = b"\x00" * size
            out[base + off:base + off + len(enc)] = enc
    return bytes(out)


def build_mesh(template, positions, indices, normals=None, uvs=None, bones=None,
               tangents=None):
    """Собрать .mdl-msh с новой топологией на основе формата шаблона."""
    info = dict(template)
    if indices and isinstance(indices[0], (tuple, list)):
        indices = [i for tri in indices for i in tri]
    info["n_vtx"] = len(positions)
    info["n_idx"] = len(indices)
    info["vtx_raw"] = build_block(info, positions, normals, uvs, bones, tangents)
    info["idx_raw"] = b"".join(struct.pack("<H", int(i) & 0xFFFF) for i in indices)
    info["tail_raw"] = b""
    return build(info)
