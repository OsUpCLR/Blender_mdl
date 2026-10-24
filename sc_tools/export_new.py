# -*- coding: utf-8 -*-
"""Экспорт «с нуля» из Blender: меши (любая топология), скелет, анимации."""
import os

import bpy
from mathutils import Vector, Matrix

from . import msh as MSH
from .blend_export import g_pos, g_nrm, g_quat, build_skeleton, build_animation


def _stored_vectors(me, name):
    """Векторы из файла, сохранённые при импорте (Blender-пространство)."""
    try:
        attr = me.attributes.get(name)
        if attr is not None and len(attr.data) == len(me.vertices):
            return [tuple(d.vector) for d in attr.data]
    except Exception:
        pass
    return None


def _stored_scalars(me, name):
    try:
        attr = me.attributes.get(name)
        if attr is not None and len(attr.data) == len(me.vertices):
            return [float(d.value) for d in attr.data]
    except Exception:
        pass
    return None


def _stored_ints(me, name):
    try:
        attr = me.attributes.get(name)
        if attr is not None and len(attr.data) == len(me.vertices):
            return [int(d.value) for d in attr.data]
    except Exception:
        pass
    return None


def _stored_normals(me):
    """Нормали из файла, сохранённые при импорте (Blender-пространство)."""
    return _stored_vectors(me, "sc_normals")


def _template(path, skinned=False):
    if not path or not os.path.exists(path):
        return MSH.default_template(skinned=skinned)
    return MSH.parse_file(path)


def _bone_map(obj, arm):
    """Индекс кости игры для каждой вершины (или None, если скиннинга нет)."""
    if arm is None or not obj.vertex_groups:
        return None
    names = {b.name: i for i, b in enumerate(arm.data.bones)}
    groups = {}
    for g in obj.vertex_groups:
        if g.name in names:
            groups[g.index] = names[g.name]
    if not groups:
        return None
    out = []
    for v in obj.data.vertices:
        best = None
        best_w = -1.0
        for ge in v.groups:
            if ge.group in groups and ge.weight > best_w:
                best = groups[ge.group]
                best_w = ge.weight
        out.append(best if best is not None else 0)
    return out



def _action_fcurves(action):
    """Кривые действия: старый API (Blender <=4.3) и слои (4.4+/5.x)."""
    try:
        return list(action.fcurves)
    except AttributeError:
        pass
    out = []
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            bags = getattr(strip, "channelbags", None)
            if bags is None:
                bags = []
                for slot in getattr(action, "slots", []):
                    try:
                        cb = strip.channelbag(slot)
                    except Exception:
                        cb = None
                    if cb is not None:
                        bags.append(cb)
            for cb in bags:
                out.extend(getattr(cb, "fcurves", []))
    return out

