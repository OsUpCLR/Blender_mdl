# -*- coding: utf-8 -*-
"""Импорт OBJ-модели в игру: полный пайплайн одним вызовом.

Делает всё, что мы проходили вручную:
  импорт → нормализация осей → масштаб/центрирование → разбивка по материалам
  → материалы-паттерны ship_hull → нормали → разворот 180° → экспорт аддоном
  → текстуры DXT5 (родной размер) → .mdf с полным набором сэмплеров
  → правки .mdl-hdr → скелет и анимации из шаблона → .pak

Использование из оператора `sc_model_iex.obj_to_game` либо напрямую:
    obj_to_game(obj_path, base, template_dir, out_dir, target_len=5.5)
"""
import math
import os
import shutil
import struct

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import texture as TEX
from . import pak as PAK
from . import hdrwrite as HW
try:
    from . import dxt_encode as DXT
except Exception:                     # нет энкодера — текстуры не соберутся
    DXT = None

REL = "models/ships/race_3/l"
PATTERN = "ship_hull"
GLASS_BLOCK = (
    "material dyn_glass\n{\n"
    "\tUserParam0_Float4 ( %.3f %.3f %.3f )\n"
    "\tUserParam0_Float  ( %.2f %.2f 0 0 )\n"
    "\tUserParam1_Float4 ( 0.000611 0.00228 0.00901 )\n"
    "}")
GLASS_DEFAULT = (0.60, 0.66, 0.72, 0.50, 1.00)
SAMPLERS_FULL = True


# ------------------------------------------------------------------ текстуры
def _load_rgba(path):
    """Картинка -> (w, h, bytes RGBA), без PIL: через bpy.data.images."""
    import numpy as np
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)
    bpy.data.images.remove(img)
    arr = np.clip(px * 255.0, 0, 255).astype(np.uint8)
    arr[:, :, 3] = 255                      # принудительно непрозрачно
    return w, h, arr.tobytes()


def _downsample(arr):
    import numpy as np
    h, w, _ = arr.shape
    nw, nh = max(1, w // 2), max(1, h // 2)
    a = arr[:nh * 2, :nw * 2].astype(np.uint16)
    a = a.reshape(nh, 2, nw, 2, 4).mean(axis=(1, 3)).astype(np.uint8)
    return a


def build_pair(img, ref):
    """DXT5-пара из картинки (img = (w,h,bytes))."""
    import numpy as np
    w, h, raw = img
    arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)
    levels = []
    cur = arr
    while True:
        ch, cw, _ = cur.shape
        levels.append((cw, ch, DXT.encode(cur.tobytes(), cw, ch, "DXT5")))
        if cw == 1 and ch == 1:
            break
        cur = _downsample(cur)
    return TEX.build_pair(levels, w, h, 14, cube=False,
                          n_tfh=min(6, max(0, len(levels) - 1)),
                          h0_extra=ref["h0"], h1_extra=ref["h1"], already_packed=True)


# ------------------------------------------------------------------ сцена
def _bbox(objs):
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _jpg_of(mat, src_dir):
    name = mat.name if mat else ""
    if "-" in name:
        p = os.path.join(src_dir, name.split("-", 1)[1])
        if os.path.exists(p):
            return p
    return None


