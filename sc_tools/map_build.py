# -*- coding: utf-8 -*-
"""Сборка сцены карты Star Conflict в Blender: `.mdl-geo` + `.mdl-zon`.

Геометрия берётся из `.mdl-geo` (чанки), раскладка — из зон `.mdl-zon`:
дров-батч = (материал, чанк, первая вершина, число треугольников). Индексы в
чанке сквозные, поэтому батчи одного чанка склеиваются в один объект, а материал
ставится на грани.

Материал зоны — путь лайтмапы (`textures\\graylightmap`, `mapskit\\...`),
поэтому в Blender он собирается как изображение-лайтмапа.
"""
import os

import bpy

try:
    from . import map_import as MI
except ImportError:                       # запуск вне пакета (тесты)
    import map_import as MI


def game_to_blender(x, y, z, scale=1.0):
    """Игровые координаты -> Blender (как в импорте моделей: -x, z, -y)."""
    return (-x * scale, z * scale, -y * scale)


def _material_for(name, rel, resolve_tex, make_image, cache, log):
    """Материал с лайтмапой: базовая текстура + свечение."""
    key = rel.lower()
    if key in cache:
        return cache[key]
    mat = bpy.data.materials.new("MAP_" + (name or os.path.basename(rel) or "mat"))
    mat.use_nodes = True
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    img = None
    try:
        tfh = resolve_tex(rel) if resolve_tex else None
        if tfh:
            img = make_image(tfh) if make_image else None
    except Exception as exc:
        log("[MAP] текстура %r: %s" % (rel, exc))
    if img is not None:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-420, 260)
        if bsdf is not None:
            try:
                links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
                links.new(tex.outputs["Color"], bsdf.inputs["Emission Color"])
                if "Emission Strength" in bsdf.inputs:
                    bsdf.inputs["Emission Strength"].default_value = 0.6
            except Exception:
                pass
    elif bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.55, 0.55, 0.58, 1.0)
    mat["sc_map_lightmap"] = rel
    cache[key] = mat
    return mat


def build_map(context, folder, base=None, zone_indices=None, scale=1.0,
              use_lightmaps=True, resolve_tex=None, make_image=None,
              log=print):
    """Собрать карту. Возвращает статистику (dict)."""
    scan = MI.scan_folder(folder)
    if base is None:
        cands = MI.map_bases(folder)
        if not cands:
            raise RuntimeError("в папке нет пары .mdl-geo + .mdl-zon: %s" % folder)
        base = cands[0]
    rec = scan.get(base)
    if not rec or not rec["geo"]:
        raise RuntimeError("нет .mdl-geo для базы %r" % base)
    geo = MI.read_geo(rec["geo"])
    problems = geo.check()

    coll = bpy.data.collections.new("MAP " + base)
    context.scene.collection.children.link(coll)

    mat_cache = {}
    stats = dict(base=base, zones=0, objects=0, verts=0, tris=0,
                 materials=set(), problems=problems, geo=rec["geo"])
    zone_paths = rec["zones"]
    if zone_indices is not None:
        keep = set(int(i) for i in zone_indices)
        zone_paths = [p for p in zone_paths
                      if MI.read_zone(p).index in keep]

    for zp in zone_paths:
        zone = MI.read_zone(zp)
        stats["zones"] += 1
        mats = zone.materials()
        zcoll = bpy.data.collections.new("zn%d %s" % (zone.index, zone.name or base))
        coll.children.link(zcoll)

        # батчи по чанкам
        by_chunk = {}
        for b in zone.batches:
            by_chunk.setdefault(b["chunk"], []).append(b)

        for chunk_index in sorted(by_chunk):
            if not (0 <= chunk_index < len(geo.chunks)):
                log("[MAP] зона %d: чанк %d вне диапазона" % (zone.index, chunk_index))
                continue
            ch = geo.chunks[chunk_index]
            verts = geo.vertices(ch["vtx_first"], ch["n_verts"])
            faces = []
            face_mat = []
            for b in by_chunk[chunk_index]:
                ntri = b["count"]
                try:
                    idx = geo.indices(ch["idx_first"] + 3 * b["first"], 3 * ntri)
                except Exception as exc:
                    log("[MAP] индексы батча (mat=%d first=%d): %s"
                        % (b["material"], b["first"], exc))
                    continue
                for k in range(0, len(idx) - 2, 3):
                    tri = (idx[k], idx[k + 1], idx[k + 2])
                    if max(tri) >= len(verts):
                        continue
                    faces.append(tri)
                    face_mat.append(b["material"])

            if not faces:
                continue
            me = bpy.data.meshes.new("zn%d_c%d" % (zone.index, chunk_index))
            co = [game_to_blender(v[0], v[1], v[2], scale) for v in verts]
            me.from_pydata(co, [], faces)
            me.update()
            uv = me.uv_layers.new(name="UVMap")
            for loop in me.loops:
                vi = loop.vertex_index
                uv.data[loop.index].uv = (verts[vi][3], verts[vi][4])

            ob = bpy.data.objects.new("zn%d_c%d" % (zone.index, chunk_index), me)
            zcoll.objects.link(ob)

            # материалы чанка
            used = sorted(set(face_mat))
            slot_of = {}
            for mi in used:
                rel = mats[mi] if mi < len(mats) else ""
                if use_lightmaps and rel:
                    mat = _material_for(rel, rel, resolve_tex, make_image,
                                        mat_cache, log)
                else:
                    mat = _material_for("", "", None, None, mat_cache, log)
                slot_of[mi] = len(me.materials)
                me.materials.append(mat)
                stats["materials"].add(mat.name)
            for poly, mi in zip(me.polygons, face_mat):
                poly.material_index = slot_of.get(mi, 0)

            stats["objects"] += 1
            stats["verts"] += len(verts)
            stats["tris"] += len(faces)

    stats["materials"] = sorted(stats["materials"])
    return stats
