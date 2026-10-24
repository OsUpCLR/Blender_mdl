#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запекание значений вершин в текстуру по UV — нужно, чтобы донести до Blender то,
что в OBJ не уезжает.

Зачем: `_pdo` (LightmapSampler) сэмплится **вторым** набором UV, а OBJ несёт
только один. Поэтому AO нельзя подключить отдельной картой — его надо запечь
в альбедо по первому набору UV. В шейдере AO берётся из красного канала:
`object.fx:614-629` -> `pdoTex = tex2D(LightmapSampler, pdoUv)`,
`globalOcclusion = pdoTex.x; // use cosine weighted AO`.

Модуль не зависит от Blender: только numpy + Pillow, как остальной SDK.

    from sc_tools import bake
    ao = bake.sample_scalar("..._pdo.dds", uvs2, channel=0)      # AO по вершинам
    vmap, cov = bake.rasterize_uv(indices, uvs1, ao, 2048)       # в пространство UV1
    bake.multiply_texture("..._d.png", vmap, cov, "..._d_ao.png")
"""
import os

import numpy as np
from PIL import Image


def sample_scalar(path, uvs, channel=0, wrap=True):
    """Значения текстуры по списку UV (ближайший пиксель).

    uvs — список/массив (u, v); channel — индекс канала (0=R, 1=G, ...).
    Возвращает массив float32 в 0..1, по одному значению на UV.
    """
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im, np.float32) / 255.0
    h, w = a.shape[:2]
    uv = np.asarray(uvs, np.float32)
    u = uv[:, 0] % 1.0 if wrap else np.clip(uv[:, 0], 0.0, 1.0)
    v = uv[:, 1] % 1.0 if wrap else np.clip(uv[:, 1], 0.0, 1.0)
    px = np.clip((u * (w - 1)).round().astype(np.int64), 0, w - 1)
    py = np.clip(((1.0 - v) * (h - 1)).round().astype(np.int64), 0, h - 1)
    return a[py, px, channel]


def rasterize_uv(indices, uvs, values, size, wrap=True):
    """Растеризовать значения вершин в текстуру size×size по UV.

    indices — треугольники (плоский список), uvs — UV на вершину,
    values — значение на вершину (например AO).
    Возвращает (карта значений, маска покрытия); вне покрытия значение 0.
    """
    vmap = np.zeros((size, size), np.float32)
    cov = np.zeros((size, size), np.float32)
    uv = np.asarray(uvs, np.float32)
    val = np.asarray(values, np.float32)
    u = uv[:, 0] % 1.0 if wrap else uv[:, 0]
    v = uv[:, 1] % 1.0 if wrap else uv[:, 1]
    n_tri = len(indices) // 3
    for t in range(n_tri):
        i0, i1, i2 = indices[3 * t], indices[3 * t + 1], indices[3 * t + 2]
        u0, u1, u2 = u[i0], u[i1], u[i2]
        v0, v1, v2 = v[i0], v[i1], v[i2]
        a0, a1, a2 = val[i0], val[i1], val[i2]
        # треугольник у края развёртки нужно повторить со сдвигом
        shifts = [(0.0, 0.0)]
        if (uv[i0, 0] // 1.0 != uv[i1, 0] // 1.0 or uv[i1, 0] // 1.0 != uv[i2, 0] // 1.0
                or uv[i0, 1] // 1.0 != uv[i1, 1] // 1.0 or uv[i1, 1] // 1.0 != uv[i2, 1] // 1.0):
            shifts = [(dx, dy) for dx in (-1.0, 0.0, 1.0) for dy in (-1.0, 0.0, 1.0)]
        for dx, dy in shifts:
            x0, x1, x2 = (u0 + dx) * size - 0.5, (u1 + dx) * size - 0.5, (u2 + dx) * size - 0.5
            y0, y1, y2 = (v0 + dy) * size - 0.5, (v1 + dy) * size - 0.5, (v2 + dy) * size - 0.5
            # Числители и знаменатель барицентрики — в одних единицах (тексели).
            denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(denom) < 1e-9:
                continue
            xmin = max(0, int(np.floor(min(x0, x1, x2))))
            xmax = min(size - 1, int(np.ceil(max(x0, x1, x2))))
            ymin = max(0, int(np.floor(min(y0, y1, y2))))
            ymax = min(size - 1, int(np.ceil(max(y0, y1, y2))))
            if xmax < xmin or ymax < ymin:
                continue
            xs = np.arange(xmin, xmax + 1, dtype=np.float32) + 0.5
            ys = np.arange(ymin, ymax + 1, dtype=np.float32) + 0.5
            gx, gy = np.meshgrid(xs, ys)
            w0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / denom
            w1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / denom
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
            if not inside.any():
                continue
            interp = w0 * a0 + w1 * a1 + w2 * a2
            sub_v = vmap[ymin:ymax + 1, xmin:xmax + 1]
            sub_c = cov[ymin:ymax + 1, xmin:xmax + 1]
            sub_v[inside] = interp[inside]
            sub_c[inside] = 1.0
    return vmap, cov


def multiply_texture(path, vmap, cov, out_path, size=None):
    """Умножить RGB текстуры на карту (вне покрытия — без изменений).

    Если размеры не совпадают, карта масштабируется до размера текстуры.
    Возвращает (путь, доля покрытых пикселей).
    """
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im, np.float32) / 255.0
    h, w = a.shape[:2]
    v = vmap
    c = cov
    if size and size != h:
        v = np.asarray(Image.fromarray((vmap * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
                       np.float32) / 255.0
        c = np.asarray(Image.fromarray((cov * 255).astype(np.uint8), "L").resize((w, h), Image.BILINEAR),
                       np.float32) / 255.0
    factor = np.where(c > 0.5, v, 1.0)[..., None]
    a[:, :, :3] = np.clip(a[:, :, :3] * factor, 0.0, 1.0)
    Image.fromarray((a * 255.0).astype(np.uint8), "RGBA").save(out_path)
    return out_path, float((cov > 0.5).mean())


def ao_from_pdo(pdo_path, uvs2, channel=0):
    """AO по вершинам: красный канал `_pdo`, сэмплированный по второму набору UV."""
    return sample_scalar(pdo_path, uvs2, channel=channel)
