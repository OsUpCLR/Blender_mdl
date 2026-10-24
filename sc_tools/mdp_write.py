# -*- coding: utf-8 -*-
"""`.mdp` (TCF MODEL_PHYS): разбор и запись.

Раскладка (v0x0C/0x0D; сверено с `_analysis/MDP_PHYSICS.md`):

    +0x00  char[16]  "TCF MODEL_PHYS"
    +0x10  u32  5
    +0x14  u32  1
    +0x18  u32  размер файла - 16
    +0x1C  u32  0
    +0x20  u32  версия 0x0C/0x0D
    +0x24  u32  число тел
    +0x28  u32  count2 (массив 0x84, обычно 0)
    +0x2C  u32  flag (0)
    +0x30  u32  dataOffset
    +0x34  u32  hash (копируется)
    +0x38  u32  0
    +0x3C  u32  0

    тела: N × 0x4C с 0x40 (v13) или 0x34 (v12):
        +0x00 f32×9 базис 3×3, +0x24 f32×3 позиция,
        +0x38 u32 материал, +0x3C u32 тип шейпа (6 — выпуклый, 7 — тримеш, 2 — коробка),
        +0x40 u32 размер шейпа, +0x44 u32 смещение шейпа.

    данные: 16 + dataOffset; шейп: data_base + 0x10 + смещение.
    Тип 6: +0x10 min, +0x20 max, +0x30 count, +0x50 count × vec4 (x, y, z, 0).
    Прочие типы сохраняются сырыми байтами.
"""
import struct

MAGIC = b"TCF MODEL_PHYS\x00\x00"
BODY = 0x4C
HDR = 0x50
TRI = 16


def body_offset(version):
    return 0x34 if version == 0x0C else 0x40


def shape_base(data_offset, shp_off):
    return 16 + data_offset + 0x10 + shp_off


def parse_mdp(data):
    """Разобрать .mdp. Тела хранят и вершины (тип 6), и сырой блок шейпа."""
    if not data.startswith(MAGIC):
        raise ValueError("не MODEL_PHYS")
    ver = struct.unpack_from("<I", data, 0x20)[0]
    if ver not in (0x0C, 0x0D):
        raise ValueError("поддерживаются версии 0x0C/0x0D, в файле 0x%X" % ver)
    bodies_n, count2, _flag, doff, hsh = struct.unpack_from("<5I", data, 0x24)
    bo = body_offset(ver)
    data_base = 16 + doff
    out = []
    for i in range(bodies_n):
        off = bo + i * BODY
        basis = struct.unpack_from("<9f", data, off)
        pos = struct.unpack_from("<3f", data, off + 0x24)
        kind = struct.unpack_from("<I", data, off + 0x38)[0]
        typ = struct.unpack_from("<I", data, off + 0x3C)[0]
        if typ == 2:
            # коробка: половинные размеры во float на +0x40/+0x44/+0x48
            hx, hy, hz = struct.unpack_from("<3f", data, off + 0x40)
            out.append(dict(basis=basis, pos=pos, kind=kind, typ=2, size=0,
                            offset=0, min=(-hx, -hy, -hz), max=(hx, hy, hz),
                            count=0, verts=[], tris=[], half=(hx, hy, hz), raw=b""))
            continue
        size = struct.unpack_from("<I", data, off + 0x40)[0]
        shp_off = struct.unpack_from("<I", data, off + 0x44)[0]
        sb = shape_base(doff, shp_off)
        raw = data[sb:sb + size]
        if len(raw) != size:
            raise ValueError("шейп тела %d за пределами файла" % i)
        mn = struct.unpack_from("<3f", raw, 0x10) if size >= 0x30 else (0.0, 0.0, 0.0)
        mx = struct.unpack_from("<3f", raw, 0x20) if size >= 0x30 else (0.0, 0.0, 0.0)
        cnt = struct.unpack_from("<I", raw, 0x30)[0] if size >= 0x34 else 0
        verts = []
        tris_idx = []
        if typ == 6:
            need = HDR + cnt * TRI
            if need > size:
                raise ValueError("тело %d: count %d не влезает в size %d" % (i, cnt, size))
            for k in range(cnt):
                verts.append(struct.unpack_from("<4f", raw, HDR + k * TRI))
        elif typ == 7:
            # тримеш: +0xD0 треугольников, +0xDC вершин, вершины с +0xF0 (vec4),
            # затем nt*3 u32 индексов (0xFFFFFFFD — restart)
            nv = struct.unpack_from("<I", raw, 0xDC)[0] if size >= 0xE0 else 0
            nt = struct.unpack_from("<I", raw, 0xD0)[0] if size >= 0xD4 else 0
            vstart = 0xF0
            if size >= vstart + nv * 16 + nt * 12:
                verts = [struct.unpack_from("<4f", raw, vstart + k * 16) for k in range(nv)]
                istart = vstart + nv * 16
                idx = struct.unpack_from("<%dI" % (nt * 3), raw, istart)
                tris_idx = [tuple(idx[k:k + 3]) for k in range(0, len(idx) - 2, 3)]
                cnt = nt
                if nv:
                    mn = tuple(min(v[k] for v in verts) for k in range(3))
                    mx = tuple(max(v[k] for v in verts) for k in range(3))
        out.append(dict(basis=basis, pos=pos, kind=kind, typ=typ, size=size,
                        offset=shp_off, min=mn, max=mx, count=cnt,
                        verts=verts, tris=verts, tris_idx=tris_idx, raw=raw))
    tail = data[bo + bodies_n * BODY:data_base]
    gap = data[data_base:data_base + 0x10]      # таблица имён (#NO_BONE#, Metal, ...)
    end = data_base + 0x10
    for b in out:
        end = max(end, shape_base(doff, b["offset"]) + b["size"])
    return dict(version=ver, bodies=out, doff=doff, hash=hsh, count2=count2,
                tail=tail, gap=gap, trailer=data[end:end + 16], raw_file=data)


