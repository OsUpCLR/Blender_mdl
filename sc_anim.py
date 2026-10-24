#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_anim — чтение и запись .anim (Star Conflict, контейнер TCF, чанк "ANIM" v5).

РАСКЛАДКА УСТАНОВЛЕНА (декомпиляция FUN_140835f80 + проверка на всех 4063
файлах из the game .pak files: 4063/4063 совпали по размеру, 4063/4063 — по битовой
маске индексов костей, 745330/745330 кватернионов единичные).

    ФАЙЛ
    0x00  "TCF ANIM"
    0x08  8 байт нулей
    0x10  u32 version = 5
    0x14  u32 = 1 (всегда; у всех 4063 файлов)
    0x18  u32 смещение таблицы чанков (= размер файла - 16)
    0x1C  u32 = 0 (всегда)
    0x20  чанк "ANIM":
        +0x00  f32  t0        (у поз 100000, у drill 1/24, у ship 20.875)
        +0x04  f32  duration   (0 -> в движке FLT_MAX; у поз 0)
        +0x08  u32  count1     число описателей каналов (max 60)
        +0x0C  u32  count2     число вторичных записей 0x10 (у всех файлов 0)
        +0x10  u32  mask[4]    БИТОВАЯ МАСКА индексов костей, которые есть
                               в анимации (сверено с индексами описателей)
        +0x20  i32  unknown    (у поз -2 = корень; пишем как есть)
        +0x24  u32  unknown
        +0x28  u32  unknown
        +0x2C  u32  unknown
        +0x30  описатели: count1 по 0x30
               +0x00 char[32] имя кости (совпадает с .mdl-skl)
               +0x20 u32 index  номер кости в .mdl-skl
               +0x24 u32 count  число ключей
               +0x28 u32 type   0 -> 0x20 Б, 1 -> 0x10 Б, 2 -> 0x10 Б
               +0x2C u32 = 0
        затем count2 записей по 0x10 (в релизе не встречаются)
        затем массивы:
            A : countA * 0x20  (type 0)  vec4 позиция (xyz, 0) + vec4 кватернион
            B : countB * 0x10  (type 1)  vec4 позиция (xyz, 0)
            C : countC * 0x10  (type 2)  vec4 кватернион (xyzw)
            T1: align16((countA+countB+countC) * 2)   u16 времён, В ПОРЯДКЕ
                ОПИСАТЕЛЕЙ (каждому каналу count подряд идущих u16)
            T2: align16(S4 * 2)      S4 = сумма (entry+4)
            T3: align16(S8 * 2)      S8 = сумма (entry+8)
            D3: S8 * 0x10
            D2: align16(S4 * 4)
    0x18+таблица: 16 байт — (u32 type=2, u32 size, u32 offset=0x20, u32 0)

    Времена — u16 в долях [0, 0x8000]; у всех 4268 треков с >1 ключом
    последнее время РОВНО 0x8000. Секунды: t = u16 / 32768 * duration.

    Типы каналов (проверено статистикой по всем файлам):
        type 0 — позиция + поворот   (186370/186370 кватернионов единичные,
                                     4-й элемент позиции всегда 0)
        type 1 — только позиция      (4-й элемент всегда 0)
        type 2 — только поворот      (558960/558960 единичные)

    Имя и индекс в описателе СОВПАДАЮТ с .mdl-skl: у drill.mdl-skl кости
    0 root, 1 main, 2 drill_fx, 3 rot_y, 4 drill — а в drill_idle.anim
    каналы 'rot_y' (index 3) и 'drill' (index 4).

CLI:
    python sc_anim.py dump <файл|папка>      разобрать и напечатать
    python sc_anim.py verify <корень>        прогнать все .anim, сверить размер
    python sc_anim.py roundtrip <файл|папка> пересобрать и сверить побайтово