def obj_to_game(obj_path, base, template_dir, out_dir, target_len=5.5,
                install_root=None, log=print):
    """Собрать игровую модель из OBJ. Возвращает путь к .pak."""
    src_dir = os.path.dirname(os.path.abspath(obj_path))
    # в папке шаблонов может быть много моделей: берём корабль (race*_t*),
    # иначе — самый крупный по мешу
    hdrs = [f for f in os.listdir(template_dir) if f.endswith(".mdl-hdr")]
    ships = [f for f in hdrs if f.startswith("race")]
    pick = sorted(ships)[0] if ships else sorted(
        hdrs, key=lambda f: -os.path.getsize(os.path.join(template_dir, f)))[0]
    tpl_base = pick.replace(".mdl-hdr", "")
    os.makedirs(out_dir, exist_ok=True)

    # 1) импорт
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=obj_path, forward_axis="NEGATIVE_Y", up_axis="Z")
    new = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    if not new:
        raise RuntimeError("OBJ не импортировался")
    obj = max(new, key=lambda o: len(o.data.vertices))
    for o in new:
        if o is not obj:
            bpy.data.objects.remove(o, do_unlink=True)

    # 2) самая длинная ось — горизонтально (по Y)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    mn, mx = _bbox([obj])
    size = mx - mn
    if size.z >= max(size.x, size.y):
        obj.rotation_euler = (math.radians(90), 0, 0)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    elif size.x >= size.y:
        obj.rotation_euler = (0, 0, math.radians(90))
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    bpy.context.view_layer.update()

    # 3) масштаб и центрирование
    mn, mx = _bbox([obj])
    size = mx - mn
    f = float(target_len) / max(size.y, 1e-6)
    obj.scale = (f, f, f)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()
    mn, mx = _bbox([obj])
    obj.location = obj.location - (mn + mx) / 2
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    bpy.context.view_layer.update()
    mn, mx = _bbox([obj])
    log("[obj] габарит %.2f x %.2f x %.2f" % ((mx - mn).x, (mx - mn).y, (mx - mn).z))

    # 4) разбивка по материалам
    me = obj.data
    made = []
    for mi, mat in enumerate(me.materials):
        faces = [p for p in me.polygons if p.material_index == mi]
        if not faces:
            continue
        dup = bpy.data.objects.new("%s_%02d" % (base, len(made)), me.copy())
        bpy.context.scene.collection.objects.link(dup)
        bm = bmesh.new()
        bm.from_mesh(dup.data)
        rm = [fc for fc in bm.faces if fc.material_index != mi]
        bmesh.ops.delete(bm, geom=rm, context="FACES")
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(dup.data)
        bm.free()
        dup.data.update()
        dup["sc_material_index"] = len(made)
        m = bpy.data.materials.new(PATTERN)
        m.use_nodes = True
        nt = m.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        img = bpy.data.images.new("t%d" % len(made), 4, 4)
        img.filepath = "%s\\%s_m%02d" % (REL.replace("/", "\\"), base, len(made))
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        dup.data.materials.clear()
        dup.data.materials.append(m)
        for p in dup.data.polygons:
            p.material_index = 0
        made.append({"obj": dup, "jpg": _jpg_of(mat, src_dir),
                     "glass": ("glass" in (mat.name if mat else "").lower())})
        # 5) разворот 180° (как в игре)
        bpy.context.view_layer.objects.active = dup
        dup.select_set(True)
        dup.matrix_world = Matrix.Rotation(math.radians(180), 4, "Z") @ dup.matrix_world
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        log("[obj] материал %d: граней %d, jpg=%s"
            % (mi, len(dup.data.polygons),
               os.path.basename(made[-1]["jpg"]) if made[-1]["jpg"] else "-"))
    bpy.data.objects.remove(obj, do_unlink=True)
    if not made:
        raise RuntimeError("нет материалов с гранями")

    # 6) экспорт аддоном
    bpy.ops.sc_model_iex.export_sc(
        base=base, out_dir=out_dir,
        template=os.path.join(template_dir, tpl_base + ".mdl-msh001"),
        hdr_template=os.path.join(template_dir, tpl_base + ".mdl-hdr"),
        write_meshes=True, gen_lod=False, write_model=True, write_mdp=True,
        write_skl=False, write_anim=False, write_mdf=False, pack=False)

    # 7) текстуры + mdf
    ref = TEX.parse_tfh(open(os.path.join(template_dir, tpl_base + "_d.tfh"), "rb").read())
    blocks = []
    for i, m in enumerate(made):
        tex = "%s_m%02d" % (base, i)
        body = ""
        if m["jpg"]:
            img = _load_rgba(m["jpg"])
            tfh, tfd = build_pair(img, ref)
            open(os.path.join(out_dir, tex + ".tfh"), "wb").write(tfh)
            open(os.path.join(out_dir, tex + ".tfd"), "wb").write(tfd)
            body = ('\tColormapSampler "%s\\%s_mask"\n'
                    '\tDiffuse2Sampler "%s\\%s"\n'
                    '\tDiffuseSampler "%s\\%s"\n'
                    '\tNormalSampler "%s\\%s_nm"\n'
                    '\tSpecularColorSampler "%s\\%s_msk_sc"\n'
                    % ((REL.replace("/", "\\"), base) + (REL.replace("/", "\\"), tex) * 2
                       + (REL.replace("/", "\\"), tpl_base) * 2))
        if m.get("glass"):
            blocks.append(GLASS_BLOCK % GLASS_DEFAULT)
        else:
            blocks.append('material %s\n{\n%s\tUserParam2_Float4 ( 1 1 0 0 )\n'
                          '\tpins\n\t{\n\t\tUser1 1\n\t}\n}' % (PATTERN, body))
    # чёрная маска покраски
    import numpy as np
    mask = (64, 64, np.zeros((64, 64, 4), dtype=np.uint8).tobytes())
    tfh, tfd = build_pair(mask, ref)
    open(os.path.join(out_dir, "%s_mask.tfh" % base), "wb").write(tfh)
    open(os.path.join(out_dir, "%s_mask.tfd" % base), "wb").write(tfd)
    open(os.path.join(out_dir, base + ".mdf"), "wb").write(("\n\n".join(blocks) + "\n").encode("latin1"))

    # 8) .mdl-hdr: счётчики и нулевые LOD
    meshes = sorted(f for f in os.listdir(out_dir) if f.startswith(base + ".mdl-msh"))
    h = bytearray(open(os.path.join(out_dir, base + ".mdl-hdr"), "rb").read())
    struct.pack_into("<4H", h, 0x20, 0, 0, 0, 0)
    struct.pack_into("<3f", h, 0x28, 0.0, 0.0, 0.0)
    struct.pack_into("<I", h, 0x38, (len(blocks) << 16) | len(meshes))
    open(os.path.join(out_dir, base + ".mdl-hdr"), "wb").write(bytes(h))

    # 9) скелет и анимации из шаблона
    shutil.copy2(os.path.join(template_dir, tpl_base + ".mdl-skl"),
                 os.path.join(out_dir, base + ".mdl-skl"))
    for a in os.listdir(template_dir):
        if a.endswith(".anim") and a.startswith(tpl_base):
            shutil.copy2(os.path.join(template_dir, a),
                         os.path.join(out_dir, a.replace(tpl_base, base, 1)))

    # 10) пак
    pak_path = os.path.join(out_dir, base + ".pak")
    stage = os.path.join(out_dir, "_pak")
    dst = os.path.join(stage, *REL.split("/"))
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(out_dir):
        if os.path.isfile(os.path.join(out_dir, f)) and not f.endswith(".pak"):
            shutil.copy2(os.path.join(out_dir, f), os.path.join(dst, f))
    PAK.pack_dir(stage, pak_path)
    log("[obj] пак: %s (%d Б)" % (pak_path, os.path.getsize(pak_path)))

    if install_root:
        target_dir = install_root
        shutil.copy2(pak_path, os.path.join(
            target_dir, "models_ships_race_3_l_%s.pak" % base))
        log("[obj] установлено в %s" % target_dir)
    return pak_path