def _shape_bytes(b):
    """Блок шейпа: сырой, если не правили; иначе — из вершин (тип 6)."""
    raw = b.get("raw")
    if raw and not b.get("vert_edit"):
        return bytes(raw)
    if b.get("typ", 6) == 2:
        return b""                     # коробка: shape-данных нет
    if b.get("typ", 6) != 6:
        if raw:
            return bytes(raw)
        raise ValueError("тело типа %r без сырых данных не собирается" % b.get("typ"))
    verts = b.get("verts") or b.get("tris") or []
    mn = b.get("min") or (0.0, 0.0, 0.0)
    mx = b.get("max") or (0.0, 0.0, 0.0)
    blk = bytearray(HDR)
    struct.pack_into("<3f", blk, 0x10, *mn)
    struct.pack_into("<3f", blk, 0x20, *mx)
    struct.pack_into("<I", blk, 0x30, len(verts))
    for v in verts:
        blk += struct.pack("<4f", v[0], v[1], v[2], v[3] if len(v) > 3 else 0.0)
    return bytes(blk)


def build_mdp(bodies, hsh=0, trailer=None, doff=None, tail=None,
              version=0x0D, raw_file=None, gap=None):
    """Собрать .mdp. Если ничего не правили и есть исходник — вернуть его байты."""
    changed = any(b.get("vert_edit") or (not b.get("raw") and b.get("typ", 6) != 2)
                  for b in bodies)
    if raw_file is not None and not changed:
        return bytes(raw_file)
    blobs = [_shape_bytes(b) for b in bodies]
    bo = body_offset(version)
    n = len(bodies)
    if doff is None:
        data_base = bo + n * BODY + 0x10
        data_base += (-data_base) % 16
        doff = data_base - 16
    data_base = 16 + doff
    body_bytes = bytearray()
    cur = 0
    for b, blk in zip(bodies, blobs):
        rec = bytearray(BODY)
        struct.pack_into("<9f", rec, 0, *(b.get("basis") or (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)))
        struct.pack_into("<3f", rec, 0x24, *(b.get("pos") or (0.0, 0.0, 0.0)))
        struct.pack_into("<I", rec, 0x38, int(b.get("kind", 10)))
        typ2 = int(b.get("typ", 6))
        struct.pack_into("<I", rec, 0x3C, typ2)
        if typ2 == 2:
            half = b.get("half") or (
                (b.get("max") or (0, 0, 0))[0], (b.get("max") or (0, 0, 0))[1],
                (b.get("max") or (0, 0, 0))[2])
            struct.pack_into("<3f", rec, 0x40, *[abs(v) for v in half])
        else:
            struct.pack_into("<I", rec, 0x40, len(blk))
            struct.pack_into("<I", rec, 0x44, cur)
            b["offset"] = cur
        body_bytes += rec
        cur += len(blk)
    head = bytearray(data_base)
    head[0:16] = MAGIC
    struct.pack_into("<2I", head, 0x10, 5, 1)
    struct.pack_into("<I", head, 0x20, version)
    struct.pack_into("<5I", head, 0x24, n, 0, 0, doff, hsh)
    head[bo:bo + len(body_bytes)] = body_bytes
    if tail:
        pos = bo + len(body_bytes)
        room = max(0, data_base - pos)
        head[pos:pos + min(room, len(tail))] = tail[:room]
    out = bytearray(head)
    g = bytes(gap or b"")[:16].ljust(16, b"\x00")   # имена не затираем
    out += g
    base = data_base + 0x10
    for b, blk in zip(bodies, blobs):
        off = shape_base(doff, b.get("offset", 0)) - base
        if off < 0:
            off = 0
        if len(out) < base + off + len(blk):
            out += b"\x00" * (base + off + len(blk) - len(out))
        out[base + off:base + off + len(blk)] = blk
    # таблица чанков: запись 16 Б (type=9, size, offset=0x20, 0)
    tab_off = len(out)
    out += struct.pack("<4I", 9, tab_off - 0x20, 0x20, 0)
    struct.pack_into("<I", out, 0x18, tab_off)
    return bytes(out)