"""
import math
import os
import struct
import sys

MAGIC = b"TCF ANIM"
CHUNK_OFF = 0x20
CHUNK_HDR = 0x30
DESC_SIZE = 0x30
ENTRY_SIZE = 0x10
TYPE_SIZE = {0: 0x20, 1: 0x10, 2: 0x10}
TYPE_HAS_POS = {0: True, 1: True, 2: False}
TYPE_HAS_ROT = {0: True, 1: False, 2: True}
TIME_UNITS = 32768.0


def _align16(n):
    return (n + 15) & ~15


class Channel(object):
    """Один канал анимации = одна кость."""

    __slots__ = ("name", "index", "count", "type", "times", "pos", "quat")

    def __init__(self, name, index, count, type_, times, pos, quat):
        self.name = name
        self.index = index
        self.count = count
        self.type = type_
        self.times = times          # список u16
        self.pos = pos              # список (x, y, z) или []
        self.quat = quat            # список (x, y, z, w) или []

    def seconds(self, duration):
        """Времена ключей в секундах."""
        if duration <= 0.0 or duration != duration:
            return [0.0] * len(self.times)
        return [t * duration / TIME_UNITS for t in self.times]

    def __repr__(self):
        return "<Channel %r idx=%d type=%d keys=%d>" % (
            self.name, self.index, self.type, self.count)


class Anim(object):
    """Разобранный .anim."""

    def __init__(self):
        self.version = 5
        self.t0 = 0.0
        self.duration = 0.0
        self.mask = [0, 0, 0, 0]
        self.unknown = [0, 0, 0, 0]     # c+0x20 .. c+0x2C
        self.channels = []
        self.entries = []               # вторичные записи (count2), обычно пусто
        self.path = None
        self.raw = None
        self._t2 = b""
        self._t3 = b""
        self._d3 = b""
        self._d2 = b""

    # --- удобства ---
    def channel_by_index(self, index):
        for ch in self.channels:
            if ch.index == index:
                return ch
        return None

    def channel_by_name(self, name):
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def seconds(self, ch):
        return ch.seconds(self.duration)

    def __repr__(self):
        return "<Anim %s ch=%d dur=%.4f>" % (
            os.path.basename(self.path or "?"), len(self.channels), self.duration)


# ---------------------------------------------------------------- чтение
def read_anim(source):
    """Разобрать .anim. source — путь или bytes. Возвращает Anim."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        path = None
    else:
        path = source
        with open(source, "rb") as fh:
            data = fh.read()
    if data[:8] != MAGIC:
        raise ValueError("не TCF ANIM: %r" % data[:8])
    if len(data) < CHUNK_OFF + CHUNK_HDR + 0x10:
        raise ValueError("файл слишком короткий")

    tbl_off = struct.unpack_from("<I", data, 0x18)[0]
    if tbl_off + 0x10 > len(data):
        raise ValueError("таблица чанков за пределами файла")
    # ищем чанк типа 2
    off = size = None
    i = 0
    while tbl_off + (i + 1) * 0x10 <= len(data):
        typ, sz, of, _pad = struct.unpack_from("<4I", data, tbl_off + i * 0x10)
        if typ == 2:
            off, size = of, sz
            break
        i += 1
    if off is None:
        raise ValueError("нет чанка типа 2")
    if off + size > len(data):
        raise ValueError("чанк за пределами файла")
    c = data[off:off + size]

    a = Anim()
    a.path = path
    a.raw = data
    a.version = struct.unpack_from("<I", data, 0x10)[0]
    a.t0, a.duration = struct.unpack_from("<2f", c, 0x00)
    a.count1, a.count2 = struct.unpack_from("<2I", c, 0x08)
    a.mask = list(struct.unpack_from("<4I", c, 0x10))
    a.unknown = list(struct.unpack_from("<4i", c, 0x20))

    c1, c2 = a.count1, a.count2
    if a.duration != a.duration:        # NaN
        a.duration = 0.0
    need = CHUNK_HDR + c1 * DESC_SIZE + c2 * ENTRY_SIZE
    if need > len(c):
        raise ValueError("описатели/записи за пределами чанка")

    descs = []
    for i in range(c1):
        d = CHUNK_HDR + i * DESC_SIZE
        name = c[d:d + 32].split(b"\x00", 1)[0].decode("latin-1")
        index, count, typ, pad = struct.unpack_from("<4I", c, d + 0x20)
        if typ not in TYPE_SIZE:
            raise ValueError("неизвестный тип канала %d у %r" % (typ, name))
        descs.append((name, index, count, typ, pad))

    e0 = CHUNK_HDR + c1 * DESC_SIZE
    entries = [struct.unpack_from("<4I", c, e0 + i * ENTRY_SIZE)
               for i in range(c2)]

    cA = sum(d[2] for d in descs if d[3] == 0)
    cB = sum(d[2] for d in descs if d[3] == 1)
    cC = sum(d[2] for d in descs if d[3] == 2)
    S4 = sum(e[1] for e in entries)
    S8 = sum(e[2] for e in entries)

    data0 = e0 + c2 * ENTRY_SIZE
    total = (data0 + cA * 0x20 + cB * 0x10 + cC * 0x10
             + _align16((cA + cB + cC) * 2) + _align16(S4 * 2)
             + _align16(S8 * 2) + S8 * 0x10 + _align16(S4 * 4))
    if total != len(c):
        raise ValueError("размер не сходится: расчёт %d, чанк %d"
                         % (total, len(c)))

    p = data0
    arrA = c[p:p + cA * 0x20]; p += cA * 0x20
    arrB = c[p:p + cB * 0x10]; p += cB * 0x10
    arrC = c[p:p + cC * 0x10]; p += cC * 0x10
    t1 = c[p:p + (cA + cB + cC) * 2]; p += _align16((cA + cB + cC) * 2)
    a._t2 = c[p:p + S4 * 2]; p += _align16(S4 * 2)
    a._t3 = c[p:p + S8 * 2]; p += _align16(S8 * 2)
    a._d3 = c[p:p + S8 * 0x10]; p += S8 * 0x10
    a._d2 = c[p:p + S4 * 4]
    a.entries = [{"a": e[0], "b": e[1], "c": e[2], "d": e[3]} for e in entries]

    cursor = {"pos_A": 0, "rot_A": 0, "B": 0, "C": 0}
    toff = 0
    for (name, index, count, typ, _pad) in descs:
        times = list(struct.unpack_from("<%dH" % count, t1, toff)) if count else []
        toff += count * 2
        pos, quat = [], []
        if typ == 0:
            base = cursor["pos_A"]
            pos = [struct.unpack_from("<3f", arrA, base + k * 0x20)
                   for k in range(count)]
            quat = [struct.unpack_from("<4f", arrA, base + k * 0x20 + 0x10)
                    for k in range(count)]
            cursor["pos_A"] = cursor["rot_A"] = base + count * 0x20
        elif typ == 1:
            base = cursor["B"]
            pos = [struct.unpack_from("<3f", arrB, base + k * 0x10)
                   for k in range(count)]
            cursor["B"] = base + count * 0x10
        else:
            base = cursor["C"]
            quat = [struct.unpack_from("<4f", arrC, base + k * 0x10)
                    for k in range(count)]
            cursor["C"] = base + count * 0x10
        a.channels.append(Channel(name, index, count, typ, times, pos, quat))
    return a


