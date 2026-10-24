# -*- coding: utf-8 -*-
"""Small RGBA -> DXT1/DXT3/DXT5 encoder used by sc.edit.texture."""

from __future__ import annotations


def _565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _rgb(c):
    return (((c >> 11) & 31) * 255 // 31,
            ((c >> 5) & 63) * 255 // 63,
            (c & 31) * 255 // 31)


def _dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _color_block(pixels):
    rmin = gmin = bmin = 255
    rmax = gmax = bmax = 0
    for r, g, b, _a in pixels:
        rmin, rmax = min(rmin, r), max(rmax, r)
        gmin, gmax = min(gmin, g), max(gmax, g)
        bmin, bmax = min(bmin, b), max(bmax, b)
    c0 = _565(rmax, gmax, bmax)
    c1 = _565(rmin, gmin, bmin)
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        c1 = max(0, c1 - 1)
    p0, p1 = _rgb(c0), _rgb(c1)
    p2 = tuple((2 * p0[i] + p1[i]) // 3 for i in range(3))
    p3 = tuple((p0[i] + 2 * p1[i]) // 3 for i in range(3))
    palette = (p0, p1, p2, p3)
    bits = 0
    for i, (r, g, b, _a) in enumerate(pixels):
        best = min(range(4), key=lambda k: _dist((r, g, b), palette[k]))
        bits |= best << (2 * i)
    return c0, c1, bits


def _alpha_dxt5(pixels):
    alphas = [p[3] for p in pixels]
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        palette = [a0] * 8
    elif a0 > a1:
        palette = [a0, a1] + [( (7 - i) * a0 + (i + 1) * a1) // 8
                              for i in range(6)]
    else:
        palette = [a0, a1] + [((5 - i) * a0 + (i + 1) * a1) // 6
                              for i in range(4)] + [0, 255]
    bits = 0
    for i, a in enumerate(alphas):
        best = min(range(8), key=lambda k: (a - palette[k]) ** 2)
        bits |= best << (3 * i)
    return a0, a1, bits


def encode(rgba: bytes, w: int, h: int, fmt: str) -> bytes:
    """Encode RGBA rows into a DXT block stream (format DXT1/3/5)."""
    out = bytearray()
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            pixels = []
            for dy in range(4):
                y = min(h - 1, by + dy)
                for dx in range(4):
                    x = min(w - 1, bx + dx)
                    i = (y * w + x) * 4
                    pixels.append(tuple(rgba[i:i + 4]))
            c0, c1, bits = _color_block(pixels)
            color = (c0.to_bytes(2, "little") + c1.to_bytes(2, "little")
                     + bits.to_bytes(4, "little"))
            if fmt == "DXT1":
                out += color
            elif fmt == "DXT3":
                alpha = 0
                for i, p in enumerate(pixels):
                    alpha |= (p[3] >> 4) << (4 * i)
                out += alpha.to_bytes(8, "little") + color
            elif fmt == "DXT5":
                a0, a1, abits = _alpha_dxt5(pixels)
                out += (bytes((a0, a1)) + abits.to_bytes(6, "little") + color)
            else:
                raise ValueError(f"unsupported DXT format: {fmt}")
    return bytes(out)


def encode_uncompressed(rgba: bytes, w: int, h: int, fmt: str) -> bytes:
    out = bytearray()
    for i in range(w * h):
        r, g, b, a = rgba[i * 4:i * 4 + 4]
        if fmt in ("A8R8G8B8", "X8R8G8B8"):
            out += bytes((b, g, r, a if fmt == "A8R8G8B8" else 255))
        elif fmt == "R8G8B8":
            out += bytes((b, g, r))
        elif fmt == "A8L8":
            luma = (r * 299 + g * 587 + b * 114) // 1000
            out += bytes((luma, a))
        elif fmt in ("A8", "L8"):
            out.append(a if fmt == "A8" else (r * 299 + g * 587 + b * 114) // 1000)
        elif fmt in ("R5G6B5", "A1R5G5B5"):
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            if fmt == "A1R5G5B5":
                v |= (1 if a >= 128 else 0) << 15
            out += v.to_bytes(2, "little")
        elif fmt == "A4R4G4B4":
            v = ((a >> 4) << 12) | ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
            out += v.to_bytes(2, "little")
        else:
            raise ValueError(f"unsupported texture format: {fmt}")
    return bytes(out)