def build_bvh_nodes(verts, tris):
    """BVH типа 7: префиксный обход; AABB узла — по вершинам поддерева."""
    tris = [tuple(t) for t in tris]
    cent = [[sum(verts[i][k] for i in t) / 3.0 for k in range(3)] for t in tris]
    out = []

    def rec(ids):
        start = len(out)
        if len(ids) == 1:
            t = tris[ids[0]]
            mn = [min(verts[i][k] for i in t) for k in range(3)]
            mx = [max(verts[i][k] for i in t) for k in range(3)]
            out.append([mn, mx, ids[0]])
            return mn, mx
        cmn = [min(cent[i][k] for i in ids) for k in range(3)]
        cmx = [max(cent[i][k] for i in ids) for k in range(3)]
        axis = max(range(3), key=lambda k: cmx[k] - cmn[k])
        ids2 = sorted(ids, key=lambda i: cent[i][axis])
        half = len(ids2) // 2
        out.append([None, None, None])
        n1, x1 = rec(ids2[:half])
        n2, x2 = rec(ids2[half:])
        end = len(out)
        out[start][0] = [min(n1[k], n2[k]) for k in range(3)]
        out[start][1] = [max(x1[k], x2[k]) for k in range(3)]
        out[start][2] = -end
        return out[start][0], out[start][1]

    rec(list(range(len(tris))))
    return out


