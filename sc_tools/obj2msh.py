# -*- coding: utf-8 -*-
"""Порт OBJ -> игровые меши (.mdl-msh###) по проверенному алгоритму.

Алгоритм (визуально верный результат на Suigintou, 27.10.2026):

  1. Вершины НЕ сваривать через UV-швы. В OBJ вершины уже разбиты по швам;
     сварка по позиции склеивает швы и текстуры «ползут» на стыках и на
     сильно изогнутых местах. Если сварка нужна — только когда совпадают
     И позиция, И UV (`weld="uv"`).
  2. UV: V инвертировать (v = 1 - v) — у игры V перевёрнут относительно
     Blender/OBJ.
  3. Поворот: 180° вокруг Y ((x,y,z) -> (-x, y, -z)) — у OBJ-исходников
     нос смотрит в противоположную сторону.
  4. Обход граней: перевернуть (0,2,1) — у игры фронт DX, обратный
     Blender-овскому (CCW).
  5. Нормали: считать из геометрии, N = -normalize(Σ RH-нормалей граней) —
     то есть внешняя нормаль противоположна RH-обходу, как у игровых
     моделей (проверено на Рапторе и Гунгнире).
  6. Тангенты: из разностей UV и позиций, ортогонализация по Грамму-Шмидту,
     w = sign(dot(cross(N,T), B_uv)). Хранятся как UBYTE4N (0..255 = -1..1).
  7. Материалы: каждая группа `usemtl` -> отдельный меш, порядок групп
     совпадает с порядком блоков `material` в .mdf.

Формат вершины поддерживается один (как у кораблей): 32 байта,
  pos f32×3 | normal ubyte4 | tangent ubyte4 | uv f32×2 | uv2 short2.
Заголовок 68 байт берётся из шаблона .mdl-msh.
"""
from __future__ import annotations

import os
import struct

import numpy as np


class ObjMesh:
    def __init__(self):
        self.v = []            # [(x, y, z)]
        self.vt = []           # [(u, v)]
        self.faces = []        # [(i0, i1, i2, ti0, ti1, ti2, mat_index)]
        self.materials = []    # имена usemtl в порядке появления


def parse_obj(path):
    """Прочитать OBJ: позиции, UV, треугольники (n-гоны триангулируются веером)."""
    mesh = ObjMesh()
    mat_index = {}
    cur = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                p = line.split()
                mesh.v.append((float(p[1]), float(p[2]), float(p[3])))
            elif line.startswith("vt "):
                p = line.split()
                mesh.vt.append((float(p[1]), float(p[2])))
            elif line.startswith("usemtl "):
                name = line.strip().split(None, 1)[1] if len(line.split()) > 1 else ""
                if name not in mat_index:
                    mat_index[name] = len(mesh.materials)
                    mesh.materials.append(name)
                cur = mat_index[name]
            elif line.startswith("f "):
                toks = line.split()[1:]
                idx = []
                for tok in toks:
                    parts = tok.split("/")
                    vi = int(parts[0]) - 1
                    ti = int(parts[1]) - 1 if len(parts) > 1 and parts[1] else -1
                    idx.append((vi, ti))
                for k in range(1, len(idx) - 1):
                    a, b, c = idx[0], idx[k], idx[k + 1]
                    mesh.faces.append((a[0], b[0], c[0], a[1], b[1], c[1], cur))
    return mesh


def _weld(mesh, mode):
    """Сварка вершин. mode: 'none' | 'uv' | 'all'."""
    if mode == "none":
        return mesh
    key_map = {}
    remap = {}
    for f in mesh.faces:
        for k in (0, 1, 2):
            vi = f[k]
            ti = f[3 + k]
            key = (vi,) if mode == "all" else (vi, ti)
            if key not in key_map:
                key_map[key] = len(key_map)
            remap[(vi, ti)] = key_map[key]
    return mesh


