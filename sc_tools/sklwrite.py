# -*- coding: utf-8 -*-
"""Запись `.mdl-skl` (скелет Star Conflict).

Структура (см. `skeleton.py`):

    заголовок 32 Б:  u32 count | u32 flags | u64 0 | u32 1 | 12 Б нулей
    кость 224 Б:     32 Б имя | i32 parent | 28 Б нулей |
                     f32×3 позиция | f32 0 |
                     f32×4 кватернион (x, y, z, w) |
                     f32×12 матрица поворота (3×4) |
                     f32×16 матрица привязки (4×4, колонками, 3-я колонка — перенос) |
                     f32×4 обратный перенос

Два файла из 3837 имеют хвост 64 Б (габарит) — поддерживается параметром trailer.
"""
import struct

HEADER = 32
BONE = 224


def _raw(value):
    """Сырые байты: bytes или hex-строка из custom property."""
    if not value:
        return b""
    if isinstance(value, str):
        try:
            return bytes.fromhex(value)
        except ValueError:
            return b""
    return bytes(value)


def build_skl(skl, raw=None):
    """Собрать файл скелета.

    raw — исходные байты: тогда меняются только известные поля, а всё
    остальное (нули-выравнивание, неизвестные байты) сохраняется как было.
    Без raw файл строится с нуля (для новых скелетов).
    """
    bones = skl.get("bones") or []
    if raw is not None:
        out = bytearray(raw)
    else:
        out = bytearray(HEADER + BONE * len(bones))
    if raw is None:
        struct.pack_into("<IIQI", out, 0, len(bones),
                         int(skl.get("flags", 0x00010000)) & 0xFFFFFFFF, 0, 1)
        for i in range(20, 32):
            out[i] = 0
    else:
        # в исходнике меняем только счётчик и флаги, остальной заголовок не трогаем
        struct.pack_into("<II", out, 0, len(bones),
                         int(skl.get("flags", 0x00010000)) & 0xFFFFFFFF)

    for bi, b in enumerate(bones):
        o = HEADER + bi * BONE
        name = (b.get("name") or "").encode("latin1")[:31]
        pos = tuple(float(x) for x in (b.get("position") or (0.0, 0.0, 0.0)))[:3]
        quat = tuple(float(x) for x in (b.get("rotation") or (0.0, 0.0, 0.0, 1.0)))[:4]
        rot = tuple(float(x) for x in (b.get("rot_matrix") or ()))[:12]
        if len(rot) < 12:
            rot = rot + (0.0,) * (12 - len(rot))
        bind = tuple(float(x) for x in (b.get("bind_matrix") or ()))[:16]
        if len(bind) < 16:
            bind = bind + (0.0,) * (16 - len(bind))
        inv = tuple(float(x) for x in (b.get("inverse_translation") or (0.0, 0.0, 0.0)))[:3]

        out[o:o + 32] = name + b"\x00" * (32 - len(name))
        struct.pack_into("<i", out, o + 32, int(b.get("parent", -1)))
        pad = _raw(b.get("pad_raw"))
        if pad and len(pad) == 28:
            out[o + 36:o + 64] = pad
        pos_raw = _raw(b.get("position_raw"))
        if pos_raw and len(pos_raw) == 12:
            out[o + 64:o + 76] = pos_raw
        else:
            struct.pack_into("<3f", out, o + 64, *pos)
        quat_raw = _raw(b.get("quat_raw"))
        if quat_raw and len(quat_raw) == 16:
            out[o + 80:o + 96] = quat_raw
        else:
            struct.pack_into("<4f", out, o + 80, *quat)
        struct.pack_into("<12f", out, o + 96, *rot)
        struct.pack_into("<16f", out, o + 144, *bind)
        struct.pack_into("<4f", out, o + 208, inv[0], inv[1], inv[2], 1.0)

    tr = skl.get("trailer")
    if tr:
        need = HEADER + BONE * len(bones)
        if len(out) < need + len(tr):
            out += b"\x00" * (need + len(tr) - len(out))
        out[need:need + len(tr)] = bytes(tr)
    return bytes(out)


def _mat3_from_quat(q):
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    )


def make_bone(name, parent, position, quat):
    """Кость «с нуля»: матрицы поворота и привязки считаются из позиции и кватерниона.

    Привязка — обратная к позе покоя: R^T и -R^T·pos; записывается колонками,
    как в файлах игры (последняя колонка — перенос).
    """
    q = tuple(float(x) for x in quat)[:4]
    n = (q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2) ** 0.5 or 1.0
    q = tuple(c / n for c in q)
    m = _mat3_from_quat(q)
    rt = tuple(m[r][c] for c in range(3) for r in range(3))     # R^T по строкам
    px, py, pz = position
    tx = -(rt[0] * px + rt[1] * py + rt[2] * pz)
    ty = -(rt[3] * px + rt[4] * py + rt[5] * pz)
    tz = -(rt[6] * px + rt[7] * py + rt[8] * pz)
    # rot_matrix: R по строкам, 4-й столбец 0
    rot = (
        m[0][0], m[0][1], m[0][2], 0.0,
        m[1][0], m[1][1], m[1][2], 0.0,
        m[2][0], m[2][1], m[2][2], 0.0,
    )
    # bind: R^T по строкам, перенос в 4-м столбце (как в файлах игры)
    bind = (
        rt[0], rt[1], rt[2], tx,
        rt[3], rt[4], rt[5], ty,
        rt[6], rt[7], rt[8], tz,
        0.0, 0.0, 0.0, 1.0,
    )
    return {
        "name": name,
        "parent": int(parent),
        "position": (float(px), float(py), float(pz)),
        "rotation": q,
        "rot_matrix": rot,
        "bind_matrix": bind,
        "inverse_translation": (tx, ty, tz),
    }