def export_hdr(mesh_paths, base, out_dir, template_path, mdf_path=None,
               lod_sizes=None, keep_template_bbox=False, material_count=None):
    """Пересобрать .mdl-hdr: bbox + счётчики mesh_count/material_count.

    mesh_count считается по фактическим файлам, material_count — по `.mdf`
    (если он передан), иначе остаётся из шаблона.
    """
    from . import hdrwrite as HW
    if not mdf_path and template_path:
        cand_mdf = template_path.replace(".mdl-hdr", ".mdf")
        if os.path.exists(cand_mdf):
            mdf_path = cand_mdf
    mat = None
    if mdf_path and os.path.exists(mdf_path):
        try:
            from . import material as MAT
            mat = len(MAT.parse_mdf(mdf_path))
        except Exception:
            mat = None
    if material_count is not None:
        mat = int(material_count)
    data = HW.build_hdr(mesh_paths, template_path or None, material_count=mat,
                        lod_sizes=lod_sizes,
                        keep_template_bbox=keep_template_bbox)
    path = os.path.join(out_dir, base + ".mdl-hdr")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _lod_copy(obj, ratio, lod):
    """Копия объекта, ужатая Decimate до ratio — для автоматического LOD."""
    me = obj.data.copy()
    ob = bpy.data.objects.new(obj.name + ".lod%d" % lod, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.matrix_world = obj.matrix_world.copy()
    for key in ("sc_src_msh", "sc_model_iex_offset", "sc_winding_reversed"):
        if key in obj:
            try:
                ob[key] = obj[key]
            except Exception:
                pass
    ob["sc_lod"] = int(lod)
    try:
        mod = ob.modifiers.new("dec", "DECIMATE")
        mod.ratio = float(ratio)
        mod.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception:
        bpy.data.objects.remove(ob, do_unlink=True)
        return None
    return ob


def _drop_lod_copies(objs):
    for ob in objs:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass


def export_meshes(objects, base, out_dir, template_path, scale=1.0, arm=None,
                  keep_normals=False, lod_levels=0, progress=None):
    """Записать .mdl-msh### по группам исходных баз.

    Части (_boost, _cap, _eng) — отдельные модели со своими .mdl-hdr/.mdf,
    поэтому каждая группа пишется своей базой.
    Возвращает {out_base: {files, lod_sizes, template}}.
    """
    tpl = _template(template_path, skinned=arm is not None)
    os.makedirs(out_dir, exist_ok=True)
    temps = []
    objects = list(objects)
    if lod_levels:
        # авто-LOD: ужимаем LOD0 и добавляем уровни 1..N
        ratios = (0.5, 0.25, 0.12, 0.06)
        lod0 = [o for o in objects if int(o.get("sc_lod", 0)) == 0]
        for lvl in range(1, int(lod_levels) + 1):
            ratio = ratios[min(lvl - 1, len(ratios) - 1)]
            for src in lod0:
                tmp = _lod_copy(src, ratio, lvl)
                if tmp is not None:
                    temps.append(tmp)
                    objects.append(tmp)
    groups = {}
    for obj in objects:
        src = obj.get("sc_src_msh") or ""
        src_base = os.path.basename(src).split(".mdl-msh")[0] if ".mdl-msh" in src else base
        groups.setdefault(src_base, []).append(obj)
    if not groups:
        return {}
    main = max(groups, key=lambda k: len(groups[k]))
    tpl_base = os.path.basename(template_path).split(".mdl-msh")[0]
    if tpl_base in groups:
        main = tpl_base
    result = {}
    for src_base, objs in groups.items():
        if src_base == main:
            out_base = base
        elif src_base.startswith(main):
            out_base = base + src_base[len(main):]
        else:
            # общая часть класса (r4_l_eng и подобные) своего имени не меняет:
            # в игре она подключается по исходному имени, общему для кораблей
            out_base = src_base
        ordered = sorted(objs, key=lambda o: (int(o.get("sc_lod", 0)), o.name))
        lod_sizes = []
        for obj in ordered:
            li = int(obj.get("sc_lod", 0))
            while len(lod_sizes) <= li:
                lod_sizes.append(0)
            lod_sizes[li] += 1
        files = _write_meshes(ordered, tpl, out_base, out_dir, scale, arm,
                              keep_normals=keep_normals, progress=progress)
        src_dir = os.path.dirname(objs[0].get("sc_src_msh") or "")
        src_hdr = os.path.join(src_dir, src_base + ".mdl-hdr")
        result[out_base] = {"files": files, "lod_sizes": lod_sizes,
                            "src_base": src_base,
                            "template": src_hdr if os.path.exists(src_hdr) else None}
    _drop_lod_copies(temps)
    return result


def _write_meshes(objects, tpl, base, out_dir, scale, arm, keep_normals=False,
                  progress=None):
    written = []
    for i, obj in enumerate(objects):
        me = obj.data
        me.calc_loop_triangles()
        mw = obj.matrix_world
        ident = (mw == Matrix.Identity(4))
        nmat = mw.to_3x3().inverted().transposed()
        uv_sets = []
        for layer in me.uv_layers:
            per_vert = [(0.0, 0.0)] * len(me.vertices)
            seen = [False] * len(me.vertices)
            for li, loop in enumerate(me.loops):
                vi = loop.vertex_index
                if not seen[vi]:
                    u, v = layer.data[li].uv
                    # при импорте V перевёрнут (1-v) — возвращаем как в файле
                    per_vert[vi] = (u, 1.0 - v)
                    seen[vi] = True
            uv_sets.append(per_vert)
        pos, nrm = [], []
        stored = _stored_normals(me)
        raw_pos = _stored_vectors(me, "sc_pos_raw")
        keep_raw_pos = False
        if raw_pos is not None:
            keep_raw_pos = True
            # деталь при импорте сдвинута на позицию кости — сравниваем
            # с учётом этого сдвига (off объявлен ниже, берём его тут)
            _off = obj.get("sc_model_iex_offset") or (0.0, 0.0, 0.0)
            for k, v in enumerate(me.vertices):
                if (abs(raw_pos[k][0] - (v.co.x - _off[0])) > 1e-6 or
                        abs(raw_pos[k][1] - (v.co.y - _off[1])) > 1e-6 or
                        abs(raw_pos[k][2] - (v.co.z - _off[2])) > 1e-6):
                    keep_raw_pos = False
                    break
        raw_bone = _stored_ints(me, "sc_bone_index")
        if (stored is not None and raw_pos is not None and not keep_raw_pos
                and not keep_normals):
            # геометрию правили — сохранённые нормали уже не соответствуют
            # форме, считаем по Blender (v.normal)
            stored = None
        off = obj.get("sc_model_iex_offset") or (0.0, 0.0, 0.0)
        ox, oy, oz = float(off[0]), float(off[1]), float(off[2])
        for vi, v in enumerate(me.vertices):
            # единичную матрицу не применяем: (1*x + 0*y + ...) превращает
            # -0.0 в +0.0 и ломает побайтовый круг
            if keep_raw_pos:
                pos.append(g_pos(raw_pos[vi]))
            else:
                p = v.co if ident else (mw @ v.co)
                pos.append(g_pos(((p.x - ox) * scale, (p.y - oy) * scale,
                                  (p.z - oz) * scale)))
            if stored is not None:
                # нормали уже единичные из файла: normalize() сдвигал младшие
                # разряды и мешал побайтовому кругу
                n = Vector(stored[vi]) if ident else (nmat @ Vector(stored[vi]))
            else:
                n = v.normal.copy() if ident else (nmat @ v.normal)
                n.normalize()
            nrm.append(g_nrm((n.x, n.y, n.z)))
        tris = []
        reversed_winding = bool(me.get("sc_winding_reversed", True))
        for t in me.loop_triangles:
            a, b, c = (me.loops[li].vertex_index for li in t.loops)
            # в файле обход CW; если импорт уже разворачивал — возвращаем,
            # иначе оставляем как есть
            tris.append((a, c, b) if reversed_winding else (a, b, c))
        tang = _stored_vectors(me, "sc_tangent")
        if tang is not None:
            signs = _stored_scalars(me, "sc_tangent_w")
            out_t = []
            for idx, t in enumerate(tang):
                v = nmat @ Vector(t)
                g = g_nrm((v.x, v.y, v.z))
                w = signs[idx] if signs is not None else 0.0
                out_t.append(tuple(g) + (w,))
            tang = out_t
        bones = raw_bone
        if bones is None:
            bones = _bone_map(obj, arm)
            if bones is not None:
                # в файле индекс кости на единицу меньше номера в арматуре
                bones = [max(0, int(b) - 1) for b in bones]
        tpl_i = tpl
        src = obj.get("sc_src_msh")
        if src and os.path.exists(src):
            try:
                tpl_i = MSH.parse_file(src)   # формат вершин — как у исходного меша
            except Exception:
                tpl_i = tpl
        rec = MSH.build_mesh(tpl_i, pos, tris, nrm, uv_sets or None, bones,
                             tangents=tang)
        path = os.path.join(out_dir, "%s.mdl-msh%03d" % (base, i))
        with open(path, "wb") as fh:
            fh.write(rec)
        written.append(path)
        if progress is not None:
            try:
                progress()
            except Exception:
                pass
    return written


def export_skeleton(arm, base, out_dir):
    """Записать .mdl-skl по арматуре (позиции/повороты/привязки из позы покоя)."""
    os.makedirs(out_dir, exist_ok=True)
    order = {b.name: i for i, b in enumerate(arm.data.bones)}
    BONE_QUATS = {}
    BONE_RAW = {}
    FLAGS = 0x00010000
    try:
        import json
        raw = arm.data.get("sc_bone_quats")
        if raw:
            BONE_QUATS = json.loads(raw)
        raw2 = arm.data.get("sc_bone_raw")
        if raw2:
            BONE_RAW = json.loads(raw2)
        FLAGS = int(arm.data.get("sc_flags") or FLAGS)
    except Exception:
        BONE_RAW = {}
    bones = []
    for b in arm.data.bones:
        head = arm.matrix_world @ b.head_local
        rot = (arm.matrix_world.to_3x3() @ b.matrix_local.to_3x3()).to_quaternion()
        if b.parent is not None:
            # в файле позиция и поворот кости — ОТНОСИТЕЛЬНО родителя
            phead = arm.matrix_world @ b.parent.head_local
            rel = head - phead
            prot = (arm.matrix_world.to_3x3()
                    @ b.parent.matrix_local.to_3x3()).to_quaternion()
            rot = prot.inverted() @ rot
        else:
            rel = head
        stored_q = BONE_QUATS.get(b.name)
        if stored_q and len(stored_q) == 4:
            # исходный кватернион из файла: Blender-ориентация кости его не
            # воспроизводит, поэтому неизменённую кость пишем как была
            quat = tuple(float(c) for c in stored_q)
        else:
            quat = g_quat((rot.w, rot.x, rot.y, rot.z))
        rec = {
            "name": b.name,
            "parent": order[b.parent.name] if b.parent else -1,
            "position": g_pos((rel.x, rel.y, rel.z)),
            "rotation": quat,
        }
        stored = BONE_RAW.get(b.name)
        if stored and stored.get("rot") and len(stored["rot"]) == 12:
            rec["rot_matrix"] = stored["rot"]
            rec["bind_matrix"] = stored.get("bind") or []
            rec["inverse_translation"] = stored.get("inv") or []
            # сырые байты: неизвестный хвост, позиция и кватернион без
            # float32 → double → float32 (иначе -0.0 и 0.99999994 теряются)
            rec["pad_raw"] = stored.get("pad") or ""
            rec["position_raw"] = stored.get("pos") or ""
            rec["quat_raw"] = stored.get("quat") or ""
        bones.append(rec)
    from . import sklwrite as SW
    data = SW.build_skl({"bones": bones, "flags": FLAGS})
    path = os.path.join(out_dir, base + ".mdl-skl")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def export_animation(arm, action, base, out_dir, fps=24.0):
    """Записать .anim из действия: каналы поз (location + rotation_quaternion)."""
    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    prev_action = arm.animation_data.action if arm.animation_data else None
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = action
    frames = set()
    for fc in _action_fcurves(action):
        for kp in fc.keyframe_points:
            frames.add(int(round(kp.co[0])))
    frames = sorted(frames) or [scene.frame_start]
    order = {b.name: i for i, b in enumerate(arm.data.bones)}
    channels = []
    for pb in arm.pose.bones:
        keys = []
        for f in frames:
            scene.frame_set(f)
            t = (f - frames[0]) / float(fps or 24.0)
            loc = g_pos((pb.location.x, pb.location.y, pb.location.z))
            q = pb.rotation_quaternion
            keys.append((t, loc, g_quat((q.w, q.x, q.y, q.z))))
        if len(keys) > 1:
            channels.append((pb.name, order.get(pb.name, 0), keys))
    data = build_animation(action.name, fps, channels)
    path = os.path.join(out_dir, "%s.anim" % base)
    with open(path, "wb") as fh:
        fh.write(data)
    if prev_action is not None:
        arm.animation_data.action = prev_action
    return path
