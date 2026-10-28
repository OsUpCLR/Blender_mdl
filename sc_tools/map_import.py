# -*- coding: utf-8 -*-
"""Разбор геометрии и зон карт Star Conflict (`.mdl-geo`, `.mdl-zon`).

Модуль без bpy: только чтение файлов, чтобы его можно было гонять в тестах.

Формат `.mdl-geo` (загрузчик game.exe 0x14078a750):
    +0x00 u32 N        число чанков
    +0x04 u32 V        всего вершин (блок вершин V*16 байт)
    +0x08 8 байт       нули
    +0x10 N*32         записи чанков: fmt, ?, f2, f3, f4, f5, f6, f7
    0x10+32N           вершины: V * (3xf32 позиция + 2xu16 лайтмап-UV)
    далее u32 n1 + n1  extra: f2 записей по 20 байт (позиция + 4xu16)
    далее u32 n2 + n2  индексы u16 (n2 = 3*V*2)
    далее u32 n3 + n3  второй поток индексов (обычно 0)

Формат `.mdl-zon` (FUN_14082b530): заголовок, блоки D*4, E, F*16, G*16,
B*0x70 (узлы квадродерева), A*0x50, C*4, H*16 (дров-батчи).
"""
import os
import struct


def al16(x):
    return (x + 15) & ~15


# ---------------------------------------------------------------- .mdl-geo

class Geo(object):
    """Геометрия карты: чанки, вершины, индексы, extra."""

    def __init__(self, data):
        self.raw = data
        self.n_chunks, self.n_verts = struct.unpack_from("<II", data, 0)
        self.chunks = []
        for i in range(self.n_chunks):
            f = struct.unpack_from("<8I", data, 0x10 + 32 * i)
            self.chunks.append(dict(fmt=f[0], pad=f[1], n_extra=f[2], n_verts=f[3],
                                    idx_first=f[4], extra_off=f[5],
                                    vtx_first=f[6], flags=f[7]))
        self.vtx_off = 0x10 + 32 * self.n_chunks
        p = self.vtx_off + self.n_verts * 16
        self.n_extra, = struct.unpack_from("<I", data, p)
        self.extra_off = p + 4
        p += 4 + self.n_extra
        self.n_idx, = struct.unpack_from("<I", data, p)
        self.idx_off = p + 4
        p += 4 + self.n_idx
        self.n_idx2, = struct.unpack_from("<I", data, p)
        self.idx2_off = p + 4

    # --- доступ к данным
    def vertex(self, i):
        """(x, y, z, u, v) — UV в 0..1."""
        o = self.vtx_off + i * 16
        x, y, z = struct.unpack_from("<3f", self.raw, o)
        u, v = struct.unpack_from("<2H", self.raw, o + 12)
        return x, y, z, u / 65535.0, v / 65535.0

    def vertices(self, start, count):
        o = self.vtx_off + start * 16
        out = []
        for i in range(count):
            x, y, z = struct.unpack_from("<3f", self.raw, o + i * 16)
            u, v = struct.unpack_from("<2H", self.raw, o + i * 16 + 12)
            out.append((x, y, z, u / 65535.0, v / 65535.0))
        return out

    def indices(self, first, count):
        """count u16-индексов, начиная с first (в единицах u16)."""
        return struct.unpack_from("<%dH" % count, self.raw,
                                  self.idx_off + first * 2)

    def extra_records(self, chunk_index):
        ch = self.chunks[chunk_index]
        n = ch["n_extra"]
        o = self.extra_off + ch["extra_off"]
        out = []
        for i in range(n):
            x, y, z = struct.unpack_from("<3f", self.raw, o + i * 20)
            a, b, c, d = struct.unpack_from("<4H", self.raw, o + i * 20 + 12)
            out.append((x, y, z, a, b, c, d))
        return out

    # --- проверка целостности
    def check(self):
        """Возвращает список проблем (пусто — всё сходится)."""
        bad = []
        size = len(self.raw)
        expect = self.idx2_off + self.n_idx2
        if expect != size:
            bad.append("размер: ожидалось %d, файл %d" % (expect, size))
        total = sum(c["n_verts"] for c in self.chunks)
        if total != self.n_verts:
            bad.append("сумма вершин чанков %d != V %d" % (total, self.n_verts))
        if self.n_idx < 3 * self.n_verts * 2:
            bad.append("индексов %d < 3*V*2" % self.n_idx)
        elif self.n_idx - 3 * self.n_verts * 2 > 4:
            bad.append("индексов %d, ожидалось ~%d" % (self.n_idx, 3 * self.n_verts * 2))
        return bad


def read_geo(path):
    with open(path, "rb") as fh:
        return Geo(fh.read())


