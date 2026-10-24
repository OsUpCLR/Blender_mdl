#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.material — материалы Star Conflict (.mdf) -> Wavefront .mtl

Формат .mdf — ОБЫЧНЫЙ ТЕКСТ (скрипт материала Hammer Engine):

    material ship_hull
    {
        ColormapSampler     "models\\...\\ship_s1"
        Diffuse2Sampler     "models\\...\\ship_glow"
        DiffuseSampler      "models\\...\\ship_d"
        LightmapSampler     "models\\...\\ship_pdo"
        NormalSampler       "models\\...\\ship_nm"
        SpecularColorSampler "models\\...\\ship_sc"
        UserParam2_Float4 ( 2 2 0 0 )
        InfluenceBones 1
        pins { User1 1 }
    }
    material dyn_glass { ... }

Пути к текстурам даны БЕЗ расширения; наши конвертированные файлы — .dds.

Привязка меша к материалу: ПЕРВОЕ поле заголовка .mdl-msh### (@0x00) —
индекс материала в списке из .mdf (в порядке следования в файле).

Слоты сэмплеров -> OBJ/MTL:
    DiffuseSampler       -> map_Kd          (диффуз/цвет)
    NormalSampler        -> norm + map_Bump (карта нормалей)
    SpecularColorSampler -> map_Ks          (глянец)
    ColormapSampler      -> # s1            (доп. слой)
    Diffuse2Sampler      -> # glow          (свечение)
    LightmapSampler      -> # pdo           (lightmap/parallax)
"""
import os
import re

# сэмплер -> слот MTL (None = пишем комментарием)
SAMPLER_MTL = {
    "DiffuseSampler": "map_Kd",
    "NormalSampler": "norm",
    "SpecularColorSampler": "map_Ks",
    # `_glow` (Diffuse2Sampler) идёт в MTL как map_Ke — Blender подключит её к Emission.
    "Diffuse2Sampler": "map_Ke",
    # Шероховатость: считается из альфы блика (_sc.a = glossFactor) функцией
    # compose_rough и кладётся как map_Pr — своего ключа в MTL для неё нет,
    # штатный импортёр Blender его игнорирует, поэтому подключает наш импортёр.
    "RoughnessSampler": "map_Pr",
    "ColormapSampler": None,
    "LightmapSampler": None,
}

# читаемые подписи для комментариев
SAMPLER_NOTE = {
    "ColormapSampler": "s1",
    "Diffuse2Sampler": "glow",
    "LightmapSampler": "pdo",
    "DetailSampler": "detail",
    "AmbOcclSampler": "ao",
}

RE_MATERIAL = re.compile(r"material\s+([A-Za-z0-9_\-.]+)")
RE_SAMPLER = re.compile(
    r"(ColormapSampler|Diffuse2Sampler|DiffuseSampler|LightmapSampler|"
    r"NormalSampler|SpecularColorSampler|SpecularSampler|BumpSampler)\s+\"([^\"]+)\"")
RE_PARAM = re.compile(r"^\s*(\w+)\s*\(\s*([-\d.eE+\s]+)\)\s*$")


def parse_mdf(path):
    """
    Разбор .mdf -> список материалов:
        [{'name': 'ship_hull', 'samplers': {'DiffuseSampler': 'models\\...'},
          'params': {'UserParam0_Float4': (0.05, 0.09, 0.7)}, 'index': 0}, ...]
    Порядок материалов = порядок в файле (он же индекс в заголовке меша).
    """
    with open(path, "rb") as fh:
        text = fh.read().decode("latin1", "replace")
    out = []
    cur = None
    for line in text.splitlines():
        m = RE_MATERIAL.search(line)
        if m and "material" == line.strip().split()[0]:
            cur = {"name": m.group(1), "samplers": {}, "params": {}, "index": len(out)}
            out.append(cur)
            continue
        s = RE_SAMPLER.search(line)
        if s and cur is not None:
            cur["samplers"][s.group(1)] = s.group(2)
            continue
        p = RE_PARAM.match(line)
        if p and cur is not None:
            try:
                cur["params"][p.group(1)] = tuple(float(x) for x in p.group(2).split())
            except ValueError:
                pass
    return out


_MATERIAL_RE = re.compile(r"^\s*material\s+(\S+)", re.M)


def patch_mdf(source_bytes, material_names=None):
    """Собрать .mdf с сохранением исходной структуры.

    Существующие блоки `material ... { ... }` остаются нетронутыми; материалы
    Blender, которых нет в исходнике, дописываются пустыми блоками (чтобы
    структура и порядок исходника не ломались).
    """
    text = source_bytes.decode("latin1", "replace")
    existing = set(_MATERIAL_RE.findall(text))
    extra = [n for n in (material_names or []) if n not in existing]
    if extra:
        if not text.endswith("\n"):
            text += "\n"
        for name in extra:
            text += "material %s\n{\n}\n\n" % name
    return text.encode("latin1", "replace")


def patch_mdf_file(source_path, material_names=None):
    return patch_mdf(open(source_path, "rb").read(), material_names)


def mdf_for_model(base):
    """Путь к .mdf, соответствующему базе модели (если есть)."""
    p = base + ".mdf"
    return p if os.path.exists(p) else None


def load_materials(base):
    """Материалы модели: (список, путь .mdf или None)."""
    p = mdf_for_model(base)
    if not p:
        return [], None
    return parse_mdf(p), p


def material_index(msh_path):
    """Индекс материала меша — первое u32 заголовка .mdl-msh###."""
    with open(msh_path, "rb") as fh:
        head = fh.read(4)
    return int.from_bytes(head, "little") if len(head) == 4 else 0