def pack_bvh(nodes, mn, mx, template_header=None, count2=0):
    """Хвост BVH: заголовок 0xC0 + узлы 16 Б + count2 записей по 32 Б."""
    rng = [max(1e-6, mx[k] - mn[k]) for k in range(3)]
    scale = [65535.0 / rng[k] for k in range(3)]
    hdr = bytearray(0xC0)
    if template_header is not None:
        hdr[:] = bytes(template_header[:0xC0])
    struct.pack_into("<3f", hdr, 0x10, *mn)
    struct.pack_into("<3f", hdr, 0x20, *mx)
    struct.pack_into("<3f", hdr, 0x30, *scale)
    struct.pack_into("<I", hdr, 0x40, 282)
    struct.pack_into("<I", hdr, 0x44, len(nodes))
    hdr[0x48] = 1
    struct.pack_into("<I", hdr, 0xB4, count2)
    out = bytearray(hdr)
    for nmn, nmx, link in nodes:
        q = []
        for k in range(3):
            a = int(round((nmn[k] - mn[k]) * scale[k])) - 1
            b = int(round((nmx[k] - mn[k]) * scale[k])) + 1
            q.append(max(0, min(65535, a)))
            q.append(max(0, min(65535, b)))
        out += struct.pack("<6Hi", q[0], q[2], q[4], q[1], q[3], q[5], int(link))
    out += b"\x00" * (32 * count2)
    return bytes(out)


def decimate_tris(verts, tris, max_tris=512, target_cells=None):
    """Децимация без сварки: по одному крупному треугольнику на ячейку сетки,
    остаток — крупнейшие по площади. Геометрия сохраняется точнее."""
    tris = [tuple(t) for t in tris]
    if len(tris) <= max_tris:
        return list(verts), tris
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    if target_cells is None:
        target_cells = max(2, int(round(max_tris ** (1.0 / 3.0))) * 2)
    span = [max(1e-6, hi[k] - lo[k]) for k in range(3)]

    def area2(f):
        ax = verts[f[1]][0]-verts[f[0]][0]; ay = verts[f[1]][1]-verts[f[0]][1]; az = verts[f[1]][2]-verts[f[0]][2]
        bx = verts[f[2]][0]-verts[f[0]][0]; by = verts[f[2]][1]-verts[f[0]][1]; bz = verts[f[2]][2]-verts[f[0]][2]
        cx = ay*bz-az*by; cy = az*bx-ax*bz; cz = ax*by-ay*bx
        return cx*cx+cy*cy+cz*cz

    ordered = sorted(tris, key=area2, reverse=True)
    keep, buckets = [], set()
    for f in ordered:
        c = [(verts[f[0]][k]+verts[f[1]][k]+verts[f[2]][k])/3.0 for k in range(3)]
        key = tuple(int((c[k]-lo[k]) / span[k] * target_cells) for k in range(3))
        if key in buckets:
            continue
        buckets.add(key)
        keep.append(f)
        if len(keep) >= max_tris:
            return list(verts), keep
    used = set(keep)
    for f in ordered:
        if f in used:
            continue
        keep.append(f)
        if len(keep) >= max_tris:
            break
    return list(verts), keep


def build_bvh_nodes(verts, tris):
    """BVH типа 7: префиксный обход; AABB узла — по вершинам поддерева."""
    tris = [tuple(t) for t in tris]
    cent = [[sum(verts[i][k] for i in t) / 3.0 for k in range(3)] for t in tris]
    out = []

    def rec(ids):
        start = len(out)
        if len(ids) == 1:
            t = tris[ids[0]]
            mn = [min(verts[i][k] for i in t) for k in range(3)]
            mx = [max(verts[i][k] for i in t) for k in range(3)]
            out.append([mn, mx, ids[0]])
            return mn, mx
        cmn = [min(cent[i][k] for i in ids) for k in range(3)]
        cmx = [max(cent[i][k] for i in ids) for k in range(3)]
        axis = max(range(3), key=lambda k: cmx[k] - cmn[k])
        ids2 = sorted(ids, key=lambda i: cent[i][axis])
        half = len(ids2) // 2
        out.append([None, None, None])
        n1, x1 = rec(ids2[:half])
        n2, x2 = rec(ids2[half:])
        end = len(out)
        out[start][0] = [min(n1[k], n2[k]) for k in range(3)]
        out[start][1] = [max(x1[k], x2[k]) for k in range(3)]
        out[start][2] = -end
        return out[start][0], out[start][1]

    rec(list(range(len(tris))))
    return out