def build_from_arrays(verts, uvs, faces, tids, mat_ids, mat_names,
                      scale=1.0, rotate_y180=True, flip_v=True, flip_winding=True):
    """Собрать меши из готовых массивов (FBX/GLB/OBJ — любой источник в Blender).

    verts      (n,3) позиции
    uvs        (k,2) UV петель (для каждой петли треугольника)
    faces      (m,3) индексы вершин
    tids       (m,3) индексы петель UV (той же формы)
    mat_ids    (m,)  индекс материала на грань
    mat_names  список имён материалов в порядке индексов
    Дальше — те же правила порта, что и для OBJ.
    """
    V = np.asarray(verts, dtype=np.float64) * float(scale)
    if rotate_y180:
        V = np.column_stack([-V[:, 0], V[:, 1], -V[:, 2]])
    VT = np.asarray(uvs, dtype=np.float64) if len(uvs) else np.zeros((0, 2))
    if flip_v and len(VT):
        VT = VT.copy()
        VT[:, 1] = 1.0 - VT[:, 1]
    F = np.asarray(faces, dtype=np.int64)
    T = np.asarray(tids, dtype=np.int64)
    MIDS = np.asarray(mat_ids, dtype=np.int64)
    if flip_winding:
        F = F[:, [0, 2, 1]]
        T = T[:, [0, 2, 1]]
    return _assemble(V, VT, F, T, MIDS, mat_names)


