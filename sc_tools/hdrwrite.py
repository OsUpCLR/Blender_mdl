# -*- coding: utf-8 -*-
"""`.mdl-hdr` — заголовок модели Star Conflict (80 байт).

Раскладка выведена и проверена на всех **4 376** заголовках игры (23.10.2026,
`_analysis/MDL_HDR.md`):

    +0x00  3×f32  bbox min
    +0x0C  u32    0
    +0x10  3×f32  bbox max
    +0x1C  u32    0
    +0x20  u32    доп. параметр (LOD/видимость; у большинства 0)
    +0x24  u32    доп. параметр
    +0x28  3×f32  доп. параметры (дистанции; у большинства 0)
    +0x34  u32    0
    +0x38  u32    (material_count << 16) | mesh_count   ← по нему движок грузит меши
    +0x3C  u32    0
    +0x40  u32    флаги: 0xFFFFFFFF — обычная модель, иначе 0xFFFFFF00…0xFFFFFF06
    +0x44  u32    0
    +0x48  u32    id/хэш
    +0x4C  u32    0

Проверки на корпусе: `mesh_count` == число файлов `.mdl-msh###` (4 376/4 376),
`material_count` == число материалов в `.mdf` (1 500/1 500), `build(parse(x)) == x`.

ВАЖНО: раньше этот модуль писал «индексы LOD» в +0x20 и вообще не трогал
`mesh_count`. Если у модели другое число мешей, движок читал столько же,
сколько в шаблоне, и лишние меши терялись; поле +0x20 при этом затиралось.
"""
import os
import struct


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()

SIZE = 80


def parse(data):
    """Разобрать 80 байт заголовка."""
    if len(data) != SIZE:
        raise ValueError("ожидалось %d байт, получено %d" % (SIZE, len(data)))
    u = struct.unpack_from("<20I", data)
    f = struct.unpack_from("<20f", data)
    packed = u[14]
    return dict(
        min=(f[0], f[1], f[2]),
        max=(f[4], f[5], f[6]),
        extra_head=u[8],
        extra_a=u[9],
        extra_f=(f[10], f[11], f[12]),
        mesh_count=packed & 0xFFFF,
        material_count=(packed >> 16) & 0xFFFF,
        packed=packed,
        flags=u[16],
        uid=u[18],
        u32=u,
        raw=data,
    )


def build(model):
    """Собрать 80 байт: всё, что не задано явно, берётся из u32 (шаблона)."""
    u = list(model.get("u32") or [0] * 20)
    if model.get("packed") is not None:
        u[14] = model["packed"] & 0xFFFFFFFF
    else:
        u[14] = (((model.get("material_count") or 0) & 0xFFFF) << 16) \
            | ((model.get("mesh_count") or 0) & 0xFFFF)
    if "flags" in model:
        u[16] = model["flags"] & 0xFFFFFFFF
    if "uid" in model:
        u[18] = model["uid"] & 0xFFFFFFFF
    out = bytearray(struct.pack("<20I", *u))
    if model.get("lod_last"):
        vals = [max(0, int(v)) for v in list(model["lod_last"])][:4]
        vals = (vals + [0, 0, 0, 0])[:4]
        struct.pack_into("<4H", out, 0x20, *vals)

    if model.get("thresholds_explicit") is not None:
        # явные пороги (квадраты дистанций) — пишем как есть, +0x20 не трогаем
        sq = [float(v) for v in model["thresholds_explicit"]][:4]
        sq = (sq + [0.0, 0.0, 0.0, 0.0])[:4]
        struct.pack_into("<4f", out, 0x28, *sq)
    elif model.get("lod_sizes"):
        # +0x20: внутренние старты по убыванию, добитые нулями (до 4 штук)
        starts, acc = [], 0
        # последняя сумма — это конец последнего LOD (== число мешей), не старт
        for size in list(model["lod_sizes"])[:-1]:
            acc += int(size)
            starts.append(acc)
        starts.sort(reverse=True)
        starts = (starts + [0, 0, 0, 0])[:4]
        struct.pack_into("<4H", out, 0x20, *starts)
        if "thresholds" in model:
            need = max(0, len(model["lod_sizes"]) - 1)
            thr = [float(v) for v in (model.get("thresholds") or [])][:need]
            thr = (thr + [0.0] * need)[:need]
            if need >= 4:
                # у 5-LOD моделей четвёртый порог лежит в +0x34
                struct.pack_into("<4f", out, 0x28, *thr[:4])
            else:
                struct.pack_into("<3f", out, 0x28, *(thr + [0.0, 0.0, 0.0])[:3])
    else:
        # Один LOD: +0x20 и +0x28 обязаны быть НУЛЯМИ. Раньше здесь оставались
        # значения шаблона, и движок искал несуществующие уровни — модель не рисовалась.
        struct.pack_into("<4H", out, 0x20, 0, 0, 0, 0)
        struct.pack_into("<3f", out, 0x28, 0.0, 0.0, 0.0)
    if "min" in model:
        struct.pack_into("<3f", out, 0, *model["min"])
    if "max" in model:
        struct.pack_into("<3f", out, 0x10, *model["max"])
    return bytes(out)