# --------------------------------------------------------------- .mdl-zon

class Zone(object):
    """Одна зона: заголовок, плоскости/точки, материалы-лайтмапы, дров-батчи."""

    def __init__(self, data):
        self.raw = data
        (self.index, self.a, self.b, self.c, self.d,
         self.e) = struct.unpack_from("<6I", data, 0x20)
        self.f, self.g = struct.unpack_from("<2H", data, 0x38)
        self.h, = struct.unpack_from("<I", data, 0x3C)
        self.name = data[0:0x20].split(b"\0")[0].decode("latin1")
        self.box = struct.unpack_from("<6f", data, 0x40)

        o = 0x60
        self.offsets = struct.unpack_from("<%dI" % self.d, data, o) if self.d else ()
        o += al16(self.d * 4)
        self.blob = data[o:o + self.e]
        o += al16(self.e)
        # F — плоскости, G — точки
        self.planes = [struct.unpack_from("<4f", data, o + 16 * i) for i in range(self.f)]
        o += self.f * 16
        self.points = [struct.unpack_from("<4f", data, o + 16 * i) for i in range(self.g)]
        o += self.g * 16
        self.nodes = [data[o + 0x70 * i:o + 0x70 * (i + 1)] for i in range(self.b)]
        o += self.b * 0x70
        self.inner = [data[o + 0x50 * i:o + 0x50 * (i + 1)] for i in range(self.a)]
        o += self.a * 0x50
        self.carr = struct.unpack_from("<%di" % self.c, data, o) if self.c else ()
        o += al16(self.c * 4)
        # дров-батчи
        self.batches = []
        for i in range(self.h):
            mat, ch, first, cnt, flags = struct.unpack_from("<HHIII", data, o + 16 * i)
            self.batches.append(dict(material=mat, chunk=ch, first=first,
                                     count=cnt, flags=flags))
        o += self.h * 16
        self.end = o

    def materials(self):
        """Имена (пути) материалов зоны из блоба."""
        out = []
        for off in self.offsets:
            s = self.blob[off:off + 0x20].split(b"\0")[0]
            out.append(s.decode("latin1"))
        return out

    def check(self):
        bad = []
        if self.end != len(self.raw):
            bad.append("размер: разобрано %d, файл %d" % (self.end, len(self.raw)))
        return bad


def read_zone(path):
    with open(path, "rb") as fh:
        return Zone(fh.read())


# ------------------------------------------------------------- каталог карты

def scan_folder(folder):
    """{base: {'geo': path|None, 'zones': [paths], 'hdr': path|None}}.

    База — имя файла до «.mdl-», поэтому mainmenu и mainmenu_e не смешиваются.
    """
    out = {}
    for f in sorted(os.listdir(folder)):
        low = f.lower()
        if ".mdl-" not in low:
            continue
        base = f.split(".mdl-")[0]
        if base.lower() != f.split(".mdl-")[0].lower():
            base = f.split(".mdl-")[0]
        rec = out.setdefault(base, {"geo": None, "zones": [], "hdr": None})
        if low.endswith(".mdl-geo"):
            rec["geo"] = os.path.join(folder, f)
        elif ".mdl-zon" in low:
            rec["zones"].append(os.path.join(folder, f))
        elif low.endswith(".mdl-hdr") and rec["hdr"] is None:
            rec["hdr"] = os.path.join(folder, f)
    for rec in out.values():
        rec["zones"].sort()
    return out


def map_bases(folder):
    """Базы, у которых есть и геометрия, и хотя бы одна зона."""
    return sorted(b for b, r in scan_folder(folder).items()
                  if r["geo"] and r["zones"])


def map_stats(folder, base=None):
    """Сводка по карте: зоны, батчи, вершины, треугольники, материалы."""
    scan = scan_folder(folder)
    if base is None:
        cands = [b for b, r in scan.items() if r["geo"] and r["zones"]]
        base = cands[0] if cands else ""
    rec = scan.get(base) or {"geo": None, "zones": [], "hdr": None}
    out = dict(folder=folder, base=base, geo=rec["geo"],
               hdr=rec["hdr"], zones=[])
    if not rec["geo"]:
        return out
    geo = read_geo(rec["geo"])
    out["chunks"] = geo.n_chunks
    out["verts"] = geo.n_verts
    out["problems"] = geo.check()
    for zp in rec["zones"]:
        z = read_zone(zp)
        tris = sum(b["count"] for b in z.batches)
        out["zones"].append(dict(path=zp, index=z.index, name=z.name,
                                 batches=len(z.batches), tris=tris,
                                 materials=z.materials(),
                                 problems=z.check()))
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m sc_tools.map_import <папка с картами>")
    print(map_stats(sys.argv[1]))