# ---------------------------------------------------------------- запись
def write_anim(a):
    """Собрать .anim обратно в байты (побайтово совпадает с оригиналом)."""
    n = len(a.channels)
    c1 = n
    c2 = len(a.entries)
    body = bytearray()

    body += struct.pack("<2f2I", a.t0, a.duration, c1, c2)
    body += struct.pack("<4I", *a.mask)
    body += struct.pack("<4i", *a.unknown)
    assert len(body) == CHUNK_HDR

    for ch in a.channels:
        nm = ch.name.encode("latin-1")[:31]
        body += nm + b"\x00" * (32 - len(nm))
        body += struct.pack("<4I", ch.index, ch.count, ch.type, 0)
    for e in a.entries:
        body += struct.pack("<4I", e["a"], e["b"], e["c"], e["d"])

    arrA = bytearray()
    arrB = bytearray()
    arrC = bytearray()
    t1 = bytearray()
    for ch in a.channels:
        if ch.type == 0:
            for k in range(ch.count):
                x, y, z = ch.pos[k]
                arrA += struct.pack("<4f", x, y, z, 0.0)
                arrA += struct.pack("<4f", *ch.quat[k])
        elif ch.type == 1:
            for k in range(ch.count):
                x, y, z = ch.pos[k]
                arrB += struct.pack("<4f", x, y, z, 0.0)
        else:
            for k in range(ch.count):
                arrC += struct.pack("<4f", *ch.quat[k])
        t1 += struct.pack("<%dH" % len(ch.times), *ch.times)

    body += arrA + arrB + arrC
    body += t1 + b"\x00" * (_align16(len(t1)) - len(t1))
    body += a._t2 + b"\x00" * (_align16(len(a._t2)) - len(a._t2))
    body += a._t3 + b"\x00" * (_align16(len(a._t3)) - len(a._t3))
    body += a._d3
    body += a._d2 + b"\x00" * (_align16(len(a._d2)) - len(a._d2))

    chunk = bytes(body)
    size = len(chunk)
    tbl_off = CHUNK_OFF + size
    head = bytearray(MAGIC)
    head += b"\x00" * 8
    head += struct.pack("<4I", a.version, 1, tbl_off, 0)
    assert len(head) == CHUNK_OFF
    out = bytes(head) + chunk
    out += struct.pack("<4I", 2, size, CHUNK_OFF, 0)
    return out