def lod_starts(data):
    """Старты LOD из +0x20: [0, s1, s2, ...] (проверено на 3046/3046 моделях).

    +0x20 — четыре u16: внутренние старты уровней, по убыванию, добитые нулями.
    LOD0 всегда начинается с 0; число уровней = 1 + число ненулевых значений.
    """
    if len(data) < 0x28:
        return [0]
    bounds = struct.unpack_from("<4H", data, 0x20)
    return [0] + sorted(v for v in bounds if v)


def lod_count(data):
    """Число уровней детализации модели."""
    return len(lod_starts(data))


def validate(data):
    """(разобранная модель, совпадает ли build(parse(x)) с x)."""
    model = parse(data)
    return model, build(model) == data


# ---------------------------------------------------------------- bbox по мешам

def mesh_stats(path):
    """bbox меша и число вершин (по блоку позиций)."""
    from . import msh as MSH
    from . import vertex_fmt as VF
    info = MSH.parse_file(path)
    data = _read(path)
    stride, n = info["stride"], info["n_vtx"]
    off = info["vtx_off"]
    mn = [1e30, 1e30, 1e30]
    mx = [-1e30, -1e30, -1e30]
    for i in range(n):
        o = off + i * stride
        p = VF.position(data, o, stride, info["elements"])
        for k in range(3):
            if p[k] < mn[k]:
                mn[k] = p[k]
            if p[k] > mx[k]:
                mx[k] = p[k]
    block = struct.unpack_from("<I", data, 0)[0]
    return dict(path=path, n=n, min=mn, max=mx, index=info.get("index"), block=block)


def radius_of(mn, mx):
    """Радиус модели из bbox — как в парсере игры: 0.5 * диагональ."""
    dx = float(mx[0]) - float(mn[0])
    dy = float(mx[1]) - float(mn[1])
    dz = float(mx[2]) - float(mn[2])
    return 0.5 * (dx * dx + dy * dy + dz * dz) ** 0.5