def pack_bvh(nodes, mn, mx, template_header=None, count2=0):
    """Хвост BVH: заголовок 0xC0 + узлы 16 Б + count2 записей по 32 Б."""
    rng = [max(1e-6, mx[k] - mn[k]) for k in range(3)]
    scale = [65535.0 / rng[k] for k in range(3)]
    hdr = bytearray(0xC0)
    if template_header is not None:
        hdr[:] = bytes(template_header[:0xC0])
    struct.pack_into("<3f", hdr, 0x10, *mn)
    struct.pack_into("<3f", hdr, 0x20, *mx)
    struct.pack_into("<3f", hdr, 0x30, *scale)
    struct.pack_into("<I", hdr, 0x40, 282)
    struct.pack_into("<I", hdr, 0x44, len(nodes))
    hdr[0x48] = 1
    struct.pack_into("<I", hdr, 0xB4, count2)
    out = bytearray(hdr)
    for nmn, nmx, link in nodes:
        q = []
        for k in range(3):
            a = int(round((nmn[k] - mn[k]) * scale[k])) - 1
            b = int(round((nmx[k] - mn[k]) * scale[k])) + 1
            q.append(max(0, min(65535, a)))
            q.append(max(0, min(65535, b)))
        out += struct.pack("<6Hi", q[0], q[2], q[4], q[1], q[3], q[5], int(link))
    out += b"\x00" * (32 * count2)
    return bytes(out)


def build_bvh_nodes(verts, tris):
    """BVH типа 7: префиксный обход; AABB узла — по вершинам поддерева."""
    tris = [tuple(t) for t in tris]
    cent = [[sum(verts[i][k] for i in t) / 3.0 for k in range(3)] for t in tris]
    out = []

    def rec(ids):
        start = len(out)
        if len(ids) == 1:
            t = tris[ids[0]]
            mn = [min(verts[i][k] for i in t) for k in range(3)]
            mx = [max(verts[i][k] for i in t) for k in range(3)]
            out.append([mn, mx, ids[0]])
            return mn, mx
        cmn = [min(cent[i][k] for i in ids) for k in range(3)]
        cmx = [max(cent[i][k] for i in ids) for k in range(3)]
        axis = max(range(3), key=lambda k: cmx[k] - cmn[k])
        ids2 = sorted(ids, key=lambda i: cent[i][axis])
        half = len(ids2) // 2
        out.append([None, None, None])
        n1, x1 = rec(ids2[:half])
        n2, x2 = rec(ids2[half:])
        end = len(out)
        out[start][0] = [min(n1[k], n2[k]) for k in range(3)]
        out[start][1] = [max(x1[k], x2[k]) for k in range(3)]
        out[start][2] = -end
        return out[start][0], out[start][1]

    rec(list(range(len(tris))))
    return out


def pack_bvh(nodes, mn, mx, template_header=None, count2=0):
    """Хвост BVH: заголовок 0xC0 + узлы 16 Б + count2 записей по 32 Б."""
    rng = [max(1e-6, mx[k] - mn[k]) for k in range(3)]
    scale = [65535.0 / rng[k] for k in range(3)]
    hdr = bytearray(0xC0)
    if template_header is not None:
        hdr[:] = bytes(template_header[:0xC0])
    struct.pack_into("<3f", hdr, 0x10, *mn)
    struct.pack_into("<3f", hdr, 0x20, *mx)
    struct.pack_into("<3f", hdr, 0x30, *scale)
    struct.pack_into("<I", hdr, 0x40, 282)
    struct.pack_into("<I", hdr, 0x44, len(nodes))
    hdr[0x48] = 1
    struct.pack_into("<I", hdr, 0xB4, count2)
    out = bytearray(hdr)
    for nmn, nmx, link in nodes:
        q = []
        for k in range(3):
            a = int(round((nmn[k] - mn[k]) * scale[k])) - 1
            b = int(round((nmx[k] - mn[k]) * scale[k])) + 1
            q.append(max(0, min(65535, a)))
            q.append(max(0, min(65535, b)))
        out += struct.pack("<6Hi", q[0], q[2], q[4], q[1], q[3], q[5], int(link))
    out += b"\x00" * (32 * count2)
    return bytes(out)