def build_texture_index(tex_dir, prefer="png"):
    """
    Индекс текстур: имя_без_расширения (в нижнем регистре) -> путь.
    Ищет рекурсивно, чтобы находить вложенные папки.

    prefer задаёт, какой файл побеждает, если для одного имени их несколько:
      "png" (по умолчанию) — .png > .tga > .jpg > .dds: берётся ОБРАБОТАННЫЙ
          файл, а не сырой. Причина: рядом лежат сырая конвертация из игры
          (`*_nm.dds` — DXT5nm, где X в альфе) и исправленная карта
          (`*_nm.png` — обычная RGB); при сыром файле Blender показывал «нет
          карты нормалей».
      "dds" — .dds > .png > .tga > .jpg: берутся текстуры в формате игры
          (для сравнения вариантов; производные — dyed/AO/rough/нормаль —
          существуют только в PNG и остаются PNG).
    """
    orders = {
        "png": {".png": 3, ".tga": 2, ".jpg": 1, ".dds": 0},
        "dds": {".dds": 3, ".png": 2, ".tga": 1, ".jpg": 0},
    }
    PRIORITY = orders.get(str(prefer).lower(), orders["png"])
    idx = {}
    if not tex_dir or not os.path.isdir(tex_dir):
        return idx
    for dp, dn, fn in os.walk(tex_dir):
        for f in fn:
            stem, ext = os.path.splitext(f)
            ext = ext.lower()
            if ext not in PRIORITY:
                continue
            key = stem.lower()
            old = idx.get(key)
            if old is None:
                idx[key] = os.path.join(dp, f)
                continue
            old_ext = os.path.splitext(old)[1].lower()
            if PRIORITY[ext] > PRIORITY.get(old_ext, -1):
                idx[key] = os.path.join(dp, f)
    return idx


def _rel(path, start):
    """Относительный путь с прямыми слэшами (как принято в MTL)."""
    try:
        return os.path.relpath(path, start).replace("\\", "/")
    except ValueError:
        return path.replace("\\", "/")


def write_mtl(path, materials, tex_dir=None, tex_index=None, kd=(0.8, 0.8, 0.8),
              alpha_materials=("decal",)):
    """
    Запись .mtl. Возвращает (число материалов, сколько текстур привязано).
    Текстуры ищутся по имени файла без расширения в tex_dir (рекурсивно).

    alpha_materials — подстроки имён материалов, которым пишется `map_d`
    (альфа-карта `<имя>_a.png` из `sc_texfix.py`). По умолчанию только декали:
    у них альфа — маска знака, тогда как альфу корпуса основной проход движка
    не использует (ALPHA_TEST стоит лишь в depth-проходе), и `map_d` сделал бы
    корпус полупрозрачным.
    """
    if tex_index is None:
        tex_index = build_texture_index(tex_dir)
    here = os.path.dirname(os.path.abspath(path)) or "."
    lines = ["# Star Conflict materials - sc_tools.material"]
    bound = 0
    for mat in materials:
        want_alpha = any(a.lower() in mat["name"].lower() for a in alpha_materials)
        lines.append("")
        lines.append("newmtl %s" % mat["name"])
        lines.append("Kd %.4f %.4f %.4f" % kd)
        lines.append("Ka 0.0000 0.0000 0.0000")
        lines.append("Ks 0.2500 0.2500 0.2500")
        lines.append("Ns 32.0")
        lines.append("d 1.0")
        lines.append("illum 2")
        for sampler, engine_path in mat["samplers"].items():
            stem = os.path.basename(engine_path.replace("/", "\\"))
            found = tex_index.get(stem.lower())
            slot = SAMPLER_MTL.get(sampler)
            if found:
                rel = _rel(found, here)
                if slot:
                    if slot == "norm":
                        lines.append("norm %s" % rel)
                        lines.append("map_Bump %s" % rel)
                    else:
                        lines.append("%s %s" % (slot, rel))
                    # альфа-карта отдельным файлом (`sc_texfix.py` пишет <имя>_a.png):
                    # у декалей альфа — маска знака, без неё панель рисуется прямоугольником
                    if slot == "map_Kd" and want_alpha:
                        amap = tex_index.get((stem + "_a").lower())
                        if amap:
                            lines.append("map_d %s" % _rel(amap, here))
                else:
                    lines.append("# %s %s  (%s)" %
                                 (SAMPLER_NOTE.get(sampler, sampler), rel, sampler))
                bound += 1
            else:
                lines.append("# %s %s  (не найдена)" %
                             (SAMPLER_NOTE.get(sampler, sampler), engine_path))
    os.makedirs(here, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(materials), bound