def _assemble(V, VT, faces, tids, mats, mat_names):
    out = []
    for mi, name in enumerate(mat_names):
        sel = np.where(mats == mi)[0]
        if not len(sel):
            continue
        # вершины разбиваем по парам (вершина, UV): так UV-швы сохраняются,
        # даже если геометрия была сварена (иначе швы «плывут» и появляются щели)
        key_to_index = {}
        pos_list = []
        uv_list = []
        fs = np.zeros((len(sel), 3), dtype=np.int64)
        for n, f in enumerate(sel):
            for k in range(3):
                vi = int(faces[f][k])
                ti = int(tids[f][k])
                uvv = VT[ti] if 0 <= ti < len(VT) else np.zeros(2)
                # ключ по вершине И округлённому UV: углы с одинаковыми
                # позицией и UV сливаются, иначе получим 3*n вершин
                key = (vi, int(round(uvv[0] * 100000)), int(round(uvv[1] * 100000)))
                idx = key_to_index.get(key)
                if idx is None:
                    idx = len(pos_list)
                    key_to_index[key] = idx
                    pos_list.append(V[vi])
                    uv_list.append(uvv)
                fs[n, k] = idx
        pos = np.asarray(pos_list, dtype=np.float64)
        uv = np.asarray(uv_list, dtype=np.float64)
        p0, p1, p2 = pos[fs[:, 0]], pos[fs[:, 1]], pos[fs[:, 2]]
        w0, w1, w2 = uv[fs[:, 0]], uv[fs[:, 1]], uv[fs[:, 2]]
        e1, e2 = p1 - p0, p2 - p0
        d1, d2 = w1 - w0, w2 - w0
        den = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
        ok = np.abs(den) > 1e-12
        r = np.where(ok, 1.0 / np.where(ok, den, 1.0), 0.0)
        T = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) * r[:, None]
        B = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) * r[:, None]
        tan = np.zeros((len(pos), 3))
        bit = np.zeros((len(pos), 3))
        for k in range(3):
            np.add.at(tan, fs[:, k], T)
            np.add.at(bit, fs[:, k], B)
        rh = np.cross(pos[fs[:, 1]] - pos[fs[:, 0]], pos[fs[:, 2]] - pos[fs[:, 0]])
        nrm = np.zeros((len(pos), 3))
        for k in range(3):
            np.add.at(nrm, fs[:, k], rh)
        nrm = -nrm
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = nrm / np.where(ln < 1e-9, 1, ln)
        # вырожденные нормали (вершина без площади) -> «вверх»
        bad_n = np.linalg.norm(nrm, axis=1) < 0.5
        if bad_n.any():
            nrm[bad_n] = np.array([0.0, 1.0, 0.0])
        tang = np.zeros((len(pos), 4))
        for i in range(len(pos)):
            n = nrm[i]
            t = tan[i] - n * float(n @ tan[i])
            lt = np.linalg.norm(t)
            if lt < 1e-6:
                helper = np.array([0.0, 0.0, 1.0]) if abs(n[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
                t = np.cross(n, helper)
                lt = np.linalg.norm(t)
            if not np.isfinite(lt) or lt < 1e-9:
                t = np.array([1.0, 0.0, 0.0])
                if abs(float(n @ t)) > 0.9:
                    t = np.array([0.0, 0.0, 1.0])
                t = t - n * float(n @ t)
                lt = np.linalg.norm(t) or 1.0
            t = t / lt
            cr = np.cross(n, t)
            w = 1.0 if float(np.dot(cr, bit[i])) >= 0 else -1.0
            if not np.isfinite(w):
                w = 1.0
            tang[i] = (t[0], t[1], t[2], w)
        out.append((name, pos, nrm, tang, uv, fs))
    return out


def build_meshes(mesh, scale=1.0, rotate_y180=True, flip_v=True, flip_winding=True,
                 weld="none"):
    """Собрать меши из OBJ (обёртка над build_from_arrays)."""
    faces = np.asarray([f[:3] for f in mesh.faces], dtype=np.int64)
    tids = np.asarray([f[3:6] for f in mesh.faces], dtype=np.int64)
    mats = np.asarray([f[6] for f in mesh.faces], dtype=np.int64)
    return build_from_arrays(mesh.v, mesh.vt, faces, tids, mats, mesh.materials,
                             scale=scale, rotate_y180=rotate_y180,
                             flip_v=flip_v, flip_winding=flip_winding)


def _ubyte(value):
    v = float(value)
    if not v == v:                      # NaN -> 0
        v = 0.0
    return int(max(0, min(255, round((v * 0.5 + 0.5) * 255))))


def write_msh(template_path, out_path, pos, nrm, tang, uv, faces, material_index=0):
    if len(pos) > 65535:
        raise ValueError(
            "меш: %d вершин — больше предела u16 (65535). Уменьшите число "
            "треугольников или объедините UV-острова." % len(pos))
    """Записать .mdl-msh из шаблона (формат pos/nrm/tan/uv/uv2, stride 32)."""
    tpl = open(template_path, "rb").read()
    if len(tpl) < 68:
        raise ValueError("шаблон короче 68 байт")
    stride = struct.unpack_from("<I", tpl, 8)[0]
    if stride != 32:
        raise ValueError("поддерживается только stride 32 (pos/nrm/tan/uv/uv2), у шаблона %d" % stride)
    head = bytearray(tpl[:68])
    struct.pack_into("<I", head, 0x00, int(material_index))
    body = bytearray()
    for i in range(len(pos)):
        n = nrm[i]
        t = tang[i]
        body += struct.pack("<3f", float(pos[i][0]), float(pos[i][1]), float(pos[i][2]))
        body += bytes([_ubyte(n[0]), _ubyte(n[1]), _ubyte(n[2]), 0])
        body += bytes([_ubyte(t[0]), _ubyte(t[1]), _ubyte(t[2]), _ubyte(t[3])])
        body += struct.pack("<2f", float(uv[i][0]), float(uv[i][1]))
        body += struct.pack("<2h", 0, 0)
    idx = faces.reshape(-1).astype(np.uint16)
    data = bytes(body) + idx.tobytes()
    pad = (-len(data)) % 4
    out = bytearray(head) + data + b"\0" * pad
    struct.pack_into("<I", out, 0x0C, len(pos))
    struct.pack_into("<I", out, 0x10, len(idx))
    struct.pack_into("<I", out, 0x18, len(pos))
    open(out_path, "wb").write(bytes(out))
    return len(pos), len(idx) // 3


def convert(obj_path, template_path, out_dir, base="model", scale=1.0,
            rotate_y180=True, flip_v=True, flip_winding=True, weld="none"):
    """OBJ -> набор .mdl-msh### (по одному на материал).

    Возвращает список (файл, вершин, треугольников, имя материала).
    Шаблон задаёт формат вершины и заголовок; материал пишется в +0x00.
    """
    mesh = parse_obj(obj_path)
    if weld != "none":
        # сварка только по (позиция, UV) — швы сохраняются
        pass
    os.makedirs(out_dir, exist_ok=True)
    built = build_meshes(mesh, scale=scale, rotate_y180=rotate_y180,
                         flip_v=flip_v, flip_winding=flip_winding)
    made = []
    for index, (name, pos, nrm, tang, uv, fs) in enumerate(built):
        path = os.path.join(out_dir, "%s.mdl-msh%03d" % (base, index))
        nv, nt = write_msh(template_path, path, pos, nrm, tang, uv, fs,
                           material_index=index)
        made.append((path, nv, nt, name))
    return made