def build_trimesh(mn, mx, verts, tris, template=None, bvh_header=None,
                  basis=None, pos=None, kind=10, max_tris=512):
    """Собрать шейп типа 7: заголовок 0xF0, вершины vec4, индексы u32, BVH.

    template — байты заголовка шейпа (0xF0) от донора; bvh_header — первые
    0xC0 байт BVH-заголовка донора (служебные поля движка).
    """
    verts, tris = decimate_tris(verts, tris, max_tris=max_tris)
    if bvh_header is not None and len(bvh_header) > 0x48 and bvh_header[0x48] != 1:
        raise ValueError("шаблон BVH с 64-байтовыми узлами (флаг 0) не поддерживается")
    nv, nt = len(verts), len(tris)
    hdr = bytearray(0xF0)
    if template is not None:
        hdr[:] = bytes(template[:0xF0])
    struct.pack_into("<3f", hdr, 0x40, *mn)
    struct.pack_into("<3f", hdr, 0x50, *mx)
    struct.pack_into("<I", hdr, 0x84, 1)
    struct.pack_into("<I", hdr, 0xB0, 0x101)
    struct.pack_into("<f", hdr, 0xB4, 0.01)
    struct.pack_into("<I", hdr, 0xD0, nt)
    struct.pack_into("<I", hdr, 0xD8, 12)
    struct.pack_into("<I", hdr, 0xDC, nv)
    struct.pack_into("<I", hdr, 0xE0, 0)
    struct.pack_into("<I", hdr, 0xE4, 16)
    struct.pack_into("<I", hdr, 0xE8, 2)
    blk = bytearray(hdr)
    for v in verts:
        blk += struct.pack("<4f", v[0], v[1], v[2], 0.0)
    for t in tris:
        blk += struct.pack("<3I", int(t[0]), int(t[1]), int(t[2]))
    pad = ((-(3 * nt)) & 3) * 4          # выравнивание перед BVH, как в движке
    blk += b"\x00" * pad
    nodes = build_bvh_nodes(verts, tris)
    blk += pack_bvh(nodes, mn, mx, template_header=bvh_header, count2=2)
    raw = bytes(blk)
    return dict(min=mn, max=mx, typ=7, basis=basis, pos=pos, kind=kind,
                verts=[(v[0], v[1], v[2], 0.0) for v in verts],
                tris_idx=[tuple(t) for t in tris], count=nt, size=len(raw), raw=raw)


def build_one_body(min3, max3, verts, hsh=0, name=None):
    return build_mdp([dict(min=min3, max=max3, verts=verts, typ=6)], hsh=hsh)


def build_from_mesh(min3, max3, triangles, hsh=0, trailer=None, name=None):
    """Собрать .mdp из треугольников: уникальные ВЕРШИНЫ (vec4), тип 6."""
    seen = set()
    verts = []
    for tri in triangles:
        for v in tri:
            key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
            if key in seen:
                continue
            seen.add(key)
            verts.append((v[0], v[1], v[2], 0.0))
    return build_mdp([dict(min=min3, max=max3, verts=verts, typ=6)], hsh=hsh,
                     trailer=trailer)


def triangle_plane(v0, v1, v2):
    """Устарело: в файле вершины, а не плоскости (оставлено для справки)."""
    ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    nx = ay * bz - az * by
    ny = az * bx - ax * bz
    nz = ax * by - ay * bx
    ln = (nx * nx + ny * ny + nz * nz) ** 0.5
    if ln < 1e-9:
        return (0.0, 0.0, 0.0, 0.0)
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    d = nx * v0[0] + ny * v0[1] + nz * v0[2]
    return (nx * d, ny * d, nz * d, 0.0)