# ---------------------------------------------------------------- утилиты
def quat_to_axis_angle(q):
    """Кватернион (x, y, z, w) -> (ось, угол в градусах)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    w = max(-1.0, min(1.0, w))
    angle = 2.0 * math.degrees(math.acos(w))
    s = math.sqrt(max(0.0, 1.0 - w * w))
    if s < 1e-6:
        return (0.0, 0.0, 0.0), 0.0
    return (x / s, y / s, z / s), angle


def iter_anim_files(root):
    out = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".anim"):
                out.append(os.path.join(dp, f))
    out.sort()
    return out


# ---------------------------------------------------------------- CLI
def cmd_dump(path):
    files = iter_anim_files(path) if os.path.isdir(path) else [path]
    for p in files:
        try:
            a = read_anim(p)
        except Exception as exc:
            print("=== %s — ошибка: %s" % (p, exc))
            continue
        print("=== %s  (%d Б)" % (os.path.basename(p), len(a.raw)))
        print("    v%d  t0=%.4f  duration=%.4f  каналов=%d  mask=%s  unknown=%s"
              % (a.version, a.t0, a.duration, len(a.channels),
                 " ".join("%08X" % w for w in a.mask), a.unknown))
        for ch in a.channels:
            extra = []
            if ch.quat:
                axis, ang = quat_to_axis_angle(ch.quat[0])
                extra.append("q0=({:.4f},{:.4f},{:.4f},{:.4f}) {:.1f}° вокруг {}"
                             .format(*ch.quat[0], ang,
                                     tuple(round(v, 2) for v in axis)))
            if ch.pos:
                extra.append("p0=({:.4f},{:.4f},{:.4f})".format(*ch.pos[0]))
            print("    [%2d] %-16r тип %d ключей %-4d t[:3]=%s  %s"
                  % (ch.index, ch.name, ch.type, ch.count,
                     ch.times[:3], " ".join(extra)))


def cmd_verify(root):
    files = iter_anim_files(root)
    ok = bad = 0
    reasons = {}
    for p in files:
        try:
            read_anim(p)
            ok += 1
        except Exception as exc:
            bad += 1
            reasons.setdefault(str(exc), []).append(p)
    print("файлов: %d, разобрано: %d, ошибок: %d" % (len(files), ok, bad))
    for k, v in reasons.items():
        print("   %-60s %d  напр. %s" % (k, len(v), v[0]))
    return 0 if bad == 0 else 1


def cmd_roundtrip(path):
    files = iter_anim_files(path) if os.path.isdir(path) else [path]
    ok = 0
    bad = []
    for p in files:
        try:
            a = read_anim(p)
            out = write_anim(a)
        except Exception as exc:
            bad.append((p, str(exc)))
            continue
        if out == a.raw:
            ok += 1
        else:
            diff = next((i for i in range(min(len(out), len(a.raw)))
                         if out[i] != a.raw[i]), min(len(out), len(a.raw)))
            bad.append((p, "различие @%#x (собрано %d Б, было %d Б)"
                        % (diff, len(out), len(a.raw))))
    print("побайтово совпало: %d / %d" % (ok, len(files)))
    for p, why in bad[:20]:
        print("   %s — %s" % (p, why))
    return 0 if not bad else 1


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "dump":
        cmd_dump(path)
        return 0
    if cmd == "verify":
        return cmd_verify(path)
    if cmd == "roundtrip":
        return cmd_roundtrip(path)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
