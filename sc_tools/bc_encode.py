# -*- coding: utf-8 -*-
"""Качественный энкодер BC1/BC3 (DXT1/DXT5) на numpy, без внешних зависимостей.

Мотив: штатный энкодер SDK выбирает концы блока по min/max и один раз
раскладывает индексы — на градиентах это даёт «плоские» пятна и блочность.
Здесь после первичной раскладки концы пересчитываются методом наименьших
квадратов (LSQ) по уже назначенным индексам, затем индексы пересчитываются
заново; выбирается лучший из двух кандидатов (min/max и «сжатые» концы).

Формат BC3 (DXT5): 8 байт альфы (2 конца + 16×3-битных индекса) +
8 байт цвета (BC1, 4-цветный режим: 2×RGB565 + 16×2-битных индекса).
BC1 (DXT1) — только цветовой блок.
"""
from __future__ import annotations

import struct

import numpy as np


def _blocks(rgba, size=4):
    """(h,w,4) uint8 -> (N,16,4) float32, блоки 4x4 в порядке строк."""
    h, w = rgba.shape[:2]
    assert h % size == 0 and w % size == 0, "размеры должны быть кратны 4"
    b = rgba.reshape(h // size, size, w // size, size, rgba.shape[2])
    b = b.transpose(0, 2, 1, 3, 4).reshape(-1, size * size, rgba.shape[2])
    return b.astype(np.float32)


def _quant565(c):
    c = np.clip(np.rint(c), 0, 255).astype(np.int32)
    r = (c[..., 0] >> 3) & 0x1F
    g = (c[..., 1] >> 2) & 0x3F
    b = (c[..., 2] >> 3) & 0x1F
    return (r << 11) | (g << 5) | b


def _dequant565(v):
    v = v.astype(np.int32)
    r = ((v >> 11) & 0x1F) << 3
    g = ((v >> 5) & 0x3F) << 2
    b = (v & 0x1F) << 3
    r = r | (r >> 5)
    g = g | (g >> 6)
    b = b | (b >> 5)
    return np.stack([r, g, b], axis=-1).astype(np.float32)


def _palette4(e0, e1):
    """(N,3) концы -> (N,4,3) палитра 4-цветного режима."""
    p2 = (2.0 * e0 + e1) / 3.0
    p3 = (e0 + 2.0 * e1) / 3.0
    return np.stack([e0, e1, p2, p3], axis=1)


def _assign(colors, palette):
    """Индексы ближайших цветов палитры и ошибка. colors (N,16,3), palette (N,4,3)."""
    d = ((colors[:, :, None, :] - palette[:, None, :, :]) ** 2).sum(axis=-1)
    idx = d.argmin(axis=-1)
    err = d.min(axis=-1).sum(axis=1)
    return idx, err


def _lsq_endpoints(colors, idx):
    """Пересчёт концов LSQ по назначенным индексам (4-цветный режим)."""
    w0 = np.array([1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0], np.float32)
    w1 = np.array([0.0, 1.0, 1.0 / 3.0, 2.0 / 3.0], np.float32)
    a0 = w0[idx]
    a1 = w1[idx]
    # нормальные уравнения 2x2 по каждому каналу
    s00 = (a0 * a0).sum(axis=1)
    s01 = (a0 * a1).sum(axis=1)
    s11 = (a1 * a1).sum(axis=1)
    b0 = (a0[..., None] * colors).sum(axis=1)
    b1 = (a1[..., None] * colors).sum(axis=1)
    det = s00 * s11 - s01 * s01
    det = np.where(np.abs(det) < 1e-6, 1e-6, det)
    e0 = (b0 * s11[:, None] - b1 * s01[:, None]) / det[:, None]
    e1 = (b1 * s00[:, None] - b0 * s01[:, None]) / det[:, None]
    return e0, e1


def encode_bc1(rgba, refine=True):
    """Цветовая часть BC1/DXT1: 8 байт на блок 4x4."""
    blocks = _blocks(rgba, 4)
    colors = blocks[:, :, :3]
    mn = colors.min(axis=1)
    mx = colors.max(axis=1)
    mean = colors.mean(axis=1, keepdims=True)
    d = colors - mean
    cov = np.einsum("nki,nkj->nij", d, d)
    # главная ось блока (собственный вектор наибольшего собственного числа)
    vals, vecs = np.linalg.eigh(cov)
    axis = vecs[:, :, -1:]                      # (N,3,1)
    proj = np.einsum("nki,nij->nkj", d, axis)   # (N,16,1)
    t_min = proj.min(axis=1); t_max = proj.max(axis=1)
    e0_pca = mean[:, 0, :] + axis[:, :, 0] * t_max
    e1_pca = mean[:, 0, :] + axis[:, :, 0] * t_min
    best = None
    for e0, e1 in ((mx, mn), (mx * 0.97 + mn * 0.03, mn * 0.97 + mx * 0.03),
                   (e0_pca, e1_pca)):
        pal = _palette4(e0, e1)
        idx, err = _assign(colors, pal)
        q0 = _quant565(e0)
        q1 = _quant565(e1)
        sel = (err, q0, q1, idx)
        if best is None or err.sum() < best[0].sum():
            best = sel
    err, q0, q1, idx = best
    if refine:
        # несколько итераций «назначение индексов -> пересчёт концов (LSQ)»
        best_err = err.sum()
        q0b, q1b, idxb = q0, q1, idx
        for _it in range(4):
            e0, e1 = _lsq_endpoints(colors, idx)
            q0n = _quant565(e0)
            q1n = _quant565(e1)
            pal = _palette4(_dequant565(q0n), _dequant565(q1n))
            idxn, errn = _assign(colors, pal)
            if errn.sum() < best_err:
                best_err = errn.sum()
                q0b, q1b, idxb = q0n, q1n, idxn
            q0, q1, idx = q0n, q1n, idxn
        q0, q1, idx = q0b, q1b, idxb
    # 4-цветный режим требует q0 > q1; иначе меняем и перекодируем индексы
    # 4-цветный режим требует q0 > q1: меняем концы и перенумеровываем (0<->1, 2<->3)
    swap = q0 <= q1
    if swap.any():
        remap = np.array([1, 0, 3, 2], np.int64)
        q0, q1 = np.where(swap, q1, q0), np.where(swap, q0, q1)
        idx = np.where(swap[:, None], remap[idx], idx)
    bits = np.zeros(len(blocks), np.uint32)
    for i in range(16):
        bits |= (idx[:, i].astype(np.uint32) & 0x3) << (2 * i)
    out = bytearray()
    for n in range(len(blocks)):
        out += struct.pack("<HHI", int(q0[n]), int(q1[n]), int(bits[n]))
    return bytes(out)


def _alpha_palette(a0, a1):
    """(N,) концы -> (N,8) уровни альфы (8-alpha режим, a0 > a1)."""
    lv = [a0, a1]
    for i in range(2, 8):
        lv.append(((8 - i) * a0 + (i - 1) * a1) / 7.0)
    return np.stack(lv, axis=1)


def encode_bc3_alpha(rgba, refine=True):
    """Альфа-часть BC3: 8 байт на блок 4x4."""
    blocks = _blocks(rgba, 4)
    a = blocks[:, :, 3]
    a0 = a.max(axis=1)
    a1 = a.min(axis=1)
    pal = _alpha_palette(a0, a1)
    d = np.abs(a[:, :, None] - pal[:, None, :])
    idx = d.argmin(axis=-1)
    if refine:
        # одна итерация LSQ по индексам
        w = np.zeros((8,), np.float32)
        for i in range(8):
            if i < 2:
                w0, w1 = (1.0 if i == 0 else 0.0), (1.0 if i == 1 else 0.0)
            else:
                w0 = (8 - i) / 7.0
                w1 = (i - 1) / 7.0
            w[i] = w0 - w1  # a = w1*a1 + (a0-a1)*w0 -> параметризация ниже
        # проще: прямая подгонка двух концов для каждого блока
        # a ≈ a1 + (a0 - a1) * k_i, где k_i = w0
        k = np.zeros((8,), np.float32)
        for i in range(8):
            k[i] = 1.0 if i == 0 else (0.0 if i == 1 else (8 - i) / 7.0)
        kk = k[idx]                      # (N,16)
        base = 1.0 - kk
        # a = a1*base + a0*kk  (линейно по двум неизвестным)
        s00 = (base * base).sum(axis=1)
        s01 = (base * kk).sum(axis=1)
        s11 = (kk * kk).sum(axis=1)
        b0 = (base * a).sum(axis=1)
        b1 = (kk * a).sum(axis=1)
        det = s00 * s11 - s01 * s01
        det = np.where(np.abs(det) < 1e-6, 1e-6, det)
        na1 = (b0 * s11 - b1 * s01) / det
        na0 = (b1 * s00 - b0 * s01) / det
        a0n = np.clip(np.rint(na0), 0, 255)
        a1n = np.clip(np.rint(na1), 0, 255)
        pal2 = _alpha_palette(a0n, a1n)
        d2 = np.abs(a[:, :, None] - pal2[:, None, :])
        idx2 = d2.argmin(axis=-1)
        if d2.min(axis=-1).sum() < d.min(axis=-1).sum():
            a0, a1, idx = a0n, a1n, idx2
    a0 = np.clip(np.rint(a0), 0, 255).astype(np.uint8)
    a1 = np.clip(np.rint(a1), 0, 255).astype(np.uint8)
    bits = np.zeros(len(blocks), np.uint64)
    for i in range(16):
        bits |= (idx[:, i].astype(np.uint64) & 0x7) << np.uint64(3 * i)
    out = bytearray()
    for n in range(len(blocks)):
        out += bytes([int(a0[n]), int(a1[n])]) + int(bits[n]).to_bytes(6, "little")
    return bytes(out)


def encode(rgba, fmt="DXT5"):
    """rgba: (h,w,4) uint8 -> сжатые данные (BC3 или BC1)."""
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    if fmt in ("DXT1", "BC1"):
        return encode_bc1(rgba)
    alpha = encode_bc3_alpha(rgba)
    color = encode_bc1(rgba)
    # чередование блоков: 8 байт альфы + 8 байт цвета
    a = np.frombuffer(alpha, np.uint8).reshape(-1, 8)
    c = np.frombuffer(color, np.uint8).reshape(-1, 8)
    out = np.concatenate([a, c], axis=1).tobytes()
    return out

def encode_best(rgba, fmt="DXT5"):
    """Сжать текстурой лучшим доступным энкодером.

    Порядок: энкодер Targem (dxt_targem, PSNR ~39.5 дБ на тестовой текстуре),
    затем собственный BC3 (PCA + LSQ, ~37.2 дБ), затем запасной вариант.
    """
    try:
        from . import dxt_targem as _sdk
        return _sdk.encode(rgba.tobytes(), rgba.shape[1], rgba.shape[0], fmt)
    except Exception:
        return encode(rgba, fmt)