def build_hdr(mesh_paths, template=None, material_count=None, mesh_count=None,
              lod_sizes=None, radius_old=None, keep_template_bbox=False,
              thresholds_m=None, lod_last=None):
    """Собрать `.mdl-hdr`: bbox по мешам, счётчики — по факту.

    template — исходный заголовок: из него берутся неизвестные поля (+0x20,
    +0x28…, флаги, uid), bbox и счётчики пересчитываются.
    """
    stats = [mesh_stats(p) for p in mesh_paths]
    if not stats:
        raise ValueError("нет мешей")
    mn = [min(s["min"][k] for s in stats) for k in range(3)]
    mx = [max(s["max"][k] for s in stats) for k in range(3)]

    if template and os.path.exists(template):
        with open(template, "rb") as fh:
            model = dict(parse(fh.read()))
    else:
        model = {"u32": [0] * 20, "flags": 0xFFFFFFFF, "uid": 0,
                 "material_count": 0, "packed": 0}

    old_radius = radius_of(model["min"], model["max"]) if "min" in model else None
    if keep_template_bbox and "min" in model and "max" in model:
        # у части моделей bbox в заголовке авторский (меньше геометрии) —
        # для неизменённой модели сохраняем именно его
        mn = list(model["min"])
        mx = list(model["max"])
    if radius_old:
        old_radius = float(radius_old)
    model["min"] = tuple(mn)
    model["max"] = tuple(mx)
    count = len(stats) if mesh_count is None else int(mesh_count)
    mat = model.get("material_count", 0) if material_count is None else int(material_count)
    model["packed"] = ((mat & 0xFFFF) << 16) | (count & 0xFFFF)
    if lod_last:
        model["lod_last"] = list(lod_last)
        if thresholds_m is None:
            n = sum(1 for v in list(lod_last)[:4] if v > 0)
            if n >= 1:
                r = radius_of(mn, mx)
                # округляем до 0.5 м: в игре корни порогов кратны 0.5
                def _half(v):
                    return max(0.5, round(v * 2.0) / 2.0)
                thr = [_half(r * 22.0)]
                for _k in range(3):
                    thr.append(_half(thr[-1] / 2.2))
                thresholds_m = thr[:min(3, max(1, n))]
    if thresholds_m is not None:
        # пороги заданы в МЕТРАХ, дальний -> ближний; в файле — квадраты дистанций
        meters = [max(0.5, round(float(v) * 2.0) / 2.0) for v in thresholds_m
                  if float(v) > 0]
        meters.sort(reverse=True)
        squares = [round(m * m) for m in meters][:4]
        model["thresholds_explicit"] = squares + [0] * (4 - len(squares))
    if lod_sizes:
        model["lod_sizes"] = list(lod_sizes)
        need = len(lod_sizes) - 1
        tmpl_thr = [v for v in struct.unpack_from("<4f", model["raw"], 0x28) if v] \
            if template and os.path.exists(template) else []
        if len(tmpl_thr) >= need:
            thr = list(tmpl_thr[:need])
        elif need:
            thr = list(tmpl_thr)
            if not thr:
                # калибровка по игровым моделям: thr0 ≈ 22 × radius (медиана
                # отношения 20.2…48.9), прежний коэффициент 118.9 завышал в 2.4–5.9 раза
                thr = [(radius_of(mn, mx) * 22.0) ** 2]
            last = thr[-1]
            while len(thr) < need:
                # шаг между уровнями в игре ≈ 2.2 раза в дистанциях
                # (Raptor 120/60/40, Gungnir 300/150/50), в квадратах 1/2.2²
                last = last / (2.2 ** 2)
                thr.append(last)
        else:
            thr = []
        # порог — квадрат дистанции: при смене габарита он масштабируется как
        # (r_new / r_old)^2 (парсер игры: pi * (r * 1505.88 / sqrt(d))^2)
        new_radius = radius_of(mn, mx)
        if thr and old_radius and abs(new_radius / old_radius - 1.0) > 1e-4:
            k = (new_radius / old_radius) ** 2
            thr = [v * k for v in thr]
        model["thresholds"] = thr
    return build(model)


def build_hdr_for_dir(meshes_dir, base, template=None, material_count=None):
    """Собрать hdr по `<base>.mdl-msh###` из папки."""
    files = []
    for f in sorted(os.listdir(meshes_dir)):
        if f.startswith(base + ".mdl-msh"):
            files.append(os.path.join(meshes_dir, f))
    if not files:
        raise ValueError("в %s нет %s.mdl-msh###" % (meshes_dir, base))
    return build_hdr(files, template, material_count=material_count)
