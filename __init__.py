# -*- coding: utf-8 -*-
"""
SC Model IEX — Blender add-on for importing Star Conflict models and textures.

Works directly with the game formats, without intermediate OBJ/FBX:
    .mdl-hdr + .mdl-msh###  ->  geometry (vertices, normals, UVs, indices)
    .mdf                    ->  materials (textures per slot)
    .tfh + .tfd             ->  textures (unpacked to DDS by sc_tools)

Installation:
    1. Blender -> Edit -> Preferences -> Add-ons -> Install...
       choose the folder or zip with this add-on (the __init__.py file inside the folder).
    2. Enable the "SC Model IEX" add-on.
    3. In the add-on preferences set the path to SC_MODEL_IEX (the folder with sc_tools) and the path to data.

Usage:
    File -> Import -> Star Conflict model (.mdl-hdr)  — choose a .mdl-hdr
    or  File -> Import -> Star Conflict model from .pak — choose a .pak
    The model is assembled from all .mdl-msh### files next to the chosen .mdl-hdr.

Why not via OBJ: OBJ loses part of the data (for example separate UV sets
and material order) and also requires writing files to disk. Here the geometry
goes into bmesh directly, materials are created in Blender, textures are packed
into the .blend.
"""

bl_info = {
    "name": "SC Model IEX",
    "author": "Star Conflict SDK",
    "version": (0, 4, 8),
    "blender": (4, 2, 0),
    "location": "File > Import > Star Conflict model",
    "description": "Star Conflict model import/export (full set: parts, decals, paints)",
    "category": "Import-Export",
}

import os
import sys
import json
import re
import tempfile
import zlib

import bpy

def _L(s):
    """UI text in the language of Blender (Russian when the UI is Russian)."""
    try:
        from .sc_tools.i18n_ui import L as _tr
        return _tr(s)
    except Exception:
        return s
from mathutils import Matrix, Vector
from bpy.props import StringProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper


# --------------------------------------------------------------------------
# access to the sc_tools library
# --------------------------------------------------------------------------

LOG_NAME = "SC_MODEL_IEX_log"

# Build tag: printed to the log first of all. Needed to see at once
# which code version actually runs in Blender (a common trap — the add-on
# was reinstalled, but an old module stayed in memory).
ADDON_BUILD = "build 57 / coincident mount bones deduplicated (one copy per head+rotation); parts placed on their own bones (each copy re-homed, rotation about bone head, no double offset); single parts path: index (parts.py canon) is primary, folder scan only as fallback; bind = translation + 3x3 rotation; textures are taken directly from the game .pak files (no index or unpacked folder required); portable Steam detection; bilingual FAQ; language-aware UI; collisions 2/6/7, 224"

FAQ_TEXT = """
SC Model IEX — frequently asked questions
==========================

INSTALLATION
1. Add-ons -> Install -> the SC_MODEL_IEX folder (or zip).
2. Enable "SC Model IEX".
3. Add-on preferences: "Game root (data)" = ...\\star conflict\\data
   (it contains gamedata/def/ex/ship.lua, ship_dyes.lua, ship_armors.lua).
   "Game paks" = textures are extracted from the game .pak files automatically (no unpacked folder needed)
   are taken from there, for example armor and crystals).
   sc_tools is already bundled with the add-on — nothing to specify.

IMPORT
* Model: File -> Import -> Star Conflict model (.mdl-hdr).
* From a pak: File -> Import -> Star Conflict model from .pak.
* LOD: -1 = all levels, 0 = the most detailed one.
* Parts (_eng/_cap/_boost) are assembled with a checkbox; the skeleton with a separate one.
* Paint: _d — albedo, _s1 — MASKS of three layers (R/G/B), the layer color comes
  from the ship def (default_custom_elements -> ShipDyes.albedo).
* Details: the "Details" list — _L("All parts + binding to bones") (default),
  _L("All parts without binding"), _L("This model only (no parts)"). The pak import
  also has a "Parts from other paks" list: _L("Load them") / _L("Do not load them").
* Armor (dark metal on details): "Armor pattern (ShipArmors)", for example
  Crossout_Armor02; off by default, because in the game the pattern
  is chosen per-instance and is not present in the files.
* Elydium crystals: Race5 ships have _s1 in DXT5, the alpha is the crystal mask.
  The add-on writes "crystals: N%" to the log and sets obj["sc_model_iex_crystals"].
  IMPORTANT: the crystal color is NOT from the texture (per-instance UserParam4), so
  visually the crystals are currently drawn as regular paint.

MATERIALS (as in object.fx)
Diffuse — _d; _glow — glow; _nm — DXT5nm (A=X, G=Y, R=AO); _sc — specular,
alpha = gloss; _pdo — shading (UV2); _msk — G=AO, B=gloss.
Dye colors are converted from sRGB to linear — otherwise the hull gets lighter
while unpainted details stay black.

BLENDER 5.2 SPECIFICS
* Custom normals from the file are not set — Blender computes its own.
* _nm/_sc/_pdo are loaded as Non-Color.
* The scene is NOT cleared: before importing, delete the default cube
  (select_all -> delete), otherwise it gets into the frame.
* The "top, nose up" view (Numpad 7): +X is to the right, matching the game.

EXPORT
* Modes: folder (files), new_pak (archive), "append/append with overwrite".
* Round trip import -> export without edits: 54/54 files byte-identical.
* Parts from other paks (Phoenix: capacitor in misc.pak, boosters in
  models_ships_race_4_l.pak) do NOT go into the hull pak (otherwise "2 or more
  identical copies"). Their edits are saved with the "Rebuild the paks of parts
  from other paks" checkbox (updates their pak with a .bak), otherwise the log tells where the part is.
* Deploy: close the game -> back up the original .pak -> put the new pak
  UNDER THE ORIGINAL NAME -> launch. To roll back — restore the backup.

COMMON PROBLEMS
* "texture not found" — check "Game root" and "Unpacked tree".
* The model is dark — that is paint/lighting; enable "Lighting like in the game".
* In the game the edit is on the other side — the old behavior before the mirror fix;
  the current version mirrors the axes like the game (verified on _boost).
* Animations rotate the wrong way — report it, we will fix the quaternion sign.

COLLISIONS (.mdp)
* type 6 (shell) — the basis of statics and all ships: ships have 8–16 vertices,
  usually one body (solid.model);
* type 7 (trimesh) — small details: median 16 triangles, maximum 224;
  on large objects it gives a "porous" collision;
* type 2 (box) — rarely, built from the bounds;
* solid.model requires EXACTLY one body, compound_model — more than one
  (there is a "Body per object" checkbox for it);
* the type 7 limit is 224 triangles: 512+ crashes the physics server;
* the .mdp is placed into the same pak where the model lives; a duplicate file in two paks
  gives "2 or more identical files";
* the TCF container is written automatically (chunk table of type 9), the shape header
  and the BVH are taken from the .mdp template;
* the trimesh is one-sided in places — for walls and large
  objects use type 6.

The "FAQ" BUTTON is available here and in the import/export dialogs.
"""


# object name -> list of bone indices per vertex (usage 2 from .mdl-msh)
PART_PLACE_DETAILS = True   # ставить детали в точку кости (галочка в импорте)
_MESH_BONE_IDX = {}
# object name -> list of influences per vertex [(bone, weight), …] (usage 2 + usage 1)
_MESH_INFLUENCES = {}


_LOG_FILE = os.path.join(tempfile.gettempdir(), "SC_MODEL_IEX_log.txt")


def _log_to_file(msg):
    """Дублировать строку лога в файл — чтобы диагностировать падения без Blender."""
    global _LOG_FILE
    try:
        if not _LOG_FILE:
            return
        with open(_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(str(msg) + "\n")
    except Exception:
        pass


def _log(text):
    "Write to the console AND to the SC_MODEL_IEX_log text block (visible in the Text Editor)."
    print(text)
    try:
        _log_to_file(text)
    except Exception:
        pass
    try:
        txt = bpy.data.texts.get(LOG_NAME)
        if txt is None:
            txt = bpy.data.texts.new(LOG_NAME)
        txt.write(text + "\n")
    except Exception:
        pass


def _log_reset():
    "Clear the log before a new import."
    try:
        if _LOG_FILE and os.path.exists(_LOG_FILE):
            os.remove(_LOG_FILE)
    except Exception:
        pass
    _MESH_BONE_IDX.clear()
    _MESH_INFLUENCES.clear()
    print("[SC_MODEL_IEX] add-on build: %s" % ADDON_BUILD)
    try:
        txt = bpy.data.texts.get(LOG_NAME)
        if txt is None:
            txt = bpy.data.texts.new(LOG_NAME)
        txt.clear()
        txt.write("""[SC_MODEL_IEX] import log (add-on build: %s)
""" % ADDON_BUILD)
    except Exception:
        pass


def _addon_prefs(context):
    return context.preferences.addons[__name__].preferences


def _auto_game_root():
    "Find the game data folder if the user did not specify it."
    cands = []
    env = os.environ.get("STARCONFLICT_DATA") or os.environ.get("SC_DATA")
    if env:
        cands.append(env)
    # Steam: install path from the registry, libraries from libraryfolders.vdf
    try:
        import winreg
        for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Valve\\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    steam = winreg.QueryValueEx(k, "SteamPath")[0]
            except Exception:
                continue
            steam = os.path.normpath(steam)
            libs = [steam]
            vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf):
                try:
                    txt = open(vdf, encoding="utf-8", errors="replace").read()
                    for m in re.finditer(r'"path"\\s+"([^"]+)"', txt):
                        p = m.group(1).replace("\\\\", "\\")
                        if p not in libs:
                            libs.append(p)
                except Exception:
                    pass
            for lib in libs:
                cands.append(os.path.join(lib, "steamapps", "common", "star conflict", "data"))
                cands.append(os.path.join(lib, "steamapps", "common", "Star Conflict", "data"))
    except Exception:
        pass
    cands += [r"C:\\Program Files (x86)\\Steam\\steamapps\\common\\star conflict\\data",
              r"C:\\Program Files\\Steam\\steamapps\\common\\star conflict\\data"]
    for cand in cands:
        if cand and os.path.isdir(cand):
            return cand
    return ""


def _game_root(prefs):
    "Game data folder: from the preferences, otherwise auto-detected."
    if getattr(prefs, "game_root", ""):
        return bpy.path.abspath(prefs.game_root)
    return _auto_game_root()


def _aux_file(prefs, attr, rel):
    "Path to the data file: from the preferences, otherwise relative to the game data."
    val = getattr(prefs, attr, "")
    if val:
        return bpy.path.abspath(val)
    root = _game_root(prefs)
    return os.path.join(root, rel) if root else None


def _sdk_root():
    "Folder with the bundled SDK (sc_tools sits next to the add-on __init__.py)."
    here = os.path.dirname(os.path.abspath(__file__))
    return here if os.path.isdir(os.path.join(here, "sc_tools")) else ""


def _ensure_sc_tools(context):
    "Import sc_tools (bundled with the add-on or from the preferences)."
    root = _sdk_root()
    if root and root not in sys.path:
        sys.path.insert(0, root)
    try:
        from sc_tools import model as M           # noqa: F401
        from sc_tools import material as MAT      # noqa: F401
        from sc_tools import texture as TEX       # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            """failed to load sc_tools (%r): %s
Check that the sc_tools folder is next to the add-on __init__.py."""
            % (root, exc))
    return M, MAT, TEX


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------

def _texture_cache_dir(context):
    prefs = _addon_prefs(context)
    path = bpy.path.abspath(prefs.cache_dir) if prefs.cache_dir else ""
    if not path:
        path = os.path.join(tempfile.gettempdir(), "sc_import_cache")
    os.makedirs(path, exist_ok=True)
    return path


def _load_texture(context, TEX, tfh_path, cache_dir):
    "Unpack .tfh/.tfd into DDS and return the file path (or None)."
    try:
        out = TEX.convert(tfh_path, cache_dir, fmt="dds")
        if isinstance(out, (list, tuple)):
            out = out[0] if out else None
        if out and os.path.exists(str(out)):
            return str(out)
    except Exception as exc:
        _log("[SC_MODEL_IEX] texture %s not unpacked: %s" % (tfh_path, exc))
    return None


def _make_image(context, TEX, tfh_path, cache_dir, pack=True):
    dds = _load_texture(context, TEX, tfh_path, cache_dir)
    if not dds:
        return None
    name = os.path.splitext(os.path.basename(tfh_path))[0]
    try:
        img = bpy.data.images.load(dds, check_existing=True)
        img.name = name
        if pack:
            try:
                img.pack()
            except Exception:
                pass
        return img
    except Exception as exc:
        _log("[SC_MODEL_IEX] Blender did not open %s: %s" % (dds, exc))
        return None


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------

TEXTURE_SLOTS = (
    # (key in mdf, role in the nodes)
    ("ColormapSampler", "colormap"),   # _s1: paint layer masks
    ("DiffuseSampler", "diffuse"),     # _d: albedo
    ("Diffuse2Sampler", "glow"),       # _glow: glow
    ("NormalSampler", "normal"),       # _nm: normals
    ("SpecularColorSampler", "specular"),  # _sc: specular (alpha = gloss)
    ("LightmapSampler", "pdo"),        # _pdo: shading (PD_OCCL)
    ("AmbOcclSampler", "msk"),         # _msk: R=height, G=AO, B=gloss
)


def _use_uv_layer(nodes, links, tex_node, uv_name):
    """Point a specific UV set at the image node.

    In Blender 5.2 ShaderNodeTexImage has no uv_map property — the set is chosen
    by a separate ShaderNodeUVMap node connected to the Vector input.
    """
    try:
        uvn = nodes.new("ShaderNodeUVMap")
        uvn.uv_map = uv_name
        uvn.location = (tex_node.location[0] - 260, tex_node.location[1])
        links.new(uvn.outputs["UV"], tex_node.inputs["Vector"])
        return True
    except Exception as exc:
        _log("[SC_MODEL_IEX] UV set %s not applied: %s" % (uv_name, exc))
        return False


def _material_name(mdef, fallback):
    for key in ("name", "Name", "material"):
        if isinstance(mdef, dict) and mdef.get(key):
            return str(mdef[key])
    return fallback


def _material_textures(mdef):
    """Get texture paths from the material description.

    The real structure from sc_tools.material.load_materials:
        {'name': 'sky', 'index': 0, 'samplers': {'DiffuseSampler': 'path'}}
    """
    out = {}
    if not isinstance(mdef, dict):
        return out
    samplers = mdef.get("samplers")
    if isinstance(samplers, dict):
        for key, value in samplers.items():
            if isinstance(value, str):
                out[str(key)] = value
    for key, value in mdef.items():
        if isinstance(key, str) and key.lower().endswith("sampler") and isinstance(value, str):
            out.setdefault(key, value)
    return out


_TEX_INDEX = {}
_INDEX = {}


def _load_index(context):
    """Read the common model and texture index (sc_index.json).

    The index is built by SC_MODEL_IEX\\build_texture_index.py: it knows which .pak holds
    every model and every texture, so the plug-in does not need to walk the game
    folder or unpack whole paks.
    """
    prefs = _addon_prefs(context)
    path = bpy.path.abspath(prefs.index_path) if getattr(prefs, "index_path", "") else ""
    if not path or not os.path.exists(path):
        return None
    if _INDEX.get("path") == path:
        return _INDEX.get("data")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        _log("[SC_MODEL_IEX] index not read: %s" % exc)
        return None
    _INDEX["path"] = path
    _INDEX["data"] = data
    _log("[SC_MODEL_IEX] index: models %d, textures %d"
          % (len(data.get("models") or {}), len(data.get("textures") or {})))
    return data


def _inflate(raw):
    try:
        return zlib.decompress(raw)
    except Exception:
        pass
    try:
        return zlib.decompressobj(-15).decompress(raw)
    except Exception:
        return raw


def _pak_extract(pak_path, wanted, out_dir):
    """Extract only the needed files from the pak. Returns {name: path on disk}.

    The data is taken via resolve_paths() — the same way as pak.unpack_file. Assembling
    a file from chunks by the field2/index fields is not allowed: field1 is the name offset,
    not the chunk count, so wrong files were read (verified: the .mdf came out
    1.4 MB instead of 1665 B).
    """
    from sc_tools import pak as P
    result = {}
    try:
        it = P.parse(pak_path)
        items = P.core().resolve_paths(it, None)
    except Exception as exc:
        _log("[SC_MODEL_IEX] pak not parsed %s: %s" % (os.path.basename(pak_path), exc))
        return result
    # names — by field1 (payload offset), see FORMATS_SESSION/16
    raw = it.get("names_raw") or b""
    offs, i, rec = {}, 0, 0
    while i + 4 <= len(raw):
        n = int.from_bytes(raw[i:i + 4], "little")
        if not (0 < n <= 65536 and i + 4 + n <= len(raw)):
            break
        buf = bytearray(raw[i + 4:i + 4 + n])
        for j in range(n):
            buf[j] ^= P.key_byte(j, n, rec)
        offs[i + 4] = bytes(buf).decode("latin1")
        i += 4 + n + 1
        rec += 1
    files = it.get("files") or []
    want_low = {w.lower(): w for w in wanted}
    for idx, item in enumerate(items):
        payload, rel, blob, _nidx = item
        f = files[idx] if idx < len(files) else {}
        name = offs.get(f.get("field1"), rel)
        if name.lower() not in want_low:
            continue
        dst = os.path.join(out_dir, name.replace("\\", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(blob)
        result[name] = dst
    return result


def _texture_index(game_root):
    "Texture index by file name, built once and cached."
    if not game_root or not os.path.isdir(game_root):
        return {}
    if _TEX_INDEX.get("root") == game_root:
        return _TEX_INDEX.get("map") or {}
    mapping = {}
    for dirpath, _dirs, files in os.walk(game_root):
        for name in files:
            low = name.lower()
            if low.endswith((".tfh", ".dds", ".png")):
                mapping.setdefault(low, os.path.join(dirpath, name))
    _TEX_INDEX["root"] = game_root
    _TEX_INDEX["map"] = mapping
    _log("[SC_MODEL_IEX] texture index: %d files in %s" % (len(mapping), game_root))
    return mapping


_PAK_NAMES = {}
_TEX_FOUND = {}
_TEX_MISS = set()


def _pak_names(pak_path):
    """Cached list of file names inside a .pak."""
    try:
        key = (pak_path, os.path.getmtime(pak_path))
    except OSError:
        key = (pak_path, 0)
    cached = _PAK_NAMES.get(key)
    if cached is not None:
        return cached
    names = []
    try:
        from sc_tools import pak as P
        data = P.parse(pak_path)
        names = P.decode_names_raw(data.get("names_raw") or b"")
        if not names:
            names = [n.decode("latin1") if isinstance(n, bytes) else str(n)
                     for n in (data.get("names") or [])]
    except Exception as exc:
        _log("[SC_MODEL_IEX] pak names not read %s: %s" % (os.path.basename(pak_path), exc))
    _PAK_NAMES.clear()
    _PAK_NAMES[key] = names
    return names


def _texture_from_paks(game_root, rel, cache_dir):
    """Find a texture (.tfh+.tfd) inside the game paks and extract it."""
    if not game_root or not cache_dir or not os.path.isdir(game_root):
        return None
    cache_key = (game_root, rel.lower())
    if cache_key in _TEX_FOUND:
        return _TEX_FOUND[cache_key]
    if cache_key in _TEX_MISS:
        return None
    want = rel.replace("/", "\\").strip("\\").lower()
    wanted_names = (want + ".tfh", want + ".tfd")
    try:
        all_paks = [f for f in os.listdir(game_root) if f.lower().endswith(".pak")]
    except OSError:
        return None
    # приоритет: пак, имя которого выведено из пути текстуры
    parts = [x for x in rel.replace("/", "\\").split("\\") if x]
    prefix = "_".join(parts[:-1]).lower()
    leaf = parts[-2].lower() if len(parts) > 1 else ""
    def rank(nm):
        low = nm.lower()
        if prefix and low.startswith(prefix):
            return 0
        if leaf and leaf in low:
            return 1
        if low.startswith(("textures", "models_")):
            return 2
        return 3
    paks = sorted(all_paks, key=lambda nm: (rank(nm), nm))
    for name in paks:
        pak_path = os.path.join(game_root, name)
        names = _pak_names(pak_path)
        if not names:
            continue
        low = {n.lower().replace("/", "\\"): n for n in names}
        hit = low.get(wanted_names[0])
        if not hit:
            continue
        wanted = [hit]
        tfd = low.get(wanted_names[1])
        if tfd:
            wanted.append(tfd)
        got = _pak_extract(pak_path, wanted, cache_dir)
        for nm, path in got.items():
            if nm.lower().endswith(".tfh"):
                _log("[SC_MODEL_IEX] texture from pak %s: %s" % (name, nm))
                _TEX_FOUND[cache_key] = path
                return path
    _TEX_MISS.add(cache_key)
    return None


def _resolve_texture(tex_dir, rel, game_root, index, index_data=None, cache_dir=None):
    """Find the .tfh by the path from the mdf.

    Order: the sc_index.json index (the pak is known right away) -> the model folder ->
    the game root -> the file name -> the common index by name.
    """
    stem = rel.replace("\\", "/").strip("/")
    bare = os.path.basename(stem)
    rel_os = stem.replace("/", os.sep)
    exts = (".tfh", ".dds", ".png")

    # 0) index: we know the pak with the texture at once, pull only it
    if index_data and cache_dir:
        texs = index_data.get("textures") or {}
        entry = texs.get(rel) or texs.get(stem.replace("/", "\\"))
        if not entry and index_data.get("textures_by_name"):
            real = index_data["textures_by_name"].get(bare.lower())
            if real:
                entry = texs.get(real)
                if entry:
                    rel = real
                    stem = real.replace("\\", "/").strip("/")
        if entry and entry.get("pak"):
            data_dir = index_data.get("game_data") or ""
            pak_path = os.path.join(data_dir, entry["pak"])
            if os.path.exists(pak_path):
                # names inside the pak use BACKSLASHES; a path may arrive here
                # with forward slashes too (from lua), so we normalize
                pak_rel = stem.replace("/", "\\")
                wanted = [pak_rel + ".tfh"]
                if entry.get("tfd"):
                    wanted.append(pak_rel + ".tfd")
                got = _pak_extract(pak_path, wanted, cache_dir)
                hit = got.get(pak_rel + ".tfh")
                if not hit:
                    for name, path_hit in got.items():
                        if name.lower().endswith(".tfh"):
                            hit = path_hit
                            break
                if hit:
                    return hit

    # 0b) без индекса: достать текстуру прямо из паков игры
    if cache_dir and game_root:
        hit = _texture_from_paks(game_root, stem, cache_dir)
        if hit:
            return hit
    cands = []
    for ext in exts:
        cands.append(os.path.join(tex_dir, rel_os + ext))
    if game_root:
        for ext in exts:
            cands.append(os.path.join(game_root, rel_os + ext))
    for ext in exts:
        cands.append(os.path.join(tex_dir, bare + ext))
    for cand in cands:
        if os.path.exists(cand):
            return cand
    for ext in exts:
        hit = index.get((bare + ext).lower())
        if hit:
            return hit
    return None


def _ship_def_name(base):
    "race1_l_t5_sniper -> Ship_Race1_L_T5_Sniper (the ship def name)."
    parts = []
    for p in (base or "").split("_"):
        if not p:
            continue
        if p[0].isdigit():
            parts.append(p.capitalize())
        elif len(p) == 1:
            parts.append(p.upper())
        elif p[0] == "t" and p[1:].isdigit():
            parts.append("T" + p[1:])
        else:
            parts.append(p.capitalize())
    return "Ship_" + "_".join(parts)


def _srgb_to_linear(v):
    "sRGB 0..1 -> linear value (Blender treats colors as linear)."
    v = max(0.0, min(1.0, float(v)))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _color_linear(rgb):
    "Color from 0..255 (as in the game data) -> a linear tuple."
    return tuple(_srgb_to_linear(c / 255.0) for c in rgb[:3])


def _default_paint(context, base):
    "Default colors of the three paint layers (from the ship def and ship_dyes.lua)."
    prefs = _addon_prefs(context)
    root = _game_root(prefs)
    if not root:
        return None
    ship = os.path.join(root, "gamedata", "def", "ex", "ship.lua")
    dyes = os.path.join(root, "gamedata", "ship_dyes.lua")
    if not (os.path.isfile(ship) and os.path.isfile(dyes)):
        return None
    try:
        text = open(ship, "rb").read().decode("latin1", "replace")
        defname = _ship_def_name(base)
        pat = re.compile(r"Def\.%s\.default_custom_elements\[ai\.VesselCustomSlot\.MATERIAL_(\d)\]"
                         r"=\"([^\"]+)\"" % re.escape(defname))
        names = {int(m.group(1)): m.group(2) for m in pat.finditer(text)}
        if not names:
            # parts (_eng/_cap/_boost) may have no def of their own — take the ship
            stem = re.sub(r"_(Eng|Cap|Boost|Drone)$", "", defname, flags=re.I)
            if stem != defname:
                pat = re.compile(r"Def\.%s\.default_custom_elements\[ai\.VesselCustomSlot\.MATERIAL_(\d)\]"
                                 r"=\"([^\"]+)\"" % re.escape(stem))
                names = {int(m.group(1)): m.group(2) for m in pat.finditer(text)}
        if not names:
            # the model may be used by another def (e.g. a premium ship):
            # look for Def.<name>.model="...<base>" and take the paint from it
            stem_base = re.sub(r"_(Eng|Cap|Boost|Drone)$", "", os.path.basename(base), flags=re.I)
            mp = re.compile(r'Def\.(\w+)\.model\s*=\s*"([^"]*%s)"' % re.escape(stem_base))
            for m in mp.finditer(text):
                other = m.group(1)
                pat2 = re.compile(r"Def\.%s\.default_custom_elements\[ai\.VesselCustomSlot\.MATERIAL_(\d)\]"
                                  r"=\"([^\"]+)\"" % re.escape(other))
                found = {int(x.group(1)): x.group(2) for x in pat2.finditer(text)}
                if found:
                    names = found
                    _log("[SC_MODEL_IEX] paint: def by model -> %s" % other)
                    break
        if not names:
            _log("[SC_MODEL_IEX] paint: def %s not found" % defname)
            return None
        from sc_tools import dyes as D
        table = D.parse_ship_dyes(dyes)
    except Exception as exc:
        _log("[SC_MODEL_IEX] paint not parsed: %s" % exc)
        return None
    cols = []
    for i in range(3):
        ent = None
        if names.get(i):
            _key, ent = D.find_dye(table, names[i])
        if ent and ent.get("albedo"):
            cols.append(_color_linear(ent["albedo"]))
        else:
            cols.append(None)
    _log("[SC_MODEL_IEX] default paint (linear): %s" % [None if c is None else tuple(round(v, 4) for v in c) for c in cols])
    return cols


def _unpacked_root(prefs):
    "Root of the unpacked paks: preferences, SC_UNPACKED env, otherwise empty."
    val = getattr(prefs, "unpacked_root", "")
    if val:
        return bpy.path.abspath(val)
    return os.environ.get("SC_UNPACKED") or os.environ.get("SC_UNPACKED_ROOT") or ""


def _armor_entry(context, name):
    "ShipArmors entry by name: texture, tiling1/2, gloss, spec, saturation2."
    if not name:
        return None
    prefs = _addon_prefs(context)
    root = _game_root(prefs)
    if not root:
        return None
    path = os.path.join(root, "gamedata", "ship_armors.lua")
    if not os.path.isfile(path):
        return None
    try:
        text = open(path, "rb").read().decode("latin1", "replace")
    except Exception:
        return None

    def _parse(body, key):
        ent = {"name": key}
        t = re.search(r'texture\s*=\s*"([^"]+)"', body)
        if not t:
            return None
        ent["texture"] = t.group(1)
        for k in ("tiling1", "tiling2", "gloss", "saturation2"):
            q = re.search(r"%s\s*=\s*([-\d.eE+]+)" % k, body)
            if q:
                ent[k] = float(q.group(1))
        sp = re.search(r'spec\s*=\s*"([^"]+)"', body)
        if sp:
            try:
                ent["spec"] = _color_linear([float(x) for x in sp.group(1).split()[:3]])
            except ValueError:
                pass
        return ent

    # exact record name or texture name (armor02 -> Crossout_Armor02)
    entries = re.finditer(r"^\t(\w+)\s*=\s*\{(.*?)^\t\}", text, re.S | re.M)
    low = name.lower()
    fallback = None
    for it in entries:
        key, body = it.group(1), it.group(2)
        if key.lower() == low:
            return _parse(body, key)
        if fallback is None:
            t = re.search(r'texture\s*=\s*"([^"]+)"', body)
            if t and t.group(1).rsplit("/", 1)[-1].lower() == low:
                fallback = _parse(body, key)
    return fallback


def _build_material(context, MAT, TEX, base_name, mdef, tex_dir, cache_dir,
                    index, game_root=None, tex_index=None, index_data=None,
                    dye=None, dye_layer=0, paint_mode="dye", decal=None,
                    brightness=1.0, paint=None, armor=None):
    mat = bpy.data.materials.new(_material_name(mdef, "%s_%02d" % (base_name, index)))
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        for node in nodes:
            if node.type == "BSDF_PRINCIPLED":
                bsdf = node
                break

    texs = _material_textures(mdef)
    params = mdef.get("params") or {}
    _mname = (mdef.get("name") or "").strip().lower()
    if _mname in ("animated_mock", "dyn_animated_mock") or _mname.startswith("animated_mock"):
        _p1 = params.get("UserParam1_Float4") or ()
        _p2 = params.get("UserParam2_Float4") or ()
        _p4 = params.get("UserParam4_Float4") or ()
        _scroll = _p2[2] if len(_p2) > 2 else 0.0
        _flip = (_p1[0] if len(_p1) > 0 else 0.0, _p1[1] if len(_p1) > 1 else 0.0)
        _log("[SC_MODEL_IEX] material %s: ANIMATED texture (UV animation is not imported). V scroll: %s; UV offset: %s; flipbook: %s"
             % (mdef.get("name"),
                ("%.3f" % _scroll) if _scroll else "none",
                ("(%.2f, %.2f, %.2f, %.2f)" % (tuple(_p4) + (0.0,) * 4)[:4]) if _p4 else "none",
                "off" if (_flip[0] >= 1e5 or _flip[1] == 0.0) else ("%.0f frames" % _flip[0])))
    bound = 0
    tex_nodes = {}
    for key, role in TEXTURE_SLOTS:
        rel = texs.get(key) or texs.get(key.lower())
        if not rel:
            continue
        tfh = _resolve_texture(tex_dir, rel, game_root, tex_index or {},
                               index_data=index_data, cache_dir=cache_dir)
        if not tfh:
            _root = _unpacked_root(_addon_prefs(context))
            if _root:
                _cand = os.path.join(_root, rel.replace("\\", os.sep).replace("/", os.sep) + ".tfh")
                if os.path.isfile(_cand):
                    tfh = _cand
        if not tfh:
            _log("[SC_MODEL_IEX] texture not found: %s (slot %s)" % (rel, key))
            continue
        img = _make_image(context, TEX, tfh, cache_dir)
        if not img:
            continue
        if role == "colormap" and hasattr(TEX, "alpha_mask_stats"):
            try:
                st = TEX.alpha_mask_stats(tfh, tfh[:-4] + ".tfd")
                if st and st.get("frac", 0.0) > 0.02:
                    mat["sc_model_iex_crystals"] = float(st["frac"])
                    _log("[SC_MODEL_IEX] crystals in %s: %.1f%% of the mask (color NOT from the texture — per-instance UserParam4)" % (mdef.get("name"), st["frac"] * 100.0))
            except Exception as exc:
                _log("[SC_MODEL_IEX] crystal mask not checked: %s" % exc)
        if role in ("normal", "specular"):
            try:
                img.colorspace_settings.name = "Non-Color"
            except Exception:
                pass
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.location = (-700, 300 - 250 * len(tex_nodes))
        tex_nodes[role] = tex

    # Base Color: _d is the albedo. _s1 (ColormapSampler) is NOT a color but masks
    # of three paint layers in R/G/B + the crystal mask in alpha (see ship_hull.mmp).
    # So the albedo must not be multiplied by _s1: for Gungnir this gave a dark orange
    # hull instead of a gray one.
    diffuse = tex_nodes.get("diffuse")
    colormap = tex_nodes.get("colormap")
    base_socket = None
    if diffuse:
        base_socket = diffuse.outputs["Color"]

    # Dye: the color from ship_dyes.lua is applied BY THE LAYER MASK from _s1.
    if paint_mode == "dye" and dye and colormap and base_socket and bsdf:
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.location = (-520, 60)
        links.new(colormap.outputs["Color"], sep.inputs["Color"])
        channel = ("Red", "Green", "Blue")[max(0, min(2, dye_layer))]
        tint = _color_linear(dye.get("albedo") or (128, 128, 128))
        paint = nodes.new("ShaderNodeMix")
        paint.data_type = "RGBA"
        paint.blend_type = "MIX"
        paint.location = (-300, 300)
        try:
            links.new(sep.outputs[channel], paint.inputs["Factor"])
            links.new(base_socket, paint.inputs[6])
            paint.inputs[7].default_value = (tint[0], tint[1], tint[2], 1.0)
            base_socket = paint.outputs[2]
            _log("[SC_MODEL_IEX] dye %s to layer %d (r=%d g=%d b=%d)"
                  % (dye.get("name", "?"), dye_layer,
                     tint[0] * 255, tint[1] * 255, tint[2] * 255))
        except Exception as exc:
            _log("[SC_MODEL_IEX] dye not applied: %s" % exc)

    if base_socket and bsdf:
        # Brightness: in the game _d and _s1 are modulated by light and dye, so in
        # Blender the base color comes out dark. The multiplier raises it.
        if brightness and abs(brightness - 1.0) > 1e-3:
            hs = nodes.new("ShaderNodeHueSaturation")
            hs.location = (-120, 300)
            try:
                hs.inputs["Value"].default_value = brightness
                links.new(base_socket, hs.inputs["Color"])
                base_socket = hs.outputs["Color"]
            except Exception as exc:
                _log("[SC_MODEL_IEX] brightness not applied: %s" % exc)
        links.new(base_socket, bsdf.inputs["Base Color"])
    elif bsdf:
        # Material without textures (glass, some effects): color from the .mdf parameters
        params = mdef.get("params") or {}
        col = params.get("UserParam0_Float4")
        if col and len(col) >= 3:
            bsdf.inputs["Base Color"].default_value = (col[0], col[1], col[2], 1.0)

    # Alpha: only for "transparent" materials (signs, decals, glass).
    # For the hull (_d DXT3) alpha is not transparency, it must not be connected.
    low = (mdef.get("name") or "").lower()
    if diffuse and bsdf and "Alpha" in bsdf.inputs and (
            "decal" in low or "sign" in low or "glass" in low):
        try:
            if getattr(diffuse.image, "channels", 4) == 4:
                links.new(diffuse.outputs["Alpha"], bsdf.inputs["Alpha"])
                try:
                    mat.surface_render_method = "BLENDED"
                except Exception:
                    try:
                        mat.blend_method = "BLEND"
                    except Exception:
                        pass
        except Exception:
            pass

    # Paint like in the shader (object.fx:435-455):
    #   dyedAlbedo = Σ color_i * mask_i;  dyeInfluence = saturate(Σ mask_i);
    #   albedo = lerp(_d, dyedAlbedo, dyeInfluence)
    if paint and colormap and diffuse and bsdf and paint_mode != "none":
        try:
            sep = nodes.new("ShaderNodeSeparateColor")
            sep.location = (-620, 160)
            links.new(colormap.outputs["Color"], sep.inputs["Color"])

            acc = None
            for i, col in enumerate(paint[:3]):
                if not col:
                    continue
                sc = nodes.new("ShaderNodeVectorMath")
                sc.operation = "SCALE"
                sc.location = (-440, 200 - i * 45)
                sc.inputs[0].default_value = (col[0], col[1], col[2])
                links.new(sep.outputs[i], sc.inputs["Scale"])
                if acc is None:
                    acc = sc.outputs[0]
                else:
                    add = nodes.new("ShaderNodeVectorMath")
                    add.operation = "ADD"
                    add.location = (-300, 200 - i * 45)
                    links.new(acc, add.inputs[0])
                    links.new(sc.outputs[0], add.inputs[1])
                    acc = add.outputs[0]

            if acc is not None:
                a1 = nodes.new("ShaderNodeMath")
                a1.operation = "ADD"
                a1.location = (-440, 20)
                links.new(sep.outputs[0], a1.inputs[0])
                links.new(sep.outputs[1], a1.inputs[1])
                a2 = nodes.new("ShaderNodeMath")
                a2.operation = "ADD"
                a2.use_clamp = True
                a2.location = (-300, 20)
                links.new(a1.outputs[0], a2.inputs[0])
                links.new(sep.outputs[2], a2.inputs[1])

                mix = nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MIX"
                mix.location = (-140, 160)
                links.new(a2.outputs[0], mix.inputs["Factor"])
                links.new(diffuse.outputs["Color"], mix.inputs[6])
                links.new(acc, mix.inputs[7])
                base_socket = mix.outputs[2]
                links.new(base_socket, bsdf.inputs["Base Color"])
        except Exception as exc:
            _log("[SC_MODEL_IEX] paint not connected: %s" % exc)

    # Shading: three sources, like in object.fx —
    #   _nm.R  (FetchBumpOccl.z), _msk.G (masks.g), _pdo.R (globalOcclusion, UV2).
    # In Blender we multiply onto Base Color with soft weights, otherwise it is too dark.
    normal_node = tex_nodes.get("normal")
    msk_node = tex_nodes.get("msk")
    pdo = tex_nodes.get("pdo")
    ao_sources = []
    if normal_node is not None:
        try:
            sepn = nodes.new("ShaderNodeSeparateColor")
            sepn.location = (-760, -300)
            links.new(normal_node.outputs["Color"], sepn.inputs["Color"])
            ao_sources.append((sepn.outputs[0], 0.5, "nm.R"))
        except Exception:
            pass
    if msk_node is not None:
        try:
            sepm = nodes.new("ShaderNodeSeparateColor")
            sepm.location = (-760, -380)
            links.new(msk_node.outputs["Color"], sepm.inputs["Color"])
            ao_sources.append((sepm.outputs[1], 0.5, "msk.G"))
        except Exception:
            pass
    if pdo is not None:
        try:
            _use_uv_layer(nodes, links, pdo, "UV2")
            sepp = nodes.new("ShaderNodeSeparateColor")
            sepp.location = (-760, -460)
            links.new(pdo.outputs["Color"], sepp.inputs["Color"])
            ao_sources.append((sepp.outputs[0], 0.3, "pdo.R"))
        except Exception:
            pass
    for src, strength, tag in ao_sources:
        if base_socket is None or bsdf is None:
            break
        try:
            occ = nodes.new("ShaderNodeMix")
            occ.data_type = "RGBA"
            occ.blend_type = "MULTIPLY"
            occ.location = (-320, -300)
            occ.inputs["Factor"].default_value = strength
            links.new(base_socket, occ.inputs[6])
            links.new(src, occ.inputs[7])
            base_socket = occ.outputs[2]
            links.new(base_socket, bsdf.inputs["Base Color"])
        except Exception as exc:
            _log("[SC_MODEL_IEX] AO %s not connected: %s" % (tag, exc))

    # Armor (object.fx:461-485): Detail2Sampler pattern, tiling = UserParam2.xy*armorTiling;
    # the armor color/gloss go into SPECULAR by the albedo.a * armorMask mask.
    armor_gate = None
    if armor and diffuse and bsdf and paint_mode != "none":
        try:
            tfh = _resolve_texture(tex_dir, armor["texture"], game_root, tex_index or {},
                                   index_data=index_data, cache_dir=cache_dir)
            if not tfh:
                root = _unpacked_root(_addon_prefs(context))
                if root:
                    cand = os.path.join(root, armor["texture"].replace("\\", "/") + ".tfh")
                    if os.path.isfile(cand):
                        tfh = cand
            img = _make_image(context, TEX, tfh, cache_dir) if tfh else None
            if img:
                uv = nodes.new("ShaderNodeUVMap")
                uv.uv_map = "UVMap"
                uv.location = (-700, -420)
                mp = nodes.new("ShaderNodeMapping")
                mp.location = (-560, -420)
                txy = params.get("UserParam2_Float4") or (1.0, 1.0, 0.0, 0.0)
                t1 = armor.get("tiling1", 1.0)
                t2 = armor.get("tiling2", 1.0)
                mp.inputs["Scale"].default_value = (txy[0] * t1, txy[1] * t2, 1.0)
                links.new(uv.outputs["UV"], mp.inputs["Vector"])
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (-380, -420)
                links.new(mp.outputs["Vector"], tex.inputs["Vector"])
                sepA = nodes.new("ShaderNodeSeparateColor")
                sepA.location = (-180, -420)
                links.new(tex.outputs["Color"], sepA.inputs["Color"])
                sat2 = nodes.new("ShaderNodeMath")
                sat2.operation = "MULTIPLY"
                sat2.location = (-20, -470)
                sat2.inputs[1].default_value = armor.get("saturation2", 0.0)
                links.new(sepA.outputs[2], sat2.inputs[0])
                mask = nodes.new("ShaderNodeMath")
                mask.operation = "SUBTRACT"
                mask.use_clamp = True
                mask.location = (140, -420)
                links.new(sepA.outputs[1], mask.inputs[0])
                links.new(sat2.outputs[0], mask.inputs[1])
                gate = nodes.new("ShaderNodeMath")
                gate.operation = "MULTIPLY"
                gate.use_clamp = True
                gate.location = (300, -420)
                links.new(diffuse.outputs["Alpha"], gate.inputs[0])
                links.new(mask.outputs[0], gate.inputs[1])
                armor_gate = gate.outputs[0]
                _log("[SC_MODEL_IEX] armor: %s (tiling %.2f/%.2f)" % (armor["name"], t1, t2))
        except Exception as exc:
            _log("[SC_MODEL_IEX] armor not connected: %s" % exc)

    # Glass: color and transparency from UserParam0
    if "glass" in low and bsdf:
        params = mdef.get("params") or {}
        a = params.get("UserParam0_Float")
        if a:
            bsdf.inputs["Alpha"].default_value = max(0.0, min(1.0, a[0]))
        try:
            mat.surface_render_method = "BLENDED"
        except Exception:
            pass
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.08

    glow = tex_nodes.get("glow")
    if glow and bsdf and "Emission Color" in bsdf.inputs:
        links.new(glow.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = 1.0

    normal = tex_nodes.get("normal")
    if normal and bsdf:
        try:
            # _nm — DXT5nm: A = X, G = Y, R = AO. Into Normal Map we feed
            # the encoded [0,1] X/Y values, and restore Z.
            sepn2 = nodes.new("ShaderNodeSeparateColor")
            sepn2.location = (-620, -80)
            links.new(normal.outputs["Color"], sepn2.inputs["Color"])

            def _m(op, a=None, b=None, loc=(0, 0), label=""):
                n = nodes.new("ShaderNodeMath")
                n.operation = op
                n.location = loc
                if label:
                    n.label = label
                if a is not None:
                    links.new(a, n.inputs[0])
                if b is not None:
                    if isinstance(b, (int, float)):
                        n.inputs[1].default_value = b
                    else:
                        links.new(b, n.inputs[1])
                return n

            ax = _m("MULTIPLY", normal.outputs["Alpha"], 2.0, (-440, -40), "X*2")
            ax2 = _m("SUBTRACT", ax.outputs[0], 1.0, (-300, -40), "X-1")
            sqx = _m("MULTIPLY", ax2.outputs[0], ax2.outputs[0], (-160, -40), "X^2")
            gy = _m("MULTIPLY", sepn2.outputs[1], 2.0, (-440, -140), "Y*2")
            gy2 = _m("SUBTRACT", gy.outputs[0], 1.0, (-300, -140), "Y-1")
            sqy = _m("MULTIPLY", gy2.outputs[0], gy2.outputs[0], (-160, -140), "Y^2")
            s = _m("ADD", sqx.outputs[0], sqy.outputs[0], (0, -100), "X^2+Y^2")
            one = _m("SUBTRACT", None, None, (140, -100), "1-...")
            one.inputs[0].default_value = 1.0
            links.new(s.outputs[0], one.inputs[1])
            one.use_clamp = True
            z = _m("SQRT", one.outputs[0], None, (280, -100), "Z")
            zp = _m("ADD", z.outputs[0], 1.0, (420, -100), "Z+1")
            zenc = _m("MULTIPLY", zp.outputs[0], 0.5, (560, -100), "Zenc")

            comb = nodes.new("ShaderNodeCombineColor")
            comb.location = (700, -80)
            links.new(normal.outputs["Alpha"], comb.inputs["Red"])
            links.new(sepn2.outputs[1], comb.inputs["Green"])
            links.new(zenc.outputs[0], comb.inputs["Blue"])
            nrm = nodes.new("ShaderNodeNormalMap")
            nrm.location = (860, -80)
            links.new(comb.outputs["Color"], nrm.inputs["Color"])
            links.new(nrm.outputs["Normal"], bsdf.inputs["Normal"])
        except Exception as exc:
            _log("[SC_MODEL_IEX] DXT5nm normals not connected: %s" % exc)

    spec = tex_nodes.get("specular")
    if spec and bsdf:
        try:
            if armor_gate is not None and armor and armor.get("spec"):
                ms = nodes.new("ShaderNodeMix")
                ms.data_type = "RGBA"
                ms.blend_type = "MIX"
                ms.location = (-120, -240)
                links.new(armor_gate, ms.inputs["Factor"])
                links.new(spec.outputs["Color"], ms.inputs[6])
                c = armor["spec"]
                ms.inputs[7].default_value = (c[0], c[1], c[2], 1.0)
                spec_socket = ms.outputs[2]
            else:
                spec_socket = spec.outputs["Color"]
            for name in ("Specular Tint", "Specular IOR Level"):
                if name in bsdf.inputs:
                    links.new(spec_socket, bsdf.inputs[name])
                    break
        except Exception as exc:
            _log("[SC_MODEL_IEX] specular not connected: %s" % exc)

    # Roughness: in the game gloss = _msk.B (if present) otherwise _sc.a;
    # roughness = 1 − gloss; with armor gloss is mixed by the mask.
    msk_gloss = None
    if msk_node is not None:
        try:
            sepg = nodes.new("ShaderNodeSeparateColor")
            sepg.location = (-520, -560)
            links.new(msk_node.outputs["Color"], sepg.inputs["Color"])
            msk_gloss = sepg.outputs[2]
        except Exception:
            msk_gloss = None
    if bsdf and "Roughness" in bsdf.inputs and (msk_gloss is not None or spec):
        try:
            gloss_out = msk_gloss if msk_gloss is not None else spec.outputs["Alpha"]
            if armor_gate is not None and armor:
                gm = nodes.new("ShaderNodeMix")
                gm.data_type = "FLOAT"
                gm.location = (-120, -300)
                links.new(armor_gate, gm.inputs["Factor"])
                links.new(gloss_out, gm.inputs[2])
                gm.inputs[3].default_value = armor.get("gloss", 100.0) / 255.0
                gloss_out = gm.outputs[0]
            inv = nodes.new("ShaderNodeMath")
            inv.operation = "SUBTRACT"
            inv.location = (40, -260)
            inv.inputs[0].default_value = 1.0
            links.new(gloss_out, inv.inputs[1])
            links.new(inv.outputs["Value"], bsdf.inputs["Roughness"])
        except Exception as exc:
            _log("[SC_MODEL_IEX] roughness not connected: %s" % exc)

    # Decals (emblems on the hull): their albedo comes NOT from the .mdf but from
    # ship_decals.lua, and is sampled with the THIRD UV set (VTEXCOORD2 in the shader).
    if decal and bsdf and "decal" in mat.name.lower():
        albedo = decal.get("albedo")
        if albedo:
            tfh = _resolve_texture(tex_dir, albedo, game_root, tex_index or {},
                                   index_data=index_data, cache_dir=cache_dir)
            img = _make_image(context, TEX, tfh, cache_dir) if tfh else None
            if img:
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (300, 300)
                _use_uv_layer(nodes, links, tex, "UV3")
                links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
                _log("[SC_MODEL_IEX] decal %s: %s (UV3)" % (decal.get("name", "?"), os.path.basename(tfh)))
            else:
                _log("[SC_MODEL_IEX] decal texture not found: %s" % albedo)
        bump = decal.get("bump")
        if bump:
            tfh = _resolve_texture(tex_dir, bump, game_root, tex_index or {},
                                   index_data=index_data, cache_dir=cache_dir)
            img = _make_image(context, TEX, tfh, cache_dir) if tfh else None
            if img:
                tex = nodes.new("ShaderNodeTexImage")
                tex.image = img
                tex.location = (300, 0)
                _use_uv_layer(nodes, links, tex, "UV3")
                nrm2 = nodes.new("ShaderNodeNormalMap")
                nrm2.location = (520, 0)
                links.new(tex.outputs["Color"], nrm2.inputs["Color"])
                links.new(nrm2.outputs["Normal"], bsdf.inputs["Normal"])

    # Animated "mock" materials (glowing stripes over the hull):
    # the detail is transparent, emission = scroll, alpha = mask.
    if "animated_mock" in _mname and bsdf:
        try:
            scroll = tex_nodes.get("colormap")
            mask = tex_nodes.get("diffuse")
            if scroll is not None:
                bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
                for nm, val in (("Roughness", 1.0), ("Specular IOR Level", 0.0),
                                ("Metallic", 0.0)):
                    if nm in bsdf.inputs:
                        try:
                            bsdf.inputs[nm].default_value = val
                        except Exception:
                            pass
                if "Emission Color" in bsdf.inputs:
                    p0 = params.get("UserParam0_Float4") or (0.0,)
                    speed = float(p0[0]) if p0 else 0.0
                    gain = nodes.new("ShaderNodeMix")
                    gain.data_type = "RGBA"
                    gain.blend_type = "MULTIPLY"
                    gain.location = (-420, 420)
                    gain.inputs["Factor"].default_value = 1.0
                    gain.inputs[7].default_value = (12.0, 12.0, 12.0, 1.0)
                    links.new(scroll.outputs["Color"], gain.inputs[6])
                    links.new(gain.outputs[2], bsdf.inputs["Emission Color"])
                    bsdf.inputs["Emission Strength"].default_value = 4.0
                # alpha: mask (otherwise the transparent detail will not appear)
                if mask is not None and "Alpha" in bsdf.inputs:
                    links.new(mask.outputs["Color"], bsdf.inputs["Alpha"])
                try:
                    mat.surface_render_method = "BLENDED"
                except Exception:
                    try:
                        mat.blend_method = "BLEND"
                    except Exception:
                        pass
                try:
                    mat.use_backface_culling = False
                except Exception:
                    pass
                # UV animation (scroll) is disabled: the layer is static only.
                _log("[SC_MODEL_IEX] animated_mock %s: transparent layer, UV animation disabled"
                     % mdef.get("name"))
        except Exception as exc:
            _log("[SC_MODEL_IEX] animated_mock not built: %s" % exc)

    return mat, len(tex_nodes)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def _transform_positions(positions, axes):
    """Convert vertices from the game system into the Blender system.

    In SC the Y axis points up, Z goes along the hull (ship bbox: length along Z,
    height along Y). In Blender Z points up and forward is -Y.
        yup: (x, y, z) -> (x, -z, y)   — correct orientation
        raw: unchanged
    """
    if axes == "raw":
        return [tuple(p[:3]) for p in positions]
    out = []
    for p in positions:
        x, y, z = p[0], p[1], p[2]
        out.append((-x, -z, y))
    return out


def _msh_to_mesh(M, msh_path, name, axes="yup", MAT=None, mats=None, flip_v=True):
    "Read .mdl-msh### and create a Blender object."
    info = M.read_msh(msh_path)
    positions = info.get("positions") or []
    indices = info.get("indices") or []
    if not positions:
        return None, 0, 0
    verts = _transform_positions(positions, axes)

    # Vertices in the file are wound clockwise (as in DirectX), Blender expects
    # the opposite. Verified by measuring the signed volume: for ALL Confessor meshes it is
    # negative (-1.02, -14.86, -0.32 …), i.e. the faces are flipped and the normals
    # point inward. So we reverse the index order: this is not a mirror
    # (the matrix det stays +1) but a winding fix.
    tris = [tuple(indices[i:i + 3]) for i in range(0, len(indices) - 2, 3)]
    vol = 0.0
    for tri in tris[:2000]:
        try:
            a = verts[tri[0]]
            b = verts[tri[1]]
            c = verts[tri[2]]
        except IndexError:
            continue
        vol += (a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0]))
    if vol < 0.0:
        tris = [(t[0], t[2], t[1]) for t in tris]

    mesh = bpy.data.meshes.new(name)
    # we remember whether the winding was reversed: on export the original must be restored
    mesh["sc_winding_reversed"] = bool(vol < 0.0)
    mesh.from_pydata(verts, [], tris)
    mesh.update()

    # UV: in the file they are per vertex, while foreach_set expects a value per LOOP,
    # so we expand by loop.vertex_index. Without this Blender reports
    # "internal error setting the array" and the mesh stays without UVs.
    # There can be several UV sets: the emblems on the hull are sampled with
    # the THIRD set (VTEXCOORD2 in the game shader), so we read them all.
    uv_sets = []
    total_uv = info.get("uv_count") or 1
    for k in range(max(1, total_uv)):
        if k == 0:
            layer_uvs = info.get("uvs") or info.get("uv")
        else:
            try:
                layer_uvs = M.read_msh(msh_path, uv_index=k).get("uvs")
            except Exception:
                layer_uvs = None
        if layer_uvs and len(layer_uvs) == len(positions):
            uv_sets.append((k, layer_uvs))

    for k, layer_uvs in uv_sets:
        layer = mesh.uv_layers.new(name=_L("UVMap") if k == 0 else "UV%d" % (k + 1))
        flat = []
        for loop in mesh.loops:
            uv = layer_uvs[loop.vertex_index]
            v = float(uv[1])
            flat.extend((float(uv[0]), (1.0 - v) if flip_v else v))
        try:
            layer.data.foreach_set("uv", flat)
        except Exception as exc:
            _log("[SC_MODEL_IEX] UV set %d not applied: %s" % (k, exc))

    if not uv_sets:
        _log("[SC_MODEL_IEX] no UV (uv_count=%s, verts=%d)" % (total_uv, len(positions)))

    # Normals: we do not set custom ones — in Blender 5.2 that call crashes
    # (EXCEPTION_ACCESS_VIOLATION in mesh_normals_corner_custom_set).
    try:
        # positions as in the file (in the Blender system) — on export we return them
        # bit-exactly if the vertices were not moved (otherwise -0.0 and ulp are lost)
        flat = []
        for pos in positions:
            flat.extend((-float(pos[0]), -float(pos[2]), float(pos[1])))
        attr = mesh.attributes.get("sc_pos_raw") or mesh.attributes.new(
            "sc_pos_raw", "FLOAT_VECTOR", "POINT")
        attr.data.foreach_set("vector", flat)
    except Exception as exc:
        _log("[SC_MODEL_IEX] raw positions not saved: %s" % exc)
    try:
        bidx = info.get("bone_index") or []
        if len(bidx) == len(positions):
            attr = mesh.attributes.get("sc_bone_index") or mesh.attributes.new(
                "sc_bone_index", "INT", "POINT")
            attr.data.foreach_set("value", [int(v) for v in bidx])
    except Exception as exc:
        _log("[SC_MODEL_IEX] raw bone index not saved: %s" % exc)
    tangents = info.get("tangents")
    if tangents and len(tangents) == len(positions):
        try:
            flat = []
            signs = []
            for t in tangents:
                flat.extend((-float(t[0]), -float(t[2]), float(t[1])))
                signs.append(float(t[3]) if len(t) > 3 else 0.0)
            attr = mesh.attributes.get("sc_tangent")
            if attr is None:
                attr = mesh.attributes.new("sc_tangent", "FLOAT_VECTOR", "POINT")
            attr.data.foreach_set("vector", flat)
            watt = mesh.attributes.get("sc_tangent_w")
            if watt is None:
                watt = mesh.attributes.new("sc_tangent_w", "FLOAT", "POINT")
            watt.data.foreach_set("value", signs)
            _log("[SC_MODEL_IEX] tangents saved as the sc_tangent attribute (%d)"
                 % len(tangents))
        except Exception as exc:
            _log("[SC_MODEL_IEX] tangents not saved: %s" % exc)

    normals = info.get("normals")
    if normals and len(normals) == len(positions):
        # In Blender 5.2 custom normals cannot be set (the call crashes), so
        # we store them as an attribute: an export "from scratch" takes them, not the
        # Blender recomputation — otherwise the file after the import->export round does not match.
        try:
            flat = []
            for n in normals:
                flat.extend((-float(n[0]), -float(n[2]), float(n[1])))
            attr = mesh.attributes.get("sc_normals")
            if attr is None:
                attr = mesh.attributes.new("sc_normals", "FLOAT_VECTOR", "POINT")
            attr.data.foreach_set("vector", flat)
            _log("[SC_MODEL_IEX] normals saved as the sc_normals attribute (%d)"
                 % len(normals))
        except Exception as exc:
            _log("[SC_MODEL_IEX] normals not saved: %s" % exc)
    mesh.shade_smooth()

    # Material indices per polygon: materials are added BEFORE setting
    # the indices, otherwise Blender may discard out-of-range values.
    for mat in (mats or []):
        mesh.materials.append(mat)
    if MAT is not None:
        try:
            pmat = MAT.material_index(msh_path)
        except Exception as exc:
            pmat = None
            _log("[SC_MODEL_IEX] material indices not obtained: %s" % exc)
        if pmat is not None:
            if isinstance(pmat, (list, tuple)) and len(pmat) == len(mesh.polygons):
                try:
                    mesh.polygons.foreach_set("material_index", list(pmat))
                except Exception as exc:
                    _log("[SC_MODEL_IEX] material indices not applied: %s" % exc)
            elif isinstance(pmat, int):
                mesh.polygons.foreach_set("material_index",
                                          [pmat] * len(mesh.polygons))
            else:
                _log("[SC_MODEL_IEX] unexpected material index format: %s"
                      % type(pmat).__name__)

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    _crys = 0.0
    for _m in (mats or []):
        try:
            _crys = max(_crys, float(_m.get("sc_model_iex_crystals", 0.0) or 0.0))
        except Exception:
            pass
    if _crys > 0.0:
        obj["sc_model_iex_crystals"] = _crys
    # Skinning: usage element 2 is UP TO FOUR bone byte-indices, while the weights
    # live in usage element 1 (4 × u16, normalized to 32767). Format 21 has no
    # weight element — it has one bone per vertex and weight 1.0 (InfluenceBones 1),
    # format 22 may have InfluenceBones 3.
    bidx = info.get("bone_index") or []
    if len(bidx) == len(verts):
        _MESH_BONE_IDX[obj.name] = [int(v) for v in bidx]
    infl = info.get("bone_influences") or []
    if len(infl) == len(verts):
        _MESH_INFLUENCES[obj.name] = infl
    return obj, len(verts), len(mesh.polygons)


def _base_of(path):
    """Drop the extension of any model file to get the base.

    The user may choose .mdl-hdr, .mdf, .model, .skins or the
    .mdl-msh### itself — in all cases the base is the same.
    """
    low = path.lower()
    for ext in (".mdf", ".mdl-hdr", ".model", ".skins", ".sot", ".anim"):
        if low.endswith(ext):
            return path[: -len(ext)]
    marker = ".mdl-msh"
    if marker in low:
        return path[: low.index(marker)]
    return os.path.splitext(path)[0]


def _find_meshes(M, base):
    "Find .mdl-msh### by the base, with a fallback search in the folder and above."
    meshes = M.mesh_files(base)
    if meshes:
        return meshes
    folder = os.path.dirname(base)
    for probe in (folder, os.path.dirname(folder)):
        if not probe or not os.path.isdir(probe):
            continue
        try:
            found = M.find_models(probe)
        except Exception:
            found = []
        for item in found:
            if os.path.basename(item).lower().startswith(os.path.basename(base).lower()):
                meshes = M.mesh_files(item)
                if meshes:
                    return meshes
    return meshes


def _resolve_dye(context, name, layer):
    "Find a dye in ship_dyes.lua by name."
    if not name:
        return None, layer
    try:
        from sc_tools import dyes as D
    except Exception as exc:
        _log("[SC_MODEL_IEX] dyes module unavailable: %s" % exc)
        return None, layer
    prefs = _addon_prefs(context)
    path = _aux_file(prefs, "dyes_path", r"gamedata\ship_dyes.lua")
    try:
        table = D.parse_ship_dyes(path)
    except Exception as exc:
        _log("[SC_MODEL_IEX] ship_dyes.lua not read: %s" % exc)
        return None, layer
    key, dye = D.find_dye(table, name)
    if not dye:
        _log("[SC_MODEL_IEX] dye %r not found (%d total)" % (name, len(table)))
        return None, layer
    _log("[SC_MODEL_IEX] tint: %s" % key)
    return dye, layer


def _resolve_decal(context, name):
    "Find a decal in ship_decals.lua."
    if not name:
        return None
    try:
        from sc_tools import decals as DEC
    except Exception as exc:
        _log("[SC_MODEL_IEX] decals module unavailable: %s" % exc)
        return None
    prefs = _addon_prefs(context)
    path = _aux_file(prefs, "decals_path", r"gamedata\ship_decals.lua")
    try:
        table = DEC.parse_ship_decals(path)
        key, entry = DEC.find_ship_decal(table, name)
    except Exception as exc:
        _log("[SC_MODEL_IEX] ship_decals.lua not parsed: %s" % exc)
        return None
    if not entry:
        _log("[SC_MODEL_IEX] decal %r not found (%d total)" % (name, len(table)))
        return None
    _log("[SC_MODEL_IEX] decal: %s" % key)
    return entry


PART_BONES = {
    "eng": "engine",
    "engine": "engine",
    "cap": "capacitor",
    "capacitor": "capacitor",
    "boost": "boost_1",
    "rocket": "rocket",
    "hangar": "hangar",
}

# --- Part binding: an algorithm, without tables for specific objects ----------
# Ship parts are separate bases (`<model>_eng`, `_cap`, `_boost`, `_dmg`…),
# and each has its OWN `.mdl-skl`. Which main-skeleton bone moves it is visible
# from the root bone of the part skeleton:
#
#   part root      -> category -> main-skeleton bone
#   root_engine      engine        engine_1      (prefix match)
#   root_eng         eng           engine_1
#   root_boost       boost         boost_1
#   root_cap         cap           cap / capacitor
#   __World__        —             NONE -> the part is static (mounted on main)
#
# Verified against all 837 skeletons in the data: roots are `root`, `root_engine`,
# `root_eng`, `root_boost`, `root_boost_l/r/1..5`, `root_cap`, `root_capacitor`,
# `root_capasitor`, `__World__`, `root1`, `root_ship`, `root_hull` and so on.
# For Gungnir this gives exactly the behavior seen in the game: `_eng` moves
# (engine_1), `_boost` and `_cap` stand still (`boost_1` without channels and `__World__`).
PART_ROOT_CATEGORIES = {
    # spelling variants occurring in the data
    "eng": "engine",
    "engine": "engine",
    "capasitor": "capacitor",
}


def _part_category(part_sk):
    "Binding category from the root bone of the part skeleton."
    if not part_sk:
        return ""
    bones = [b for b in (part_sk.get("bones") or []) if isinstance(b, dict)]
    root = next((b for b in bones if b.get("parent", -1) < 0), None)
    if root is None:
        return ""
    name = (root.get("name") or "").lower()
    if not name.startswith("root"):
        return ""            # __World__, life4 and others -> a static part
    cat = name[4:].lstrip("_")
    # drop the trailing digits and sides: boost_l, boost1, boost2
    cat = cat.rstrip("0123456789").rstrip("_")
    if cat in ("l", "r", "left", "right"):
        cat = ""
    return PART_ROOT_CATEGORIES.get(cat, cat)


def _bone_for_category(sk_info, category):
    "Main-skeleton bone for the category: exact name first, then the prefix."
    if not category or not sk_info:
        return None
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    low = category.lower()
    for b in bones:
        if (b.get("name") or "").lower() == low:
            return b
    for b in bones:
        if (b.get("name") or "").lower().startswith(low):
            return b
    return None


# Ship parts live in DIFFERENT paks and are often named differently from the hull:
#   hull    race4_l_phoenix        -> models_ships_race_4_l_race4_l_phoenix.pak
#   engines r4_l_phoenix_boost     -> models_ships_race_4_l.pak        (another pak!)
#   reactor race4_l_phoenix_capacitor -> misc.pak
# Therefore, when importing from a pak, the paks of the neighboring parts must be loaded into the cache.
PART_SUFFIXES = ("_boost", "_boosts", "_eng", "_engine", "_cap", "_capacitor",
                 "_capasitor", "_damage", "_wreck", "_drone_orbit", "_drone",
                 "_hull", "_cab", "_part")

# these bases are NOT loaded as ship parts: a drone, debris and the "damaged"
# variants are separate objects, they would only clutter the assembly
SKIP_SIBLINGS = ("_drone", "_drone_orbit", "_wreck", "_damage")


def _is_part_name(stem):
    "This is the name of a SEPARATE PART, not of the hull: `..._capacitor`, `..._eng` and so on."
    low = (stem or "").lower()
    return any(low.endswith(s) for s in PART_SUFFIXES)


def _strip_part_suffix(stem):
    low = (stem or "").lower()
    for suf in sorted(PART_SUFFIXES, key=len, reverse=True):
        if low.endswith(suf):
            return stem[:-len(suf)]
    return stem


def _category_from_name(name):
    """Part category by its NAME — a fallback path when the skeleton was not read.

    Without this, `_bind_rigid` with an empty category hung the part on the `main` bone,
    and the Phoenix reactor got a `main` group (530 vertices), although by meaning
    it is a reactor and it should hang on `capacitor_1`.
    """
    low = (name or "").lower().split(".mdl-msh")[0]
    for suf, cat in (("_capacitor", "cap"), ("_capasitor", "cap"),
                     ("_boosts", "boost"), ("_boost", "boost"),
                     ("_engine", "engine"), ("_eng", "engine")):
        if low.endswith(suf):
            return cat
    return ""


def _ship_tokens(stem):
    "Significant trailing name tokens: for r4_l_phoenix and race4_l_phoenix these are l/phoenix."
    base = _strip_part_suffix(stem or "")
    toks = [t for t in base.split("_") if t]
    return toks[-2:] if len(toks) >= 2 else toks


def _same_ship(stem_a, stem_b):
    "Whether this is the same ship model (accounting for different abbreviations in the name)."
    ta, tb = _ship_tokens(stem_a), _ship_tokens(stem_b)
    return bool(ta) and ta == tb


def _model_index_file(context):
    try:
        prefs = _addon_prefs(context)
        return getattr(prefs, "index_path", "") or ""
    except Exception:
        return ""


def _game_data_file(context, rel_path):
    "Path to a game file by name (the pak lives in <game_root>)."
    try:
        prefs = _addon_prefs(context)
        root = bpy.path.abspath(getattr(prefs, "game_root", "") or "")
    except Exception:
        return ""
    if not root or not os.path.isdir(root):
        return ""
    p = os.path.join(root, rel_path)
    if os.path.exists(p):
        return p
    # game_root may also point to the data folder itself
    alt = os.path.join(root, "data", rel_path)
    return alt if os.path.exists(alt) else ""


def _unpack_sibling_paks(context, work, base, P):
    """Additionally load into the cache the paks of this ship parts that live in other paks.

    Returns the list of the loaded paths (relative to work).
    """
    ipath = _model_index_file(context)
    if not ipath or not os.path.exists(ipath):
        _log("[SC_MODEL_IEX] parts in other paks: no model index (%r)" % ipath)
        return []
    try:
        with open(ipath, encoding="utf-8") as fh:
            models = (json.load(fh).get("models") or {})
    except Exception as exc:
        _log("[SC_MODEL_IEX] parts in other paks: index not read: %s" % exc)
        return []
    rel = os.path.relpath(base, work).replace("/", "\\")
    folder = os.path.dirname(rel).lower()
    stem = os.path.basename(rel)
    added = []
    for path, info in models.items():
        p = path.replace("/", "\\")
        if os.path.dirname(p).lower() != folder:
            continue
        other = os.path.basename(p)
        if other.lower() == stem.lower() or not _same_ship(stem, other):
            continue
        if any(s in other.lower() for s in SKIP_SIBLINGS):
            continue
        pak = (info or {}).get("pak") or ""
        if not pak:
            continue
        LAST_SIBLINGS[other] = pak
        try:
            context.scene["sc_model_iex_siblings"] = dict(LAST_SIBLINGS)
        except Exception:
            pass
        if (os.path.exists(os.path.join(work, p + ".mdl-hdr"))
                and os.path.exists(os.path.join(work, p + ".mdl-msh000"))):
            continue
        pak_path = _game_data_file(context, pak)
        if not pak_path or not os.path.exists(pak_path):
            continue
        try:
            P.unpack_file(pak_path, work)
            added.append(p)
            LAST_SIBLINGS[other] = pak
            try:
                context.scene["sc_model_iex_siblings"] = dict(LAST_SIBLINGS)
            except Exception:
                pass
            _log("[SC_MODEL_IEX] part %s additionally loaded from %s" % (other, pak))
        except Exception as exc:
            _log("[SC_MODEL_IEX] IMPORTANT: part %s was not loaded from %s — %s"
                 % (other, pak, exc))
    return added


def _part_skeleton(context, folder, obj_name):
    "Part skeleton by object name: `<base>.mdl-msh000` -> `<base>.mdl-skl`."
    if not folder:
        return None
    base = obj_name.split(".mdl-msh")[0]
    return _load_skeleton(None, os.path.join(folder, base))


_SC_SKELETON_CACHE = {}


def _part_root_name(part_sk):
    "Name of the part skeleton root bone (for the log and for the category)."
    if not part_sk:
        return "?"
    for b in part_sk.get("bones") or []:
        if isinstance(b, dict) and b.get("parent", -1) < 0:
            return b.get("name") or "?"
    return "?"


def _load_skeleton(M, base):
    "Read the .mdl-skl next to the model base (bones with transformations)."
    path = base + ".mdl-skl"
    if not os.path.exists(path):
        return None
    if path in _SC_SKELETON_CACHE:
        return _SC_SKELETON_CACHE[path]
    try:
        from sc_tools import skeleton as SK
        info = SK.parse_skl(path)
        _log("[SC_MODEL_IEX] skeleton: %d bones" % len(info.get("bones") or []))
        _SC_SKELETON_CACHE[path] = info
        return info
    except Exception as exc:
        _log("[SC_MODEL_IEX] skeleton not parsed: %s" % exc)
        _SC_SKELETON_CACHE[path] = None
        return None


def _find_bone_for_part(sk_info, suffix):
    "Find a bone for the part by its suffix (_eng, _cap, _boost, _gun2 …)."
    if not sk_info:
        return None
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    s = (suffix or "").lower().lstrip("_")
    if not s:
        return None
    by_name = {b.get("name", "").lower(): b for b in bones}
    # First the EXACT bone name: Gungnir (race1_l_t5_sniper) has both `cap`
    # (the canopy, moved by takeoff.anim) and `capacitor` (a completely different bone).
    # The alias PART_BONES['cap'] = 'capacitor' used to send the part onto a foreign bone.
    if s in by_name:
        return by_name[s]
    if s in PART_BONES and PART_BONES[s] in by_name:
        return by_name[PART_BONES[s]]
    # _gun1 -> gun_1, _boost2 -> boost_2
    digits = "".join(ch for ch in s if ch.isdigit())
    letters = "".join(ch for ch in s if ch.isalpha())
    if digits and letters:
        for cand in ("%s_%s" % (letters, digits), letters):
            if cand in by_name:
                return by_name[cand]
    for name, bone in by_name.items():
        if name.startswith(s) or s.startswith(name):
            return bone
    return None


def _bone_offset(bone):
    "Bone offset in the Blender system: (x, -z, y), the same as vertices."
    pos = bone.get("position") or (0.0, 0.0, 0.0)
    return (-pos[0], -pos[2], pos[1])


def _build_armature(context, sk_info, base_name, scale=1.0):
    "Create an armature from .mdl-skl: bones with hierarchy and positions."
    if not sk_info:
        return None
    arm = bpy.data.armatures.new("%s_skl" % base_name)
    obj = bpy.data.objects.new(arm.name, arm)
    context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    created = {}
    # IMPORTANT: `position` in a bone record is the PARENT-RELATIVE one, not the absolute one.
    # This is clear from the game code (FUN_14009bf70): T_bone = T_parent + position.
    # Check on Gungnir: for engine_1 (parent wings) position = (0,0,0), so
    # its absolute position equals the wings position (0, 0.2469, -0.0968); if
    # the bone were placed at (0,0,0), as before, the nacelle would rotate around the origin
    # instead of around the wing hinge — exactly the "low axis" seen
    # in the game.
    absolute = {}

    def bone_abs(b):
        idx = b.get("index")
        if idx in absolute:
            return absolute[idx]
        p = b.get("position") or (0.0, 0.0, 0.0)
        par = b.get("parent", -1)
        if par >= 0 and 0 <= par < len(sk_info.get("bones") or []):
            base = bone_abs(sk_info["bones"][par])
        else:
            base = (0.0, 0.0, 0.0)
        absolute[idx] = (base[0] + p[0], base[1] + p[1], base[2] + p[2])
        return absolute[idx]

    for b in sk_info.get("bones") or []:
        if not isinstance(b, dict):
            continue
        eb = arm.edit_bones.new(b.get("name") or "bone")
        p = bone_abs(b)
        # the same conversion as for vertices: (x, -z, y)
        eb.head = (-p[0] * scale, -p[2] * scale, p[1] * scale)
        eb.tail = (eb.head[0], eb.head[1] + 0.25 * scale, eb.head[2])
        created[b.get("index")] = eb
    for b in sk_info.get("bones") or []:
        if not isinstance(b, dict):
            continue
        par = b.get("parent", -1)
        if par in created and b.get("index") in created:
            created[b["index"]].parent = created[par]
    bpy.ops.object.mode_set(mode="OBJECT")
    # jiggle/mover in the game are bones by name; there are no separate flags in .mdl-skl.
    # We save them as armature/bone properties so they are visible and not lost.
    try:
        jig, mover, pads = [], [], {}
        for b in sk_info.get("bones") or []:
            name = b.get("name") or ""
            low = name.lower()
            pad = b.get("pad_raw") or b""
            if "jiggle" in low:
                jig.append(name)
            if "mover" in low:
                mover.append(name)
            if any(pad):
                pads[name] = bytes(pad).hex()
            db = arm.bones.get(name)
            if db is not None:
                if "jiggle" in low:
                    db["sc_jiggle"] = True
                if "mover" in low:
                    db["sc_mover"] = True
                if pad and any(pad):
                    db["sc_pad"] = bytes(pad).hex()
        if jig:
            obj["sc_jiggle_bones"] = ",".join(jig)
        if mover:
            obj["sc_mover_bones"] = ",".join(mover)
        if pads:
            obj["sc_bone_pads"] = pads
    except Exception:
        pass
    # The bone quaternion in the file is not the same as the Blender bone orientation
    # (in Blender the Y axis runs along the bone). We remember the original so that an export
    # of an unmodified skeleton returns the same bytes.
    quats = {}
    for b in sk_info.get("bones") or []:
        q = b.get("rotation") or ()
        if len(q) == 4:
            quats[b.get("name") or ""] = [float(c) for c in q]
    try:
        import json as _json
        arm["sc_bone_quats"] = _json.dumps(quats)
        # The matrices in the file are not derived one-to-one from the quaternion (the game has its own
        # layout), so for unmodified bones we keep them as they were.
        raw = {}
        for b in sk_info.get("bones") or []:
            raw[b.get("name") or ""] = {
                "rot": [float(x) for x in (b.get("rot_matrix") or ())],
                "bind": [float(x) for x in (b.get("bind_matrix") or ())],
                "inv": [float(x) for x in (b.get("inverse_translation") or ())],
                "pad": bytes(b.get("pad_raw") or b"").hex(),
                "pos": bytes(b.get("position_raw") or b"").hex(),
                "quat": bytes(b.get("quat_raw") or b"").hex(),
            }
        arm["sc_bone_raw"] = _json.dumps(raw)
        arm["sc_flags"] = int(sk_info.get("flags") or 0)
    except Exception:
        pass
    _log("[SC_MODEL_IEX] armature %s: %d bones" % (arm.name, len(created)))
    return obj


def _pose_file_for(base, pose_name):
    "Find a pose .anim next to the model: <base>_<name>.anim."
    if not pose_name:
        return None
    cand = "%s_%s.anim" % (base, pose_name)
    if os.path.exists(cand):
        return cand
    folder = os.path.dirname(base)
    low = pose_name.lower()
    try:
        for name in os.listdir(folder):
            if name.lower().endswith(".anim") and low in name.lower():
                return os.path.join(folder, name)
    except Exception:
        pass
    return None


def _apply_pose(objs, pose_path, scale=1.0):
    """Rotate the group of pose objects around the model center.

    With flags=2 the .anim poses contain only a quaternion (axis + 90°), so
    it is enough to rotate the objects as a whole — no skeleton is needed for that.
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import anim_pose
    except Exception as exc:
        _log("[SC_MODEL_IEX] anim_pose unavailable: %s" % exc)
        return False
    try:
        info = anim_pose.parse_pose(pose_path)
    except Exception as exc:
        _log("[SC_MODEL_IEX] pose not parsed: %s" % exc)
        return False
    nodes = info.get("nodes") or []
    if not nodes or not nodes[0]["keys"]:
        _log("[SC_MODEL_IEX] the pose has no keys: %s" % os.path.basename(pose_path))
        return False
    quat = nodes[0]["keys"][0]["quat"]
    axis, angle = anim_pose.quat_to_axis_angle(quat)
    from mathutils import Vector, Quaternion, Matrix
    # axis in the game system -> Blender: (x, -z, y)
    ax = Vector((-axis[0], -axis[2], axis[1]))
    if ax.length < 1e-6:
        return False
    ax.normalize()
    center = Vector((0.0, 0.0, 0.0))
    if objs:
        s = Vector((0.0, 0.0, 0.0))
        for o in objs:
            s += o.location
        center = s / len(objs)
    rot = Matrix.Rotation(angle * 3.141592653589793 / 180.0, 4, ax)
    to_c = Matrix.Translation(center)
    from_c = Matrix.Translation(-center)
    for o in objs:
        o.matrix_world = to_c @ rot @ from_c @ o.matrix_world
    _log("[SC_MODEL_IEX] pose %s: axis %s, %.1f°"
          % (os.path.basename(pose_path), tuple(round(v, 2) for v in ax), angle))
    return True


# --------------------------------------------------------------------------
# animation (.anim)
# --------------------------------------------------------------------------

ANIM_FPS = 24.0


def _sc_anim_module(context):
    "Import sc_anim (bundled next to sc_tools)."
    root = _sdk_root()
    if root and root not in sys.path:
        sys.path.insert(0, root)
    import sc_anim
    return sc_anim


def _quat_game_to_blender(q):
    """Game quaternion (x, y, z, w) -> Blender quaternion.

    Vertices are converted by the basis M: (x, y, z) -> (x, -z, y), that is by a
    +90° rotation around X. The rotation is converted by conjugation: q_b = M · q_g · M^-1.
    """
    from mathutils import Quaternion
    half = 0.7071067811865476
    # Reflection changes the handedness: rotation axis = det(M)·M·a = (ax, az, -ay)
    return Quaternion((q[3], q[0], q[2], -q[1]))


def _vec_game_to_blender(v, scale=1.0):
    "Game vector -> Blender: (x, y, z) -> (x, -z, y)."
    return (-v[0] * scale, -v[2] * scale, v[1] * scale)


def _anim_file_for(base, anim_name):
    "Find .anim next to the model: <base>_<name>.anim, otherwise by substring."
    if not anim_name:
        return None
    if os.path.isabs(anim_name):
        return anim_name if os.path.exists(anim_name) else None
    folder = os.path.dirname(base)
    stem = os.path.basename(base)
    for cand in ("%s_%s.anim" % (stem, anim_name), anim_name,
                 "%s.anim" % anim_name):
        p = os.path.join(folder, cand)
        if os.path.exists(p):
            return p
    low = anim_name.lower()
    try:
        names = sorted(os.listdir(folder))
    except Exception:
        names = []
    for n in names:
        if n.lower().endswith(".anim") and low in n.lower():
            return os.path.join(folder, n)
    return None


def _action_fcurves(act):
    "All fcurves of an action: the old API and Blender 4.4+ slots."
    out = list(getattr(act, "fcurves", []) or [])
    if out:
        return out
    try:
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out.extend(bag.fcurves)
    except Exception:
        pass
    return out


def _anim_ref_key(name):
    """Normalize an .anim reference: no path and no extension.

    In `.model` the file name is written in different ways:
        file  race1_l_t5_sniper_landing.anim
        file  r2_h_t4_idle                 <- without extension (Relic!)
    so the comparison must be without `.anim`, otherwise the block is not found and speed
    stays 1.0 (for Relic this made 240° pass in 3 frames — "vibration"
    instead of a slow turn at speed 0.004).
    """
    n = os.path.basename((name or "").replace("/", "\\")).lower()
    if n.endswith(".anim"):
        n = n[:-5]
    return n


def _anim_speed_from_model(base, anim_path):
    """Take `speed` from the `.model` block that declares this .anim.

    `.model` does not set the duration — only `file` and `speed`. The real
    duration in the game is duration / speed (Gungnir `landing`: 0.958 s at
    speed 0.25 -> about 3.8 s). Without this the animation in Blender plays four times faster
    than in the client.
    """
    mp = base + ".model"
    if not os.path.exists(mp):
        return 1.0
    try:
        with open(mp, encoding="latin-1") as fh:
            lines = fh.read().splitlines()
    except Exception:
        return 1.0
    want = _anim_ref_key(anim_path)
    depth = 0
    inside = False
    has_file = False
    speed = 1.0
    block_speed = 1.0
    for raw in lines:
        line = raw.split("//")[0].strip()
        if not line:
            continue
        low = line.lower()
        if not inside:
            # the block starts with the line `anim <name>`, and `{` is usually on the NEXT
            # line — previously the parser required the brace on the same line and did not
            # find any block (speed always came out 1.0)
            first = line.replace("{", " ").replace("}", " ").split()
            if first and first[0].lower() == "anim" and len(first) > 1:
                inside = True
                depth = line.count("{") - line.count("}")
                has_file = False
                block_speed = 1.0
            continue
        depth += line.count("{") - line.count("}")
        parts = line.replace("{", " ").replace("}", " ").split()
        if parts:
            head = parts[0].lower()
            if head in ("file", "files") and len(parts) > 1:
                for tok in parts[1:]:
                    if _anim_ref_key(tok) == want:
                        has_file = True
            elif head.endswith(".anim") and _anim_ref_key(head) == want:
                has_file = True
            elif head == "speed" and len(parts) > 1:
                try:
                    block_speed = float(parts[1].replace(",", "."))
                except ValueError:
                    pass
        if depth <= 0:
            if has_file and block_speed > 0:
                speed = block_speed
            inside = False
            depth = 0
    if inside and has_file and block_speed > 0:
        speed = block_speed
    return speed


def _anim_fps_from_model(base, anim_path, user_fps=0.0):
    """Frames/s for the import: from the model, not hard-coded 24.

    Where the rate comes from:
      * **24 fps** is the rate the `.anim` files are laid out in. Verified
        against the data: in 3962 of 4268 tracks (with more than one key) `duration * 24 = keys - 1`
        holds exactly, and `t0` in the header is a multiple of 1/24
        in 4049 files out of 4063. So a frame in the file is 1/24 second.
      * `speed` from `.model` is NOT the rate but a playback multiplier; it must not
        be put into fps (at 24*0.25 = 6 fps, 49 keys would collapse into 6
        frames). It stretches time: frame = 1 + t*24/speed.

    If the user entered their own value (user_fps > 0), it takes priority.
    """
    speed = _anim_speed_from_model(base, anim_path) if base else 1.0
    if user_fps and user_fps > 0:
        return float(user_fps), speed, "user"
    return ANIM_FPS, speed, "model"


def _all_anim_files(base):
    "All .anim of this ship: `<model>_*.anim` next to the model."
    folder = os.path.dirname(base)
    stem = os.path.basename(base).lower()
    out = []
    try:
        for f in sorted(os.listdir(folder)):
            low = f.lower()
            if low.endswith(".anim") and low.startswith(stem + "_"):
                out.append(os.path.join(folder, f))
    except Exception:
        pass
    return out


# preference order for AUTO-selection when no name is given:
#   first what "lives" in the hangar, then landing/takeoff, then everything else
AUTO_ANIM_ORDER = ("idle", "aim", "loop", "landing", "land", "takeoff",
                   "deploy", "open", "close")


def _is_pose_file(path):
    """This is a STATIC POSE (x_d, x_up, z_l, z_r), not an animation.

    Some ships (e.g. race2_m_t5_baphomet) have nothing but poses:
    four files of 192 bytes each with duration 0. The auto-selection used to take "the
    longest file" and assigned x_d — the ship stood on its nose.
    """
    name = os.path.basename(path).lower()
    if name.endswith(".anim"):
        name = name[:-5]
    # a pose = a file whose name ends with _x_d, _x_up, _z_l or _z_r
    # ("the tail after the last underscore" must not be used: for `_x_d` that is "d")
    return name.endswith(("_x_d", "_x_up", "_z_l", "_z_r"))


def _auto_anim(avail):
    "Choose an animation without a name: idle/aim, otherwise the longest one."
    if not avail:
        return None
    # we do not take poses in the auto-selection: they are one-off tilts/rolls for aiming
    real = [p for p in avail if not _is_pose_file(p)]
    if not real:
        return None
    names = [os.path.basename(p) for p in real]
    for want in AUTO_ANIM_ORDER:
        for p, n in zip(real, names):
            low = n.lower()
            if low.endswith("_%s.anim" % want) or ("_%s_" % want) in low:
                return p
    # nothing familiar — take the longest file: usually that is the main animation
    return max(real, key=lambda p: os.path.getsize(p))


def _anim_names(avail, base=None):
    "Short names for the log: drop the model prefix — land, landing, takeoff…"
    stem = (os.path.basename(base) + "_").lower() if base else ""
    out = []
    for p in avail:
        n = os.path.basename(p)
        if n.lower().endswith(".anim"):
            n = n[:-5]
        if stem and n.lower().startswith(stem):
            n = n[len(stem):]
        out.append(n)
    return out


def _arm_for_anim(context, base, path, arm_main):
    """Choose the armature by the bones the .anim animates.

    The capacitor has its own skeleton (cap_rot_anim_jnt), while cap_anim_jnt* live in
    the hull skeleton — so the file name is no indicator, we look at the bones.
    """
    try:
        A = _sc_anim_module(context)
        anim = A.read_anim(path)
        names = {str(c.name).lower() for c in anim.channels}
    except Exception:
        return arm_main
    if not names:
        return arm_main
    best = None
    for a in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        bones = {b.name.lower() for b in a.data.bones}
        n = len(names & bones)
        if n and (best is None or n > best[0]):
            best = (n, a)
    return best[1] if best else None


def _load_all_anims(context, arm_obj, base, scale=1.0, user_fps=0.0):
    """Make a separate action from EVERY .anim of the ship.

    Needed when the animation names are unknown: they all go into the Blender
    action list (Action Editor / Dope Sheet), and switching is possible with the standard
    tools, without a custom interface.
    """
    files = _all_anim_files(base)
    made = []
    for path in files:
        fps_eff, sp, _src = _anim_fps_from_model(base, path, user_fps)
        name = os.path.splitext(os.path.basename(path))[0]
        if bpy.data.actions.get(name):
            bpy.data.actions.remove(bpy.data.actions[name])
        target_arm = _arm_for_anim(context, base, path, arm_obj)
        if target_arm is None:
            continue
        used = _apply_anim(context, target_arm, path, scale=scale, fps=fps_eff,
                           speed=sp, action_name=name, self_contained=True)
        act = bpy.data.actions.get(name)
        if not used or act is None:
            continue
        act.use_fake_user = True
        try:
            act["sc_model_iex_file"] = os.path.basename(path)
            act["sc_model_iex_speed"] = sp
            # we remember the frame range of THIS action: landing has 93
            # frames, takeoff 77, and when switching no foreign
            # scene range must remain
            lo, hi = act.frame_range
            act["sc_model_iex_frames"] = (int(round(lo)), int(round(hi)))
        except Exception:
            pass
        made.append(act)
    # we make active what would be chosen automatically: otherwise after "*"
    # the last animation remains (usually a roll pose) and the ship looks crooked
    main_files = [f for f in files
                  if _arm_for_anim(context, base, f, arm_obj) is arm_obj]
    pick = _auto_anim(main_files or files)
    if pick:
        name = os.path.splitext(os.path.basename(pick))[0]
        act = bpy.data.actions.get(name)
        if act is not None:
            _set_action(arm_obj, act)
            try:
                lo, hi = act.frame_range
                context.scene.frame_start = int(round(lo))
                context.scene.frame_end = max(int(round(lo)) + 1, int(round(hi)))
                context.scene.frame_set(int(round(lo)))
            except Exception:
                pass
    else:
        # only static poses remained (x_d/x_up/z_l/z_r): without this branch
        # the LAST created action stayed on the armature and the ship
        # froze in it (Baphomet lay on its side after z_r)
        _set_action(arm_obj, None)
        for pb in arm_obj.pose.bones:
            try:
                pb.location = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.scale = (1.0, 1.0, 1.0)
            except Exception:
                pass
        try:
            context.view_layer.update()
        except Exception:
            pass
        _log("[SC_MODEL_IEX] all found .anim files are static poses: no action assigned, the ship is in the rest pose; poses are available in the Action Editor (x_d, x_up, z_l, z_r)")
    _log("[SC_MODEL_IEX] actions loaded: %d — switch in the Action Editor" % len(made))
    if made:
        _log("[SC_MODEL_IEX] action ranges: %s"
             % ", ".join("%s 1..%d" % (a.name.replace("SC_MODEL_IEX_", ""), a["sc_model_iex_frames"][1])
                         for a in made if "sc_model_iex_frames" in a.keys()))
    return len(made)


def _apply_anim(context, arm_obj, anim_path, scale=1.0, fps=ANIM_FPS,
                do_rot=True, do_loc=True, action_name=_L(""), speed=1.0,
                base_path=None, self_contained=False, quat_flip=False):
    """Apply .anim to the armature: bone keys in an Action.

    The .anim format is closed (see sc_anim): a channel = one bone (name and index
    match .mdl-skl), u16 times in fractions [0, 0x8000] of duration.
    The rotation in a channel is local to the bone, the position is ABSOLUTE in the model
    system, so in Blender the difference from the bone rest position is written.
    """
    try:
        A = _sc_anim_module(context)
        anim = A.read_anim(anim_path)
    except Exception as exc:
        _log("[SC_MODEL_IEX] .anim not parsed (%s): %s" % (os.path.basename(str(anim_path)), exc))
        return 0
    if not anim.channels:
        _log("[SC_MODEL_IEX] the animation has no channels: %s" % os.path.basename(anim_path))
        return 0

    bones = arm_obj.data.bones
    by_name = {b.name.lower(): b.name for b in bones}
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    name = action_name or os.path.splitext(os.path.basename(anim_path))[0]
    act = bpy.data.actions.new(name)
    _set_action(arm_obj, act)

    start = 1
    if self_contained:
        # the action must be self-contained: a bone that this .anim does not
        # animate would otherwise stay in the pose from the previous action (we observed
        # "the ship lies on its side" after switching between animations). So
        # we key the rest pose for ALL bones, and the channels override it.
        sp0 = speed if speed and speed > 0 else 1.0
        end0 = int(round(start + max(0.0, anim.duration) * fps / sp0))
        for pb in arm_obj.pose.bones:
            try:
                pb.rotation_mode = "QUATERNION"
            except Exception:
                pass
            pb.location = (0.0, 0.0, 0.0)
            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
            pb.keyframe_insert("location", frame=start, group=pb.name)
            pb.keyframe_insert("rotation_quaternion", frame=start, group=pb.name)
            if end0 > start:
                pb.keyframe_insert("location", frame=end0, group=pb.name)
                pb.keyframe_insert("rotation_quaternion", frame=end0, group=pb.name)
    used = 0
    missing = []
    for ch in anim.channels:
        pb = None
        bone_name = by_name.get(ch.name.lower())
        if bone_name:
            pb = arm_obj.pose.bones.get(bone_name)
        if pb is None and ch.index < len(bones):
            pb = arm_obj.pose.bones.get(bones[ch.index].name)
        if pb is None:
            missing.append(ch.name)
            continue

        secs = anim.seconds(ch)
        # fps — from the model (24), speed from .model stretches the time
        sp = speed if speed and speed > 0 else 1.0
        frames = [start + t * fps / sp for t in secs]

        if do_rot and ch.quat:
            pb.rotation_mode = "QUATERNION"
            prev = None
            for f, q in zip(frames, ch.quat):
                bq = _quat_game_to_blender(q)
                if quat_flip:
                    # reversing the rotation direction (conjugation): needed when
                    # a detail in the editor rotates the wrong way
                    bq = bq.conjugated()
                if prev is not None and (bq.w * prev.w + bq.x * prev.x
                                         + bq.y * prev.y + bq.z * prev.z) < 0.0:
                    bq = -bq
                prev = bq
                pb.rotation_quaternion = bq
                pb.keyframe_insert(data_path="rotation_quaternion", frame=f)

        if do_loc and ch.pos:
            # The position in a channel is an OFFSET from the rest pose, NOT an absolute
            # coordinate. Proof: for `module` the bone stands at
            # (0, 0.612, 0.4654), while in ALL animations the third component is exactly 0
            # ((0, 0.4031, 0), (0, -0.1705, 0)); `wings` rest has z=-0.0968,
            # while in the files z is always 0. If these were absolute coordinates,
            # every animation would teleport the details into the z=0 plane —
            # this is exactly why the hatch used to be "always closed" and the gun
            # did not raise. So we subtract nothing.
            if not all(abs(v) < 1e-9 for p in ch.pos for v in p):
                for f, p in zip(frames, ch.pos):
                    pb.location = _vec_game_to_blender(p, scale)
                    pb.keyframe_insert(data_path="location", frame=f)
        used += 1

    curves = _action_fcurves(act)
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    end = int(round(start + max(0.0, anim.duration) * fps / (speed or 1.0)))
    context.scene.frame_start = start
    context.scene.frame_end = max(start + 1, end)
    _log("[SC_MODEL_IEX] animation %s: channels %d, bones involved %d, fcurves %d, frames %d..%d (%.3f s, speed %.3f)"
         % (os.path.basename(anim_path), len(anim.channels), used, len(curves),
            context.scene.frame_start, context.scene.frame_end, anim.duration,
            speed or 1.0))
    if missing:
        _log("[SC_MODEL_IEX] bones not found (%d): %s"
             % (len(missing), ", ".join(missing[:8])))
    return used


def _skin_bind(context, arm_obj, sk_info, scale=1.0, index_shift=1, objects=None,
               only_stem=None, bone_map=None):
    """Skinning by the BONE INDEX from the vertex (usage element 2 in .mdl-msh).

    This is the real game binding: in `.mdl-msh` formats 18/21/28 each
    vertex carries a u32 bone number, and `.mdf` sets `InfluenceBones 1` — one
    bone per vertex, the weight is always 1.0, there is no separate weight array.

    Verified: `ship_ufo_flaps` has 52 different values per mesh — exactly 52
    `flap_1…flap_52` bones moved by `ship_ufo_flaps_wave.anim`; `drill` has
    `root` and `drill` (the rotating part).

    IMPORTANT: the vertices of such meshes are already in the MODEL system (the bounds match
    `.mdl-hdr`), so they must NOT be shifted to the bone position — unlike the
    separate base parts (`_cap`, `_eng`), whose vertices are local.
    """
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    skinned = 0
    unmatched = 0
    # if an object list is given (the DETAIL armature) — touch only them,
    # otherwise we would iterate the whole scene anyway and, for example, the ship hull
    # would bind to the detail bones and start rotating together with the reactor
    pool = list(objects) if objects is not None else list(bpy.context.scene.objects)
    for obj in pool:
        if obj.type != "MESH":
            continue
        # meshes of self-animating details are already bound to THEIR OWN armature:
        # adding ship bone groups to them would give the vertices a second weight
        # and the detail would stop rotating (for the Phoenix reactor this is how
        # the main/boost_1/boost_2 groups appeared and the rotation was lost)
        if objects is None and obj.get("sc_model_iex_subrig"):
            continue
        # the bone indices in the PART meshes refer to the PART skeleton, not the ship one.
        # If they are bound to the ship skeleton, for the Phoenix reactor indices 0/1/2
        # fall onto main / boost_1 / boost_2 — and the detail starts rotating
        # together with the hull rudders. Therefore the common pass touches only the meshes
        # of the model itself (only_stem), while parts get one rigid group.
        if only_stem and obj.name.split(".mdl-msh")[0].lower() != only_stem.lower():
            continue
        idx = _MESH_BONE_IDX.get(obj.name)
        if not idx or len(idx) != len(obj.data.vertices):
            continue
        groups = {}
        # IMPORTANT: the bone index in a vertex is ONE GREATER than the record number in
        # .mdl-skl. Verified by comparing the geometry center of each group with
        # absolute bone positions (Gungnir, msh000):
        #   idx 25 (rcs_4)     -> bone 26 `module`      distance 0.12 (was 3.07)
        #   idx 26 (module)    -> bone 27 `cylinder1`   distance 0.02 (was 0.63)
        #   idx 27..32 (cylinders)-> bones 28..33        distance 0.02 (was 0.57)
        # 9 groups out of 11 matched; for the two remaining ones the mechanism bone is at the origin
        # and the distance does not distinguish anything there. From the same shift
        # it follows that the lid (geometry `gun_6`) sits on the bone `cap` and moves
        # by 1.4486, while the lamps (geometry `cap`) sit on `wings` and move with
        # the engines. This is exactly what was missing: before, every detail was
        # shifted by one bone.
        shift = int(index_shift) if len(bones) > 1 else 0
        root_name = ""
        for b in bones:
            if b.get("parent", -1) < 0:
                root_name = b.get("name") or ""
                break

        def resolve(b):
            "Bone index from the file -> bone name (taking the offset/map into account)."
            try:
                bi = int(b)
            except Exception:
                return None
            if bone_map:
                return bone_map.get(bi)
            t = bi + shift if 0 <= bi + shift < len(bones) else bi
            if 0 <= t < len(bones):
                return bones[t].get("name")
            return root_name or None

        # weights: for formats with InfluenceBones > 1 a vertex may hang on
        # several bones (usage element 1), then we set the real weights
        infl = _MESH_INFLUENCES.get(obj.name)
        weighted = bool(infl) and len(infl) == len(obj.data.vertices)
        groups = {}          # bone name -> list of vertices (weight 1.0)
        wgroups = {}         # bone name -> list of (vertex, weight)
        bad = 0
        if weighted:
            for vi, pairs in enumerate(infl):
                for b, w in pairs:
                    name = resolve(b)
                    if not name:
                        bad += 1
                        continue
                    wgroups.setdefault(name, []).append((vi, float(w)))
        else:
            for vi, b in enumerate(idx):
                name = resolve(b)
                if not name:
                    bad += 1
                    continue
                groups.setdefault(name, []).append(vi)
        if bad:
            _log("[SC_MODEL_IEX] %s: vertices/influences with a bone index outside the list — %d, they are placed on the root bone %s" % (obj.name, bad, root_name or "—"))
        made = 0
        for name in sorted(set(groups) | set(wgroups)):
            if not name or name not in arm_obj.data.bones:
                continue
            vg = obj.vertex_groups.get(name) or obj.vertex_groups.new(name=name)
            if name in groups:
                vg.add(groups[name], 1.0, "REPLACE")
            if name in wgroups:
                for vi, w in wgroups[name]:
                    vg.add([vi], w, "REPLACE")
            made += 1
        if not made:
            unmatched += 1
            continue
        if obj.modifiers.get("SC_MODEL_IEX_armature") is None:
            mod = obj.modifiers.new("SC_MODEL_IEX_armature", "ARMATURE")
            mod.object = arm_obj
            mod.use_vertex_groups = True
            mod.use_bone_envelopes = False
        if obj.parent is None:
            world = obj.matrix_world.copy()
            obj.parent = arm_obj
            obj.parent_type = "OBJECT"
            obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
            obj.matrix_world = world
        skinned += 1
    _log("[SC_MODEL_IEX] skinning by bone index: meshes %d (formats 18/21/28)%s"
         % (skinned, (", without a name match: %d" % unmatched) if unmatched else ""))
    return skinned


def _is_skinned(obj):
    "The mesh is already bound by the bone index — it can be neither shifted nor parented."
    idx = _MESH_BONE_IDX.get(obj.name)
    return bool(idx) and obj.modifiers.get("SC_MODEL_IEX_armature") is not None


def _skeleton_absolute(sk_info):
    """Absolute bone positions: the position in a record is the PARENT-RELATIVE one.

    From the game code (FUN_14009bf70): T_bone = T_parent + position. Therefore
    the absolute position is the sum over the chain, not the raw record+0x40 field.
    """
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    by_idx = {b.get("index"): b for b in bones}
    out = {}

    def walk(idx, guard=0):
        if idx in out:
            return out[idx]
        b = by_idx.get(idx)
        if b is None or guard > len(bones) + 2:
            return (0.0, 0.0, 0.0)
        p = b.get("position") or (0.0, 0.0, 0.0)
        par = b.get("parent", -1)
        base = walk(par, guard + 1) if par in by_idx else (0.0, 0.0, 0.0)
        out[idx] = (base[0] + p[0], base[1] + p[1], base[2] + p[2])
        return out[idx]

    for b in bones:
        walk(b.get("index"))
    return out


def _object_part_suffix(name, stem):
    "Part suffix from the object name: base_eng.mdl-msh000 -> '_eng'."
    low = name.lower()
    pos = low.find(".mdl-msh")
    head = name[:pos] if pos > 0 else name
    if head.lower().startswith(stem.lower()):
        return head[len(stem):]
    return ""


def _part_vertex_map(obj, bone):
    """Shift the part vertices to the bone position (empirically, not from the game code).

    Established experimentally: without the shift the parts lie in the CENTER of the hull
    (`_eng` Y ~ 0 while the hull is -5.5..4.9), that is their vertices are given locally.
    The game code (`FUN_14078aad0`, `FUN_14078c0e0`, `FUN_14078d170`) shows
    only a bone lookup by name, without multiplying the vertices by a matrix — so
    the offset comes from the node record (240 bytes) that is yet to be
    parsed. For now we apply the bone position.

    Bone coordinates in the game system: (x, y, z) -> Blender (x, -z, y).
    """
    pos = bone.get("position") or (0.0, 0.0, 0.0)
    px, py, pz = pos[0], pos[1], pos[2]
    if px == 0.0 and py == 0.0 and pz == 0.0:
        return
    mesh = obj.data
    verts = mesh.vertices
    co = [0.0] * (len(verts) * 3)
    verts.foreach_get("co", co)
    for i in range(len(verts)):
        co[i * 3] += px
        co[i * 3 + 1] -= pz
        co[i * 3 + 2] += py
    verts.foreach_set("co", co)
    mesh.update()


def _load_part_offsets(context, base):
    """Offsets for specific meshes: part_offsets.json next to the model.

    Format (key — mesh file name, value — offset in Blender coordinates):
        { "race2_l_t5_confessor_boost.mdl-msh003": [0.0, 7.4, 0.0] }

    Needed because parts are not a single whole: `_boost` has four separate meshes
    (exhaust flares) and they sit at different points of the ship. One offset for the whole
    part would not place them correctly.
    """
    prefs = _addon_prefs(context)
    sdk_root = _sdk_root()
    cands = [os.path.join(os.path.dirname(base), "part_offsets.json")]
    if sdk_root:
        cands.append(os.path.join(sdk_root, "part_offsets.json"))
    if getattr(prefs, "index_path", ""):
        cands.append(os.path.join(os.path.dirname(bpy.path.abspath(prefs.index_path)),
                                  "part_offsets.json"))
    cands.append(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(base)))), "part_offsets.json"))
    for cand in cands:
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8") as fh:
                    data = json.load(fh)
                _log("[SC_MODEL_IEX] offsets from %s: %d records"
                     % (os.path.basename(cand), len(data)))
                return data
            except Exception as exc:
                _log("[SC_MODEL_IEX] part_offsets.json not read: %s" % exc)
    return {}


LAST_SIBLINGS = {}


_EFFECTS_CACHE = None


def _effects_index(context):
    "model -> [(joint, effect)] from gamedata/def/*.lua (cached per session)."
    global _EFFECTS_CACHE
    if _EFFECTS_CACHE is not None:
        return _EFFECTS_CACHE
    import re as _re
    out = {}
    try:
        prefs = _addon_prefs(context)
        root = bpy.path.abspath(getattr(prefs, "game_root", "") or "")
        base = os.path.join(root, "gamedata", "def")
        if not os.path.isdir(base):
            base = os.path.join(root, "data", "gamedata", "def")
        if not os.path.isdir(base):
            _EFFECTS_CACHE = out
            return out
        model_re = _re.compile(r'Def\.(Ship_[A-Za-z0-9_]+)\.model\s*=\s*"([^"]+)"')
        child_re = _re.compile(r'Def\.(Ship_[A-Za-z0-9_]+)\.children\[\d+\]\.'
                               r'(joint|effect)\s*=\s*"([^"]*)"')
        models = {}
        parts = {}
        for dp, _dn, fs in os.walk(base):
            for f in sorted(fs):
                if not f.endswith(".lua"):
                    continue
                try:
                    text = open(os.path.join(dp, f), encoding="utf-8",
                                errors="replace").read()
                except Exception:
                    continue
                for m in model_re.finditer(text):
                    models[m.group(2).replace("/", "\\").lower()] = m.group(1)
                for m in child_re.finditer(text):
                    parts.setdefault(m.group(1), []).append((m.group(2), m.group(3)))
        for path, defname in models.items():
            rows = parts.get(defname) or []
            eff = []
            cur = {}
            for kind, val in rows:
                cur[kind] = val
                if kind == "effect":
                    eff.append((cur.get("joint", "?"), val))
                    cur = {}
            if eff:
                out[path] = (defname, eff)
    except Exception as exc:
        _log("[SC_MODEL_IEX] effects: index not built: %s" % exc)
    _EFFECTS_CACHE = out
    return out


def _log_ship_effects(context, base):
    "Log the ship effects (children in the def) — they are not imported."
    idx = _effects_index(context)
    rel = (base or "").replace("/", "\\").lower()
    mark = "\\models\\"
    pos = rel.find(mark)
    key = rel[pos + 1:] if pos >= 0 else rel
    found = None
    for path, info in idx.items():
        if key.endswith(path) or path.endswith(key):
            found = (path, info)
            break
    if not found:
        return
    path, (defname, eff) = found
    for joint, effect in eff:
        _log("[SC_MODEL_IEX] effect def %s: bone '%s' -> %s (particles are NOT imported)" % (defname, joint, effect))


def _part_own_anim(context, part_base):
    """Find the part .anim that rotates ITS OWN bones.

    That is how self-animating details are built: the Phoenix reactor has its own skeleton
    (`root_cap`, `cap_rot_anim_jnt`, `cap_rot_anim_jnt1`) and its own
    `_cap_rot.anim` of 3.75 s. These bones are NOT in the ship skeleton, so
    an animation applied to the ship armature does not move the detail —
    a separate armature for the detail is required.
    """
    files = _all_anim_files(part_base)
    if not files:
        return None
    sk = _load_skeleton(None, part_base)
    if not sk:
        return None
    names = {(b.get("name") or "").lower() for b in sk.get("bones") or []}
    try:
        A = _sc_anim_module(context)
    except Exception:
        return None
    best = None
    for path in files:
        try:
            anim = A.read_anim(path)
        except Exception:
            continue
        hits = [c for c in anim.channels if (c.name or "").lower() in names]
        if not hits:
            continue
        # prefer the one that has rotations and more keys
        score = (any(c.quat for c in hits), max((c.count for c in hits), default=0))
        if best is None or score > best[0]:
            best = (score, path)
    return best[1] if best else None


def _part_bone_map(objs, sk_part):
    """Layout "bone index in the detail meshes -> name of its skeleton bone".

    The bone order in `.mdl-skl` and the index order in the detail meshes do NOT match.
    Phoenix reactor: mesh indices 0/1/2, bones in the file
    `root_cap`(0), `cap_rot_anim_jnt1`(1), `cap_rot_anim_jnt`(2), but the geometry
    of index 1 sits at the place of `cap_rot_anim_jnt` (z = -1.208), and that of index 2 — at
    the place of `cap_rot_anim_jnt1` (z = -0.251), i.e. the last two bones
    are swapped. A "+1" shift is no good here: it sent 454 vertices
    of the motionless reactor body onto the rotating bone.

    Rule: index 0 is the root bone of the detail (the motionless part), the other
    indices are distributed to the remaining bones by centroid proximity (greedy).
    """
    import math
    bones = [b for b in (sk_part.get("bones") or []) if isinstance(b, dict)]
    if not bones:
        return {}
    root = None
    for b in bones:
        if b.get("parent", -1) < 0:
            root = b
            break
    if root is None:
        root = bones[0]
    # centroids of the index groups (object coordinates are already in the Blender system)
    acc = {}
    for o in objs:
        idxs = _MESH_BONE_IDX.get(o.name)
        if not idxs or len(idxs) != len(o.data.vertices):
            continue
        for v, bi in zip(o.data.vertices, idxs):
            s = acc.setdefault(int(bi), [0.0, 0.0, 0.0, 0])
            s[0] += v.co.x
            s[1] += v.co.y
            s[2] += v.co.z
            s[3] += 1
    if not acc:
        _log("[SC_MODEL_IEX] detail: bone indices not found for meshes %s — layout by geometry is impossible" % ", ".join(o.name for o in objs))
        return {}
    cent = {bi: (s[0] / s[3], s[1] / s[3], s[2] / s[3]) for bi, s in acc.items()}
    out = {}
    rest = sorted(cent)
    if 0 in cent:
        out[0] = root.get("name")
        rest = [i for i in rest if i != 0]
    free = [b for b in bones if b is not root]
    cand = []
    for bi in rest:
        c = cent[bi]
        for b in free:
            p = b.get("position") or (0.0, 0.0, 0.0)
            d = math.dist(c, (-p[0], -p[2], p[1]))
            cand.append((d, bi, b.get("name")))
    cand.sort()
    used_b, used_n = set(), set()
    for d, bi, name in cand:
        if bi in used_b or name in used_n:
            continue
        out[bi] = name
        used_b.add(bi)
        used_n.add(name)
        _log("[SC_MODEL_IEX] detail: index %d -> bone %s (distance %.3f)" % (bi, name, d))
    return out


def _setup_part_subrig(context, part_base, main_arm, sk_main,
                       scale=1.0, anim_fps=0.0, quat_flip=False):
    "A separate armature for a self-animating detail + binding to the ship."
    sk_part = _load_skeleton(None, part_base)
    if not sk_part:
        return 0
    stem = os.path.basename(part_base)
    arm = _build_armature(context, sk_part, stem + "_rig", scale)
    if arm is None:
        return 0
    # meshes of this base — onto its armature
    objs = [o for o in bpy.context.scene.objects
            if o.type == "MESH" and o.name.split(".mdl-msh")[0] == stem]
    skinned = _skin_bind(context, arm, sk_part, scale, objects=objs,
                         index_shift=0,
                         bone_map=_part_bone_map(objs, sk_part))
    if not skinned:
        # no bone indices — rigidly onto the detail root bone
        root = next((b for b in sk_part.get("bones") or [] if b.get("parent", -1) < 0), None)
        if root is not None:
            for o in objs:
                vg = o.vertex_groups.new(name=root.get("name"))
                vg.add(list(range(len(o.data.vertices))), 1.0, "REPLACE")
                if o.modifiers.get("SC_MODEL_IEX_armature") is None:
                    mod = o.modifiers.new("SC_MODEL_IEX_armature", "ARMATURE")
                    mod.object = arm
                    mod.use_vertex_groups = True
                    mod.use_bone_envelopes = False
    for o in objs:
        try:
            o["sc_model_iex_subrig"] = True
        except Exception:
            pass
    # detail animation
    anim_path = _part_own_anim(context, part_base)
    if anim_path:
        fps_eff, sp, _src = _anim_fps_from_model(part_base, anim_path, anim_fps)
        _apply_anim(context, arm, anim_path, scale=scale, fps=fps_eff, speed=sp,
                    action_name=os.path.splitext(os.path.basename(anim_path))[0],
                    quat_flip=quat_flip)
        _log("[SC_MODEL_IEX] detail %s: own animation %s (speed %.3f)"
             % (stem, os.path.basename(anim_path), sp))
    # attach the detail armature to a ship bone
    category = _part_category(sk_part)
    bone = _bone_for_category(sk_main, category) if sk_main else None
    if bone is not None and main_arm is not None:
        try:
            gpose = _skeleton_absolute(sk_main).get(bone.get("index")) or (0.0, 0.0, 0.0)
            arm.matrix_world = Matrix.Translation(
                Vector((gpose[0] * scale, -gpose[2] * scale, gpose[1] * scale)))
            bpy.context.view_layer.update()
            con = arm.constraints.new("CHILD_OF")
            con.target = main_arm
            con.subtarget = bone.get("name")
            pb = main_arm.pose.bones.get(bone.get("name"))
            world = (main_arm.matrix_world @ pb.matrix) if pb is not None else main_arm.matrix_world
            con.inverse_matrix = world.inverted()
            _log("[SC_MODEL_IEX] detail %s attached to bone %s" % (stem, bone.get("name")))
        except Exception as exc:
            _log("[SC_MODEL_IEX] detail %s not attached to a bone: %s" % (stem, exc))
    return len(objs)


def _bind_static_geometry(arm_obj, stem, sk_main):
    """Bind model meshes WITHOUT bone indices to the hull rigidly.

    Such meshes (`fmt 20` and `fmt 27`, they are not skinned in the game) have neither
    vertex groups nor a modifier in Blender — so with an assigned pose or
    animation they stay in place and "hang next to" the rotated ship
    (that is what happened with race2_m_t5_baphomet.mdl-msh002).

    We bind them to the `main` bone (the hull) with weight 1.0 and mark the object
    with the `sc_model_iex_static_geom` property — so that a future exporter knows: the original
    mesh had NO bone index element and it must not be written back.
    """
    if arm_obj is None or not sk_main:
        return 0
    bones = [b for b in (sk_main.get("bones") or []) if isinstance(b, dict)]
    names = [b.get("name") or "" for b in bones]
    target = "main" if "main" in names else (names[0] if names else "")
    if not target or target not in arm_obj.data.bones:
        return 0
    fixed = 0
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        if obj.name.split(".mdl-msh")[0].lower() != (stem or "").lower():
            continue
        if obj.modifiers.get("SC_MODEL_IEX_armature") is not None or obj.vertex_groups:
            continue
        if _MESH_BONE_IDX.get(obj.name):
            continue          # the mesh has indices — handled by _skin_bind
        try:
            vg = obj.vertex_groups.new(name=target)
            vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
            mod = obj.modifiers.new("SC_MODEL_IEX_armature", "ARMATURE")
            mod.object = arm_obj
            mod.use_vertex_groups = True
            mod.use_bone_envelopes = False
            if obj.parent is None:
                world = obj.matrix_world.copy()
                obj.parent = arm_obj
                obj.parent_type = "OBJECT"
                obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
                obj.matrix_world = world
            obj["sc_model_iex_static_geom"] = True
            fixed += 1
        except Exception as exc:
            _log("[SC_MODEL_IEX] static mesh %s not bound: %s" % (obj.name, exc))
    if fixed:
        _log("[SC_MODEL_IEX] static geometry bound to bone %s: meshes %d"
             % (target, fixed))
    return fixed


def _cleanup_subrig_groups():
    """Remove foreign bone groups from the meshes of self-animating details.

    A detail must be deformed ONLY by the bones of its own armature. If a
    `main` group (or any ship bone) remains on its meshes, the vertices get
    a second weight: the detail either stops rotating or rotates together with
    the hull. Here this is enforced strictly — regardless of which
    binding pass has already run.
    """
    fixed = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or not obj.get("sc_model_iex_subrig"):
            continue
        rig = None
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.object is not None and m.object.type == "ARMATURE":
                rig = m.object
        if rig is None:
            continue
        own = {b.name for b in rig.data.bones}
        for vg in list(obj.vertex_groups):
            if vg.name not in own:
                _log("[SC_MODEL_IEX] %s: removed a foreign group %r" % (obj.name, vg.name))
                obj.vertex_groups.remove(vg)
                fixed += 1
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.object is not rig:
                m.object = rig
    if fixed:
        _log("[SC_MODEL_IEX] detail group cleanup: removed %d" % fixed)
    return fixed


def _is_our_part(obj_name, stem, folder):
    """Whether the object belongs to the parts of this ship.

    Part names vary: `race4_l_phoenix_capacitor` (with the ship
    name), `r4_l_phoenix_boost` (a different abbreviation) and `r4_l_eng` (a shared
    class detail). The previous check "starts with the model name" discarded
    the last two groups — that is why the Phoenix engines neither moved nor
    were duplicated.
    """
    base = (obj_name or "").split(".mdl-msh")[0]
    if not base:
        return False
    if base.lower() == (stem or "").lower():
        return False          # this is the mesh of the model itself, not a part
    if base.lower().startswith((stem or "").lower()):
        return True
    if _same_ship(stem, base):
        return True
    return _is_generic_part(base, _folder_prefixes(folder))


def _shift_one_part(obj, vec):
    """Shift the vertices of one part object by vec (in Blender coordinates).

    IMPORTANT: the shift is remembered in the `sc_model_iex_offset` property. The import shifts
    the detail vertices to its bone position, and the inverse conversion on export cannot be
    performed otherwise — the detail would move a second time (see dbg_roundtrip_export.py).
    """
    try:
        obj["sc_model_iex_offset"] = [float(vec[0]), float(vec[1]), float(vec[2])]
    except Exception:
        pass
    vx, vy, vz = vec
    if vx == 0.0 and vy == 0.0 and vz == 0.0:
        return False
    mesh = obj.data
    verts = mesh.vertices
    co = [0.0] * (len(verts) * 3)
    verts.foreach_get("co", co)
    for i in range(len(verts)):
        co[i * 3] += vx
        co[i * 3 + 1] += vy
        co[i * 3 + 2] += vz
    verts.foreach_set("co", co)
    mesh.update()
    _log("[SC_MODEL_IEX] offset %s: %s" % (obj.name, tuple(round(v, 2) for v in (vx, vy, vz))))
    return True


def _bones_for_category(sk_info, category):
    """ALL bones of the main skeleton for the category, in order.

    A part from the game is ONE node, but there may be several mounting points
    (Phoenix has the `engine_1` and `engine_2` bones): so the detail must be created
    as many times as there are bones of this category.
    """
    if not category or not sk_info:
        return []
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    low = category.lower()
    exact = [b for b in bones if (b.get("name") or "").lower() == low]
    if exact:
        return exact
    return [b for b in bones if (b.get("name") or "").lower().startswith(low)]


def _bones_for_category(sk_info, category):
    """Main-skeleton bones for the category: the "mount points" of the detail.

    Strict match: bone name = category, or category + number
    (`engine_1`, `engine_2`, `boost_1…`). A prefix without a number is not acceptable:
    otherwise for `cap` all `cap_anim_jnt1…11` would get into the list (for Phoenix that
    produced 13 copies of the reactor). If there are no strict matches — we take one bone
    by the usual prefix rule.
    """
    if not category or not sk_info:
        return []
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    low = category.lower()
    exact = [b for b in bones if (b.get("name") or "").lower() == low]
    if exact:
        return exact
    numbered = []
    for b in bones:
        name = (b.get("name") or "").lower()
        if not name.startswith(low):
            continue
        rest = name[len(low):]
        if rest and (rest[0] in "_-"):
            rest = rest[1:]
        if rest.isdigit():
            numbered.append(b)
    if numbered:
        return numbered
    single = _bone_for_category(sk_info, category)
    return [single] if single else []


def _shift_parts(sk_info, stem, enabled=True, offsets=None, folder=None):
    """Shift the part vertices to their bone position — REGARDLESS of the binding.

    The bone is taken by the same algorithm as for the binding: category from the root
    bone of the part skeleton (`root_engine` -> `engine`), then a bone lookup in the
    main skeleton by name/prefix. If there is no category (`__World__`),
    there is no shift either — the part stands where it is drawn.

    If the mesh has an entry in part_offsets.json, that one is used.
    """
    if not enabled:
        return 0
    offsets = offsets or {}
    moved = 0
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH":
            continue
        if _is_skinned(obj):
            continue
        if not _is_our_part(obj.name, stem, folder):
            continue
        if obj.get("sc_model_iex_subrig"):
            continue          # a detail with its own armature — we do not shift it
        vec = offsets.get(obj.name)
        if vec is None:
            part_sk_s = _part_skeleton(bpy.context, folder, obj.name)
            category = _part_category(part_sk_s)
            if not category and part_sk_s is None:
                # the detail skeleton is missing entirely (incomplete cache) — category by name
                category = _category_from_name(obj.name)
            bones = _bones_for_category(sk_info, category) if sk_info else []
            if not bones:
                _log("[SC_MODEL_IEX] part %s: category %r, no bone — offset not applied"
                     % (obj.name, category or "—"))
                continue
            # IMPORTANT: we take the ABSOLUTE bone position (the sum over the chain), not
            # the raw record field — that one is parent-relative. Otherwise the part is raised
            # to the parent position instead of its own (the nacelles end up lower
            # than in the game).
            absolute = _skeleton_absolute(sk_info)
            # if there are several places (engine_1, engine_2) — duplicate the detail,
            # but only when the base consists of ONE mesh: otherwise the base already
            # holds all nodes (Confessor has 4 meshes for 4 places)
            base_stem = obj.name.split(".mdl-msh")[0]
            same_base = [o for o in bpy.context.scene.objects
                         if o.type == "MESH" and o.name.split(".mdl-msh")[0] == base_stem]
            targets = [(obj, bones[0])]
            if len(bones) > 1 and len(same_base) <= 1:
                for k, b_extra in enumerate(bones[1:], start=2):
                    try:
                        dup = obj.copy()
                        dup.data = obj.data.copy()
                        dup.name = "%s.i%d" % (obj.name, k)
                        bpy.context.collection.objects.link(dup)
                        targets.append((dup, b_extra))
                    except Exception as exc:
                        _log("[SC_MODEL_IEX] copy of part %s not created: %s" % (obj.name, exc))
            if len(targets) > 1:
                _log("[SC_MODEL_IEX] part %s duplicated across bones %s"
                     % (obj.name, ", ".join(b.get("name") for _, b in targets)))
            for target_obj, bone in targets:
                try:
                    target_obj["sc_model_iex_bone"] = bone.get("name") or ""
                except Exception:
                    pass
                p = (absolute.get(bone.get("index")) or bone.get("position")
                     or (0.0, 0.0, 0.0))
                _shift_one_part(target_obj, (-p[0], -p[2], p[1]))
                moved += 1
            continue
        if not vec or (vec[0] == 0.0 and vec[1] == 0.0 and vec[2] == 0.0):
            continue
        try:
            obj["sc_model_iex_offset"] = [float(vec[0]), float(vec[1]), float(vec[2])]
        except Exception:
            pass
        mesh = obj.data
        verts = mesh.vertices
        co = [0.0] * (len(verts) * 3)
        verts.foreach_get("co", co)
        vx, vy, vz = vec[0], vec[1], vec[2]
        for i in range(len(verts)):
            co[i * 3] += vx
            co[i * 3 + 1] += vy
            co[i * 3 + 2] += vz
        verts.foreach_set("co", co)
        mesh.update()
        moved += 1
        _log("[SC_MODEL_IEX] offset %s: %s" % (obj.name,
                                     tuple(round(v, 2) for v in (vx, vy, vz))))
    _log("[SC_MODEL_IEX] part offset: objects %d" % moved)
    return moved


def _place_part_at_bone(obj, arm_obj, bone_name):
    """Поставить деталь в точку крепления (bind-матрица кости).

    Детали авторы лежат вокруг своего нуля (их локальные координаты = пространство
    кости-крепежа), поэтому после привязки к кости их надо перенести в её rest-матрицу.
    Если геометрия уже в мировых координатах корабля — не трогаем.
    """
    from mathutils import Vector
    if obj.get("sc_model_iex_placed"):
        return False
    bone = arm_obj.data.bones.get(bone_name) if bone_name else None
    if bone is None:
        return False
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    cx = sum(p.x for p in pts) / len(pts)
    cy = sum(p.y for p in pts) / len(pts)
    cz = sum(p.z for p in pts) / len(pts)
    if not PART_PLACE_DETAILS:
        _log("[SC_MODEL_IEX] part %s: оставлена на месте (галочка выключена)" % obj.name)
        return False
    if (cx * cx + cy * cy + cz * cz) ** 0.5 > 2.0:
        _log("[SC_MODEL_IEX] part %s: геометрия уже в мировых координатах" % obj.name)
        return False
    obj.matrix_world = arm_obj.matrix_world @ bone.matrix_local
    obj["sc_model_iex_placed"] = True
    _log("[SC_MODEL_IEX] part %s: поставлена на кость %s (%.2f, %.2f, %.2f)"
         % (obj.name, bone.name, bone.head_local.x, bone.head_local.y, bone.head_local.z))
    return True


def _spread_parts(arm_obj, bound):
    """Размножить деталь по всем костям её категории (boost_1..N, engine_1..N).

    В модели корабля может быть несколько точек крепления одной категории
    (например boost_1, boost_2, boost_3), а файл детали — один. Каждая копия
    ставится в свою кость и привязывается к ней.
    """
    import re
    by_stem = {}
    for name, bone_name in list(bound.items()):
        stem = name.split(".mdl-msh")[0]
        by_stem.setdefault(stem, []).append((name, bone_name))
    added = 0
    for stem, items in by_stem.items():
        first = items[0][1]
        m = re.match(r"([A-Za-z_]+?)_?(\d+)$", first or "")
        if not m:
            continue
        cat = m.group(1)
        pattern = re.compile(r"^%s_?\d+$" % re.escape(cat), re.I)
        targets = [b.name for b in arm_obj.data.bones if pattern.match(b.name)]
        if len(targets) < 2:
            continue
        for tgt in targets[1:]:
            bone = arm_obj.data.bones.get(tgt)
            if bone is None:
                continue
            for name, _b in items:
                src = bpy.data.objects.get(name)
                if src is None:
                    continue
                dup = src.copy()
                dup.data = src.data.copy()
                dup.name = "%s_%s" % (name, tgt)
                bpy.context.scene.collection.objects.link(dup)
                dup.matrix_world = arm_obj.matrix_world @ bone.matrix_local
                for g in list(dup.vertex_groups):
                    if re.match(r"^%s_?\d+$" % re.escape(cat), g.name, re.I):
                        g.name = tgt
                dup["sc_model_iex_bone"] = tgt
                dup["sc_model_iex_placed"] = True
                added += 1
        _log("[SC_MODEL_IEX] деталь %s размножена на %d точек крепления"
             % (stem, len(targets)))
    return added


def _bind_rigid(context, arm_obj, stem, sk_info, folder=None):
    """Rigid binding of a part to a bone — via a vertex group, NOT via BONE parenting.

    Why not `parent_type='BONE'`: such parenting attaches the object to the TAIL
    of the bone, i.e. the object rotates around the tail and not around the head. For bones
    0.25 long this gave a shift of a third of a meter: the nacelles, when rotated by 90°,
    drifted sideways and stopped being coaxial with the hull (visible on Gungnir).
    A vertex group + Armature modifier rotates the vertices around the bone head —
    exactly like the game.

    The binding is by ALGORITHM, without tables for specific objects: the category is taken
    from the root bone of the part skeleton itself (`root_energy` does not exist, there are
    `root_engine`/`root_eng` -> `engine`, `root_boost` -> `boost`, `root_cap` ->
    `cap`), then we look for a main-skeleton bone with that name, and if there is none —
    with that prefix. If the part root is `__World__` (or not `root*` at all),
    there is no binding — the part is static and is mounted on `main`.
    """
    bones = [b for b in (sk_info.get("bones") or []) if isinstance(b, dict)]
    from mathutils import Vector
    bound = {}
    main_bone = next((b for b in bones if b.get("name", "").lower() == "main"), None)
    for obj in list(bpy.context.scene.objects):
        if obj.type != "MESH" or _is_skinned(obj):
            continue
        if not _is_our_part(obj.name, stem, folder):
            continue
        if obj.get("sc_model_iex_subrig"):
            continue          # a detail with its own armature — no binding needed
        part_sk = _part_skeleton(context, folder, obj.name)
        category = _part_category(part_sk)
        # We take the category by NAME only if the detail skeleton is missing ENTIRELY
        # (incomplete cache). If the skeleton exists but the root is `__World__` — the detail
        # is drawn in world coordinates, and its place is on the ship root (main):
        # that is how, for example, `race1_l_t5_sniper_cap` of Gungnir is built.
        if not category and part_sk is None:
            category = _category_from_name(obj.name)
        # a copy of the detail already knows its bone (the detail was duplicated over the places)
        forced = ""
        try:
            forced = obj.get("sc_model_iex_bone") or ""
        except Exception:
            forced = ""
        bone = None
        if forced:
            bone = next((b for b in bones
                         if (b.get("name") or "").lower() == forced.lower()), None)
        if bone is None:
            bone = _bone_for_category(sk_info, category)
        if bone is None:
            # either a part without binding (__World__), or the bone was not found
            bone = main_bone
        if bone is None or bone.get("name") not in arm_obj.data.bones:
            # Детали из чужих паков: нужной кости может не быть в скелете корабля.
            # Раньше такая деталь оставалась без привязки и лежала рядом с кораблём;
            # теперь сажаем её на root (или на первую кость), чтобы она двигалась
            # вместе с корпусом.
            alt = next((b for b in bones
                        if (b.get("name") or "").lower() == "root"), None)
            if alt is None and bones:
                alt = bones[0]
            if alt is None or alt.get("name") not in arm_obj.data.bones:
                _log("[SC_MODEL_IEX] part %s: подходящей кости нет — пропуск" % obj.name)
                continue
            _log("[SC_MODEL_IEX] part %s: фолбэк на кость %s"
                 % (obj.name, alt.get("name")))
            bone = alt
        try:
            _place_part_at_bone(obj, arm_obj, bone.get("name"))
        except Exception as exc:
            _log("[SC_MODEL_IEX] part %s: не удалось поставить на кость: %s"
                 % (obj.name, exc))
        bound[obj.name] = bone.get("name")
        _log("[SC_MODEL_IEX] part %s: root %r -> category %r -> bone %s"
             % (obj.name, _part_root_name(part_sk), category or "—",
                bone.get("name")))

    # --- re-hanging by bounds nesting ---
    made = 0
    for name, bone_name in bound.items():
        obj = bpy.context.scene.objects.get(name)
        if obj is None:
            continue
        vg = obj.vertex_groups.get(bone_name) or obj.vertex_groups.new(name=bone_name)
        vg.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
        if obj.modifiers.get("SC_MODEL_IEX_armature") is None:
            mod = obj.modifiers.new("SC_MODEL_IEX_armature", "ARMATURE")
            mod.object = arm_obj
            mod.use_vertex_groups = True
            mod.use_bone_envelopes = False
        if obj.parent is None:
            world = obj.matrix_world.copy()
            obj.parent = arm_obj
            obj.parent_type = "OBJECT"
            obj.matrix_parent_inverse = arm_obj.matrix_world.inverted()
            obj.matrix_world = world
        made += 1
    _log("[SC_MODEL_IEX] rigid binding (vertex group): objects %d" % made)
    try:
        _spread_parts(arm_obj, bound)
    except Exception as exc:
        _log("[SC_MODEL_IEX] размножение деталей не удалось: %s" % exc)
    return made


def _model_base_from_pak(P, pak_path, work):
    """Which model to take from the pak.

    Scanning the cache for "take the first" is not allowed: parts from other
    paks are loaded into it, and `misc.pak` brings map objects — the "first model" turned out to be
    s1340_01 instead of the ship. Therefore we match the pak name with the model names
    (ignoring underscores) and prefer the `models/ships/…` path.
    """
    pak_key = os.path.splitext(os.path.basename(pak_path))[0].lower().replace("_", "")
    cands = []
    for found in _walk_mdl_hdr(work):
        rel = found[len(work):].lstrip("\\/").replace("/", "\\")
        low = rel.lower()
        stem = os.path.basename(found)[:-len(".mdl-hdr")]
        score = 0
        if low.startswith("models\\ships\\"):
            score += 4
        elif low.startswith("models\\"):
            score += 2
        if stem.lower().replace("_", "") in pak_key:
            score += 8
        cands.append((score, rel))
    if not cands:
        return None
    cands.sort(key=lambda t: (-t[0], t[1]))
    rel = cands[0][1]
    base = os.path.join(work, rel)[:-len(".mdl-hdr")]
    _log("[SC_MODEL_IEX] model from pak: %s" % rel)
    return base


def _walk_mdl_hdr(root):
    out = []
    for dp, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".mdl-hdr"):
                out.append(os.path.join(dp, f))
    return out


def _folder_prefixes(folder):
    """Prefixes of the shared class parts for the model folder.

    `models/ships/race_4/l` -> {'race_4_l', 'race4_l'} — this folder holds
    the shared details `r4_l_eng`, `race4_l_boost` etc. used by all
    ships of the class, including those that have no detail of their own (Phoenix).
    """
    parts = [c for c in (folder or "").replace("\\", "/").split("/") if c]
    if len(parts) < 2:
        return set()
    race, cls = parts[-2], parts[-1]
    out = {("%s_%s" % (race, cls)).lower(),
           ("%s_%s" % (race.replace("_", ""), cls)).lower()}
    # short form: race_4 -> r4 (in the data the shared details are called r4_l_eng)
    digits = "".join(ch for ch in race if ch.isdigit())
    if digits:
        out.add(("r%s_%s" % (digits, cls)).lower())
    return out


def _is_generic_part(name, prefixes):
    "Shared class detail: name = <prefix>_<part>, without the ship name."
    if not prefixes:
        return False
    return _strip_part_suffix(name or "").lower() in prefixes


def _report_missing_siblings(context, folder, stem, present):
    """Tell which ship parts are missing from the folder and in which pak they live.

    Needed when the model is imported from an UNPACKED folder: the ship parts
    may live in other paks and not get into this folder (for Phoenix this is how
    the `r4_l_phoenix_boost` engines from models_ships_race_4_l.pak went missing).
    """
    ipath = _model_index_file(context)
    if not ipath or not os.path.exists(ipath):
        return
    try:
        with open(ipath, encoding="utf-8") as fh:
            models = (json.load(fh).get("models") or {})
    except Exception:
        return
    have = {(n or "").lower() for n in present}
    # the folder comes as an absolute path inside the cache, while in the index the path is
    # relative — we compare the tail of the directory by three components
    # (models\ships\race_4\l)
    def _tail3(s):
        parts = [c for c in (s or "").replace("/", "\\").split("\\") if c]
        return "\\".join(parts[-3:]).lower()

    folder_tail = _tail3(folder)
    missing = []
    for path, info in models.items():
        p = path.replace("/", "\\")
        if _tail3(os.path.dirname(p)) != folder_tail:
            continue
        other = os.path.basename(p)
        if other.lower() == stem.lower() or not _same_ship(stem, other):
            continue
        if any(s in other.lower() for s in SKIP_SIBLINGS):
            continue
        if other.lower() in have or os.path.exists(os.path.join(folder, other + ".mdl-hdr")):
            continue
        missing.append((other, (info or {}).get("pak") or "?"))
    if missing:
        _log("[SC_MODEL_IEX] no parts of this ship in the folder: %s"
             % ", ".join("%s (%s)" % (n, pk) for n, pk in missing))
        _log("[SC_MODEL_IEX] these files live in other paks — import the model via \"Star Conflict model from .pak\", then they will be loaded automatically")


def _restore_view_state(objs=None, arm_obj=None, prefer_armature=False):
    """Return Blender to a normal state after the import.

    Why: if the active object turned out to be deleted (or nothing remained —
    for example an import without a skeleton), `context.object` becomes None and the
    Object Data Properties tab disappears from Properties. Externally this looks
    like "the panel disappeared when importing the ship". Here we always leave a live
    active object in object mode.
    """
    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    # We leave the MESH active, not the armature: after importing a detail the armature
    # made itself active and the usual tab disappeared from Properties —
    # Object Data Properties (an armature shows the skeleton panel in its place).
    # The exception is importing all animations: there the ARMATURE must be active, otherwise
    # the Action Editor works with the mesh and switching actions changes nothing.
    target = None
    if prefer_armature and arm_obj is not None and arm_obj.name in bpy.data.objects:
        target = arm_obj
    for o in ([] if target is not None else (objs or [])):
        if o is not None and o.name in bpy.data.objects and o.type == "MESH":
            target = o
            break
    if target is None and arm_obj is not None and arm_obj.name in bpy.data.objects:
        target = arm_obj
    if target is None:
        for o in (objs or []):
            if o is not None and o.name in bpy.data.objects:
                target = o
                break
    if target is None:
        for o in bpy.context.scene.objects:
            if o.type == "MESH":
                target = o
                break
    if target is None:
        return
    try:
        target.hide_viewport = False
        target.hide_select = False
        target.hide_set(False)
        for o in list(bpy.context.selected_objects):
            o.select_set(False)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.context.view_layer.update()
    except Exception as exc:
        _log("[SC_MODEL_IEX] view state not restored: %s" % exc)


LAST_BIND_NOTE = ""
LAST_IMPORT_OBJECTS = []
LAST_IMPORT_ARM = None
LAST_PREFER_ARM = False


def _report_bind_state(stem):
    """Print the actual binding state to the log — for diagnostics.

    The user sees "the main group rotates", although by the code it should not
    happen. Let the log show what actually lies on the objects: name,
    parent, modifier armature and groups with vertex counts. We put the summary briefly
    into LAST_BIND_NOTE — the operator prints it into the Info panel, because the user
    does not always open SC_MODEL_IEX_log.
    """
    global LAST_BIND_NOTE
    LAST_BIND_NOTE = ""
    low = (stem or "").lower()
    if not low:
        return
    foreign = []
    subrig = 0
    for obj in sorted(bpy.context.scene.objects, key=lambda o: o.name):
        if obj.type != "MESH" or not obj.name.lower().startswith(low):
            continue
        if obj.get("sc_model_iex_subrig"):
            subrig += 1
        groups = []
        for g in obj.vertex_groups:
            n = sum(1 for v in obj.data.vertices
                    if any(x.group == g.index and x.weight > 0.5 for x in v.groups))
            groups.append("%s:%d" % (g.name, n))
        rig = ""
        for m in obj.modifiers:
            if m.type == "ARMATURE" and m.object is not None:
                rig = m.object.name
        _log("[SC_MODEL_IEX] binding %s | parent %s | armature %s | groups: %s"
             % (obj.name, obj.parent.name if obj.parent else "—",
                rig or "—", ", ".join(groups) or "none"))
        if "main" in [g.name for g in obj.vertex_groups] and \
                not obj.name.lower().startswith(low + ".mdl-msh"):
            foreign.append(obj.name)
            _log("[SC_MODEL_IEX] WARNING: object %s has a 'main' group although it is not the hull — the object is bound to the ship skeleton" % obj.name)
    if foreign:
        LAST_BIND_NOTE = " | WARNING: main at %s" % ", ".join(foreign[:3])
    else:
        LAST_BIND_NOTE = " | details with their own armature: %d" % subrig


def _model_rel_of(base):
    """models/ships/race_4/l/race4_l_phoenix из абсолютного пути (или '')."""
    p = (base or "").replace("\\", "/")
    low = p.lower()
    i = low.rfind("/models/")
    if i < 0:
        return ""
    return p[i + 1:].rsplit(".mdl-", 1)[0]


def _rehome_parts(objs, arm_obj, from_bone, to_bone):
    """Перевесить копию детали на СВОЮ кость: сдвиг вершин + группа + parent bone.

    `_bind_rigid` вешает деталь на первую кость категории (`boost_1`), поэтому все
    копии оказываются в одной точке. Здесь копия переезжает на `to_bone`.
    """
    from mathutils import Vector
    if arm_obj is None or not from_bone or not to_bone or from_bone == to_bone:
        return 0
    b_from = arm_obj.data.bones.get(from_bone)
    b_to = arm_obj.data.bones.get(to_bone)
    if b_from is None or b_to is None:
        return 0
    delta = (arm_obj.matrix_world @ b_to.head_local) - (arm_obj.matrix_world @ b_from.head_local)
    if delta.length == 0.0:
        return 0
    for ob in objs:
        for vg in ob.vertex_groups:
            vg.name = to_bone
        if getattr(ob, "parent_type", "") == "BONE":
            try:
                ob.parent_bone = to_bone
            except Exception:
                pass
        me = ob.data
        for v in me.vertices:
            v.co = v.co + delta
        me.update()
    return len(objs)


def _bind_rotate(objs, rot3, scale=1.0, arm_obj=None, bone_name="", pivot=None):
    """Повернуть вершины части вокруг точки крепления (игровой поворот `bind`).

    Перенос уже сделан штатной привязкой (`_bind_rigid` сдвигает вершины к кости и
    вешает объект на риг), поэтому здесь НЕЛЬЗЯ трогать location — иначе деталь
    уезжает вдвое. Поворот берётся из `bind` (3×3, столбцами) и переводится в оси
    Blender: R_b = C · R_g · C^T, C: (x,y,z) -> (-x, z, -y).
    """
    from mathutils import Matrix, Vector
    C = Matrix(((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
    Rg = Matrix(((rot3[0][0], rot3[0][1], rot3[0][2]),
                 (rot3[1][0], rot3[1][1], rot3[1][2]),
                 (rot3[2][0], rot3[2][1], rot3[2][2])))
    Rb = C @ Rg @ C.transposed()
    if abs(Rb.to_quaternion().w) > 0.999999:
        return 0                                   # поворот единичный — не трогаем
    if pivot is None:
        pivot = Vector((0.0, 0.0, 0.0))
        if arm_obj is not None and bone_name:
            bone = arm_obj.data.bones.get(bone_name)
            if bone is not None:
                pivot = arm_obj.matrix_world @ bone.head_local
    done = 0
    for ob in objs:
        me = ob.data
        for v in me.vertices:
            v.co = Rb @ (v.co - pivot) + pivot
        me.update()
        done += 1
    return done


def _auto_forward(arm_obj=None):
    """Проверить «перед» модели по костям и развернуть, если она летит спиной.

    Соглашение игры: +X вправо, +Y вверх, -Z вперёд (нос), +Z назад (двигатели).
    В Blender: game Z = -Blender Y. Значит выхлопы/двигатели должны иметь
    БОЛЬШИЙ Blender Y, чем курсовые пушки. Если наоборот — модель развёрнута
    на 180°, и это лечится поворотом вокруг вертикали (Blender Z).
    Возвращает True, если повернули.
    """
    import math
    from mathutils import Matrix
    if arm_obj is None:
        return False
    rear_names = ("engine", "boost", "exhaust", "thruster", "nozzle")
    front_names = ("gun", "weapon", "cannon", "laser", "rocket")
    rear, front = [], []
    for b in arm_obj.data.bones:
        low = (b.name or "").lower()
        y_bl = (arm_obj.matrix_world @ b.head_local).y
        if any(low.startswith(n) or ("_" + n) in low for n in rear_names):
            rear.append(y_bl)
        elif any(low.startswith(n) or ("_" + n) in low for n in front_names):
            front.append(y_bl)
    if len(rear) < 1 or len(front) < 1:
        _log("[SC_MODEL_IEX] auto forward: не нашёл кости двигателей/орудий — пропуск")
        return False
    r = sum(rear) / len(rear)
    f = sum(front) / len(front)
    _log("[SC_MODEL_IEX] auto forward: двигатели Y=%.2f, пушки Y=%.2f" % (r, f))
    # game +Z (хвост) -> Blender -Y: у ПРАВИЛЬНОЙ модели двигатели имеют
    # МЕНЬШИЙ Blender Y, чем курсовые пушки.
    if r <= f:
        return False                       # двигатели сзади — всё верно
    R = Matrix.Rotation(math.radians(180), 4, "Z")
    arm_obj.matrix_world = R @ arm_obj.matrix_world
    for o in bpy.context.scene.objects:
        if o.type == "MESH" and o.parent == arm_obj:
            o.matrix_world = R @ o.matrix_world
    _log("[SC_MODEL_IEX] auto forward: модель была развёрнута — поворот 180° вокруг Z")
    return True


def _auto_orient(objs, arm_obj=None):
    """Повернуть модель, если её длинная ось не лежит по Y (Blender).

    Часть моделей (пропсы, станции, отдельные корабли) свёрстана не Y-up, и после
    импорта ложится на бок. Длину определяем по общему bbox.
    """
    import math
    from mathutils import Matrix, Vector
    objs = [o for o in (objs or []) if o is not None and o.name in bpy.data.objects]
    if not objs and arm_obj is None:
        return None
    pts = []
    for o in objs:
        pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    if not pts:
        return None
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    size = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    # Осторожная эвристика: у высоких объектов (башни, мачты) длинная ось — Y,
    # и это нормально. Поворачиваем только при ЯВНОМ перевесе другой оси.
    LIMIT = 1.35
    if size[1] * LIMIT >= max(size[0], size[2]):
        return None                      # длина по Y или габариты близки — не трогаем
    rot = None
    if size[2] >= size[0] * LIMIT and size[2] >= size[1] * LIMIT:
        rot = Matrix.Rotation(math.radians(-90), 4, "X")   # явно стоял на хвосте
        what = "X -90"
    elif size[0] >= size[1] * LIMIT and size[0] >= size[2] * LIMIT:
        rot = Matrix.Rotation(math.radians(90), 4, "Z")    # явно лежал на боку
        what = "Z +90"
    else:
        return None
    target = arm_obj if arm_obj is not None else objs[0]
    target.matrix_world = rot @ target.matrix_world
    _log("[SC_MODEL_IEX] auto axes: поворот %s (модель была не Y-up)" % what)
    return what


def _import_base(context, M, MAT, TEX, base_path, scale, apply_materials,
                 axes="yup", lod=0, flip_v=True, dye_name=_L(""), dye_layer=0,
                 paint_mode="dye", decal_name=_L(""), collect_parts=True, _seen=None,
                 use_bones=True, bone_offset=None, brightness=1.0,
                 import_skeleton=True, pose_name=_L(""), part_offsets=True,
                 anim_name=_L(""), anim_fps=0.0, part_anim_flip=False, on_mesh=None):
    if _seen is None:
        _log_reset()
    "Assemble the model from .mdl-msh### of the needed LOD next to the base."
    base = _base_of(base_path)
    meshes = _find_meshes(M, base)
    if not meshes:
        raise RuntimeError(
            """no .mdl-msh### found for the base:
  %s
Check that files like <name>.mdl-msh000 and so on are located next to it.""" % base)

    if _seen is None:
        try:
            _log_ship_effects(context, base)
        except Exception as exc:
            _log("[SC_MODEL_IEX] effects not checked: %s" % exc)

    # LOD: by default we take only level 0 (the most detailed), otherwise
    # all levels get into the scene at once and overlap each other.
    levels = None
    try:
        levels = M.lod_groups(base)
    except Exception as exc:
        _log("[SC_MODEL_IEX] LOD groups not obtained: %s" % exc)
    if levels:
        if lod >= len(levels):
            _log("[SC_MODEL_IEX] LOD %d does not exist, using 0 (%d levels total)"
                  % (lod, len(levels)))
            lod = 0
        if lod >= 0:
            meshes = levels[lod]
            _log("[SC_MODEL_IEX] LOD %d of %d: meshes %d" % (lod, len(levels), len(meshes)))
        else:
            _log("[SC_MODEL_IEX] importing all LODs (%d levels)" % len(levels))

    tex_dir = os.path.dirname(base)
    cache = _texture_cache_dir(context)
    # the game root is needed to find textures by the full paths from .mdf
    prefs = _addon_prefs(context)
    game_root = _game_root(prefs)
    if game_root and not os.path.isdir(game_root):
        _log("[SC_MODEL_IEX] game root unavailable: %s" % game_root)
        game_root = ""
    if not game_root:
        _log("[SC_MODEL_IEX] game data folder not found — set it in the add-on preferences")
    tex_index = _texture_index(game_root)
    index_data = _load_index(context)
    if index_data and not index_data.get("game_data") and game_root:
        index_data["game_data"] = game_root
    dye, dye_layer = _resolve_dye(context, dye_name, dye_layer)
    decal = _resolve_decal(context, decal_name)
    mats = []
    if apply_materials:
        try:
            loaded = MAT.load_materials(base)
        except Exception as exc:
            _log("[SC_MODEL_IEX] materials not loaded: %s" % exc)
            loaded = []
        # load_materials returns a tuple, the first element is the list of descriptions
        items = loaded
        if isinstance(items, tuple):
            items = items[0] if items else []
        if isinstance(items, dict):
            items = list(items.values())
        items = list(items or [])
        paint = _default_paint(context, os.path.basename(base)) if paint_mode != "none" else None
        armor = _armor_entry(context, getattr(_addon_prefs(context), "armor_name", ""))
        for i, mdef in enumerate(items):
            mat, bound = _build_material(context, MAT, TEX, os.path.basename(base),
                                        mdef, tex_dir, cache, i,
                                        game_root=game_root, tex_index=tex_index,
                                        index_data=index_data,
                                        dye=dye, dye_layer=dye_layer,
                                        paint_mode=paint_mode, decal=decal,
                                        brightness=brightness, paint=paint, armor=armor)
            mats.append(mat)
            if bound:
                _log("[SC_MODEL_IEX] material %s: %d textures" % (mat.name, bound))

    # which mesh belongs to which LOD (for lod=-1 we load all levels)
    lod_of = {}
    if levels:
        for li, group in enumerate(levels):
            for p in group:
                lod_of[os.path.abspath(p)] = li
    total_v = total_t = 0
    objs = []
    for msh in meshes:
        name = os.path.basename(msh)
        obj, nv, nt = _msh_to_mesh(M, msh, name, axes, MAT, mats, flip_v)
        if not obj:
            continue
        if scale and scale != 1.0:
            obj.scale = (scale, scale, scale)
        if bone_offset:
            obj.location = bone_offset
        lod_idx = lod if lod >= 0 else lod_of.get(os.path.abspath(msh), 0)
        try:
            obj["sc_lod"] = int(lod_idx)
            obj["sc_src_msh"] = os.path.abspath(msh)
            coll_name = "LOD%d" % lod_idx
            coll = bpy.data.collections.get(coll_name)
            if coll is None:
                coll = bpy.data.collections.new(coll_name)
            if coll.name not in context.scene.collection.children:
                context.scene.collection.children.link(coll)
            for c in list(obj.users_collection):
                if c is not coll:
                    c.objects.unlink(obj)
            if obj.name not in coll.objects:
                coll.objects.link(obj)
        except Exception as exc:
            _log("[SC_MODEL_IEX] LOD collection not created: %s" % exc)
        objs.append(obj)
        total_v += nv
        total_t += nt
        if on_mesh is not None:
            try:
                on_mesh(len(objs), len(meshes))
            except Exception:
                pass
    _log("[SC_MODEL_IEX] imported: objects %d, vertices %d, polygons %d"
          % (len(objs), total_v, total_t))
    result = (len(objs), total_v, total_t)

    # Armature from .mdl-skl and static pose (.anim) — only for the main base
    arm_obj = None
    sk_main = None
    if _seen is None:
        if import_skeleton:
            sk_main = _load_skeleton(M, base)
            if sk_main:
                arm_obj = _build_armature(context, sk_main, os.path.basename(base), scale)
            if arm_obj is not None:
                try:
                    arm_obj["sc_src_base"] = os.path.abspath(base)
                except Exception:
                    pass
        # The pose NO LONGER rotates objects: _apply_pose used to rotate only
        # the hull meshes (parts are imported later) and the assembled ship fell apart.
        # Now the pose is applied to the BONES in the same place as the animation — below,
        # after the parts are bound.

    # A ship in the game is SEVERAL bases in ONE folder: the hull, _eng, _cap,
    # _boost, _dmg and other variants. We import ALL bases from the folder of the chosen
    # model, not only the ones matching by prefix: different ships have a different set of
    # suffixes, and the "same prefix" rule left parts out.
    if collect_parts:
        seen = _seen if _seen is not None else set()
        seen.add(os.path.normcase(base))
        folder = os.path.dirname(base)
        # if the PART itself is imported (`..._capacitor`, `..._eng`), the hull and
        # the other parts must not be pulled in: otherwise the whole ship "grows" out of a detail
        if _strip_part_suffix(os.path.basename(base)) != os.path.basename(base):
            _log("[SC_MODEL_IEX] importing part %s — assembly of the other parts is disabled"
                 % os.path.basename(base))
            collect_parts = False
    if collect_parts:
        # the skeleton of the main model: from it we take the bone positions for the parts
        sk_info = _load_skeleton(M, base) if use_bones and _seen is None else None
        stem = os.path.basename(base)
        use_index_parts = bool(
            getattr(_addon_prefs(context), "parts_from_index", True)
            and (_load_index(context) or {}).get("models"))
        try:
            candidates = M.find_models(folder)
        except Exception as exc:
            _log("[SC_MODEL_IEX] additional parts not found: %s" % exc)
            candidates = []
        covered = set()   # the categories of the parts that this ship already has
        for cand in (() if use_index_parts else candidates):
            base2 = _base_of(cand)
            if os.path.normcase(base2) in seen:
                continue
            name2 = os.path.basename(base2)
            # we take only the parts of THIS ship: the folder is sometimes shared by several
            # models (models/ships/race_4/l), and before, everything got into the assembly
            if not _same_ship(stem, name2):
                continue
            if any(s in name2.lower() for s in SKIP_SIBLINGS):
                continue
            covered.add(_part_category(_load_skeleton(None, base2)))
            offset = None
            _log("[SC_MODEL_IEX] additional part: %s" % name2)
            n2, v2, t2 = _import_base(context, M, MAT, TEX, base2, scale,
                                      apply_materials, axes=axes, lod=lod,
                                      flip_v=flip_v, dye_name=dye_name,
                                      dye_layer=dye_layer, paint_mode=paint_mode,
                                      decal_name=decal_name, collect_parts=False,
                                      _seen=seen, use_bones=use_bones,
                                      bone_offset=offset)
            result = (result[0] + n2, result[1] + v2, result[2] + t2)
            # a self-animating detail (own skeleton + own .anim) — a separate
            # armature, otherwise nobody would rotate its bones (Phoenix reactor)
            try:
                if _part_own_anim(context, base2):
                    _setup_part_subrig(context, base2, arm_obj, sk_main,
                                       scale=scale, anim_fps=anim_fps,
                                       quat_flip=part_anim_flip)
                else:
                    # the part has bone indices but no animation of its own:
                    # its bones belong to IT, not to the ship — we will bind it rigidly
                    # to the category bone (_bind_rigid does that), but we will report
                    _log("[SC_MODEL_IEX] part %s has no .anim of its own — rigid binding to the category bone (the part bone indices are NOT applied to the ship skeleton)" % name2)
                    if _part_skeleton(context, os.path.dirname(base2), name2) is None:
                        _log("[SC_MODEL_IEX] WARNING: no %s.mdl-skl in the cache — the detail skeleton was not read. The detail will be rigid (without its own animation). Check the cache or import from the pak again"
                             % os.path.basename(base2))
            except Exception as exc:
                _log("[SC_MODEL_IEX] own armature for %s not created: %s" % (name2, exc))
        # Shared class parts: if the ship has no detail of its OWN for the needed
        # category (Phoenix has no `_eng`), the game takes the shared `<r4_l>_eng`
        # from the same folder — it lives in models_ships_race_4_l.pak.
        try:
            prefixes = _folder_prefixes(folder)
            for cand in (() if use_index_parts else candidates):
                base2 = _base_of(cand)
                if os.path.normcase(base2) in seen:
                    continue
                name2 = os.path.basename(base2)
                if not _is_generic_part(name2, prefixes):
                    continue
                if any(s in name2.lower() for s in SKIP_SIBLINGS):
                    continue
                cat = _part_category(_load_skeleton(None, base2))
                if not cat or cat in covered:
                    continue
                covered.add(cat)
                _log("[SC_MODEL_IEX] shared class part: %s (category %s)" % (name2, cat))
                n2, v2, t2 = _import_base(context, M, MAT, TEX, base2, scale,
                                          apply_materials, axes=axes, lod=lod,
                                          flip_v=flip_v, dye_name=dye_name,
                                          dye_layer=dye_layer,
                                          paint_mode=paint_mode,
                                          decal_name=decal_name,
                                          collect_parts=False, _seen=seen,
                                          use_bones=use_bones, bone_offset=None)
                result = (result[0] + n2, result[1] + v2, result[2] + t2)
        except Exception as exc:
            _log("[SC_MODEL_IEX] shared class parts not added: %s" % exc)
        _log("[SC_MODEL_IEX] model total: objects %d, vertices %d, polygons %d"
              % result)
        # --- parts from the index: the ones that live in OTHER paks (parts.py canon)
        try:
            if getattr(_addon_prefs(context), "parts_from_index", True):
                from sc_tools import parts as PARTS
                from sc_tools import skeleton as SK
                idx = _load_index(context) or {}
                pref2 = _addon_prefs(context)
                data_dir = bpy.path.abspath(getattr(pref2, "game_root", "") or "")
                cache_root = os.path.join(_texture_cache_dir(context), "_parts")
                rel_hull = _model_rel_of(base)
                hull_sk = _load_skeleton(M, base) if use_bones else None

                def _extract(pak_name, names):
                    pak = os.path.join(data_dir, pak_name)
                    if not os.path.exists(pak):
                        return {}
                    return _pak_extract(pak, names,
                                        os.path.join(cache_root, os.path.splitext(os.path.basename(pak_name))[0]))

                if rel_hull:
                    finder = PARTS.Finder(idx, _extract, getattr(pref2, "unpacked_root", ""))
                    covered2 = set(covered)
                    seen_names = {os.path.basename(_p).lower() for _p in seen}
                    for rel, _pak, _kind in PARTS.plan(finder, rel_hull):
                        name2 = os.path.basename(rel)
                        if any(s in name2.lower() for s in SKIP_SIBLINGS):
                            continue
                        local = finder.ensure(rel, cache_root)
                        if not local:
                            _log("[SC_MODEL_IEX] index part without files: %s (%s)" % (name2, _pak))
                            continue
                        if name2.lower() in seen_names:
                            continue
                        if any(_o.name.lower().startswith(name2.lower() + ".mdl-msh")
                               for _o in bpy.data.objects):
                            continue
                        seen_names.add(name2.lower())
                        cat = ""
                        try:
                            cat = PARTS.category_from_skeleton(SK.parse_skl(local + ".mdl-skl"))
                        except Exception:
                            cat = ""
                        if not cat:
                            cat = PARTS.category_from_name(name2)
                        if not cat or cat in covered2:
                            continue
                        bones = PARTS.bones_for_category(hull_sk, cat) if hull_sk else []
                        if not bones:
                            _log("[SC_MODEL_IEX] index part %s: no bone for category %s"
                                 % (name2, cat))
                            continue
                        covered2.add(cat)
                        covered.add(cat)
                        # кости-разметка иногда стоят в одной точке (r1_l_engineer:
                        # engine_1/boost_1/boost_2/capacitor_1 = (0, 0.22, 0)) —
                        # копии в одну точку с одинаковым поворотом не нужны
                        try:
                            if arm_obj is not None and len(bones) > 1:
                                _uniq, _seen_b = [], set()
                                for _b in bones:
                                    _bb = arm_obj.data.bones.get(_b.get("name") or "")
                                    _off, _rot = PARTS.bind_of(_b)
                                    _key = (tuple(round(v, 3) for v in _off),
                                            tuple(tuple(round(v, 3) for v in r) for r in _rot))
                                    if _bb is not None:
                                        _h = arm_obj.matrix_world @ _bb.head_local
                                        _key = (tuple(round(v, 3) for v in _h), _key[1])
                                    if _key in _seen_b:
                                        continue
                                    _seen_b.add(_key)
                                    _uniq.append(_b)
                                if len(_uniq) < len(bones):
                                    _log("[SC_MODEL_IEX] %s: кости категории %s совпадают, копий %d вместо %d"
                                         % (name2, cat, len(_uniq), len(bones)))
                                bones = _uniq
                        except Exception as exc:
                            _log("[SC_MODEL_IEX] дедупликация костей %s: %s" % (name2, exc))
                        first_bone = (bones[0].get("name") or "") if bones else ""
                        for _bi, bone in enumerate(bones):
                            off, rot = PARTS.bind_of(bone)
                            before = set(bpy.data.objects)
                            n2, v2, t2 = _import_base(
                                context, M, MAT, TEX, local, scale, apply_materials,
                                axes=axes, lod=lod, flip_v=flip_v, dye_name=dye_name,
                                dye_layer=dye_layer, paint_mode=paint_mode,
                                decal_name=decal_name, collect_parts=False,
                                _seen=seen, use_bones=use_bones, bone_offset=None)
                            fresh = [o for o in bpy.data.objects
                                     if o not in before and o.type == "MESH"]
                            try:
                                if _bi and fresh:
                                    _rehome_parts(fresh, arm_obj, first_bone,
                                                  bone.get("name") or "")
                                _bind_rotate(fresh, rot, scale, arm_obj,
                                             bone.get("name") or "")
                            except Exception as exc:
                                _log("[SC_MODEL_IEX] bind %s: %s"
                                     % (bone.get("name"), exc))
                            result = (result[0] + n2, result[1] + v2, result[2] + t2)
                        # своя анимация детали — отдельный риг (как в старом проходе)
                        try:
                            if arm_obj is not None and _part_own_anim(context, local):
                                _setup_part_subrig(context, local, arm_obj, sk_main,
                                                   scale=scale, anim_fps=anim_fps,
                                                   quat_flip=part_anim_flip)
                        except Exception as exc:
                            _log("[SC_MODEL_IEX] subrig for %s: %s" % (name2, exc))
                        _log("[SC_MODEL_IEX] index part: %s (%s, bones %d)"
                             % (name2, cat, len(bones)))
        except Exception as exc:
            _log("[SC_MODEL_IEX] index parts not collected: %s" % exc)
        if _seen is None:
            # the part files may have stayed in other paks — we report in which ones
            _report_missing_siblings(context, folder.lower(), stem,
                                     [os.path.basename(p) for p in seen])

    # Binding to bones.
    #  * meshes of formats 18/21/28 carry a bone index per vertex — this is the real
    #    game skinning (InfluenceBones 1), the vertices are already in the model system;
    #  * the other base parts (`_cap`, `_eng`, `_boost`) are bound entirely
    #    to one bone by the suffix name — also with a vertex group and an Armature
    #    modifier, so that the rotation goes around the bone HEAD and not around the tail
    #    (BONE parenting gave a shift and the nacelles stopped being coaxial with the hull).
    # Shifting vertices to the bone position (`part_offsets`) is OFF by default:
    # the bounds of the meshes of each base match its `.mdl-hdr`, i.e. the vertices are already
    # in the model system, and the shift only moves the parts away.
    if _seen is None and arm_obj is not None and sk_main:
        # The part shift is BEFORE the binding and independent of it: the vertices of the parts lie
        # in the system of their base, and the ABSOLUTE bone position puts them in place.
        if part_offsets:
            _shift_parts(sk_main, os.path.basename(base), enabled=True,
                         offsets=_load_part_offsets(context, base),
                         folder=os.path.dirname(base))
        if use_bones:
            # If the PART itself is imported (reactor, engine, nozzles), the bone
            # layout is computed from the geometry — as for a detail inside a ship.
            # Otherwise the "+1" shift applies: for the reactor 454 hull vertices fell
            # onto the rotating cap_rot_anim_jnt1, and root_cap was not created.
            bmap = None
            if _is_part_name(os.path.basename(base)):
                own = [o for o in bpy.context.scene.objects
                       if o.type == "MESH" and
                       o.name.split(".mdl-msh")[0].lower() == os.path.basename(base).lower()]
                bmap = _part_bone_map(own, sk_main)
            _skin_bind(context, arm_obj, sk_main, scale, bone_map=bmap,
                       only_stem=os.path.basename(base))
            _bind_rigid(context, arm_obj, os.path.basename(base), sk_main,
                        folder=os.path.dirname(base))
    # Pose and animation — AFTER the binding, both are applied to the BONES.
    # If both are set, the animation wins (it is longer and goes into the same
    # Action), so in that case we do not apply the pose — otherwise it would be silently
    # overwritten by the next action.
    if _seen is None and arm_obj is not None and pose_name and anim_name:
        _log("[SC_MODEL_IEX] both a pose and an animation are set — the animation is applied")
    if _seen is None and arm_obj is not None and pose_name and not anim_name:
        pose_path = _pose_file_for(base, pose_name)
        if pose_path:
            psp, pspd, _src = _anim_fps_from_model(base, pose_path, anim_fps)
            _log("[SC_MODEL_IEX] pose on bones: %s" % os.path.basename(pose_path))
            _apply_anim(context, arm_obj, pose_path, scale=scale, fps=psp,
                        speed=pspd, action_name=_L("pose_") + os.path.basename(pose_path))
        else:
            _log("[SC_MODEL_IEX] pose %r not found next to the model" % pose_name)
    # --- animations: knowing the name is not required -------------------------------
    if _seen is None and arm_obj is not None:
        avail = _all_anim_files(base)
        if avail:
            _log("[SC_MODEL_IEX] animations next to the model: %d: %s"
                 % (len(avail), ", ".join(_anim_names(avail, base))))
        else:
            _log("[SC_MODEL_IEX] no animations next to the model (looked for %s_*.anim)"
                 % os.path.basename(base))
        want = (anim_name or "").strip()
        if want in ("*", "all", "all"):
            _load_all_anims(context, arm_obj, base, scale=scale, user_fps=anim_fps)
        else:
            # no name given — we do not import the animation
            if want:
                anim_path = _anim_file_for(base, want)
                if anim_path:
                    fps_eff, sp, fsrc = _anim_fps_from_model(base, anim_path, anim_fps)
                    _log("[SC_MODEL_IEX] animation: %s | frames/s %.2f (from %s), speed %.3f"
                         % (os.path.basename(anim_path), fps_eff, fsrc, sp))
                    _apply_anim(context, arm_obj, anim_path, scale=scale, fps=fps_eff,
                                speed=sp, quat_flip=part_anim_flip)
                else:
                    _log("[SC_MODEL_IEX] animation %r not found; available: %s"
                         % (want, ", ".join(_anim_names(avail, base)) or "- none -"))
    if _seen is None:
        # leave a live active object: otherwise the tab disappears from Properties
        # Object Data Properties (context.object = None after cleaning old ones)
        _bind_static_geometry(arm_obj, os.path.basename(base), sk_main)
        _cleanup_subrig_groups()
        _report_bind_state(os.path.basename(base))
        # when importing ALL animations we leave the armature active: the Action Editor
        # works with the active object, otherwise switching actions does not work
        all_anims = str(anim_name or "").strip() in ("*", "all", "all")
        _restore_view_state(objs, arm_obj, prefer_armature=all_anims)
        global LAST_IMPORT_OBJECTS, LAST_IMPORT_ARM, LAST_PREFER_ARM
        LAST_IMPORT_OBJECTS = [o for o in objs
                               if o is not None and o.name in bpy.data.objects]
        LAST_IMPORT_ARM = arm_obj
        global LAST_MODEL_BASE, LAST_MODEL_DIR
        LAST_MODEL_BASE = os.path.basename(base)
        LAST_MODEL_DIR = os.path.dirname(base)
        LAST_PREFER_ARM = all_anims
        if all_anims and arm_obj is not None:
            _log("[SC_MODEL_IEX] animations live on armature %s — they must be switched in the Action Editor with that armature active" % arm_obj.name)
    if str(axes) == "auto":
        try:
            if not _auto_forward(locals().get("arm_obj")):
                _auto_orient(locals().get("objs"), locals().get("arm_obj"))
        except Exception as exc:
            _log("[SC_MODEL_IEX] auto axes failed: %s" % exc)
    return result


def _on_all_anims(self, context):
    "The \"Import all animations\" checkbox: clear the animation name field."
    try:
        if self.import_anims and self.anim_name:
            self.anim_name = ""
    except Exception:
        pass


# --------------------------------------------------------------------------
# operators
# --------------------------------------------------------------------------

class SC_MODEL_IEX_OT_import_model(bpy.types.Operator, ImportHelper):
    "Star Conflict model import (.mdl-hdr or .mdl-msh###)"
    bl_idname = "sc_model_iex.import_model"
    bl_label = _L("Star Conflict model")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mdl-hdr"
    filter_glob = StringProperty(
        default="*.mdl-hdr;*.mdl-msh*;*.mdf;*.model;*.pak", options={"HIDDEN"})
    batch_folder: bpy.props.BoolProperty(
        name=_L("Whole folder"), default=False,
        description=_L("Import all .mdl-hdr from the selected folder"))
    inner: bpy.props.StringProperty(
        name=_L("Path inside the .pak"), default="",
        description=_L("For .pak: e.g. models\\ships\\race_1\\s\\r1_s_t1"))
    axes: EnumProperty(
        name=_L("Orientation"),
        items=(("auto", _L("Auto (rotate if the long axis is not Y)"), ""),
               ("yup", _L("Y-up -> Z-up (as in the game, correct)"), ""),
               ("raw", _L("No conversion (as in the file)"), "")),
        default="yup")
    lod: bpy.props.IntProperty(
        name=_L("LOD"), default=0, min=-1, max=9,
        description=_L("-1 — import all levels, 0 — the most detailed one"))
    move_parts: bpy.props.BoolProperty(
        name=_L("Move details to bones"), default=False,
        description=_L("Place details at the attachment bones; turn off to keep them where the geometry is"))
    flip_v: BoolProperty(name=_L("Flip V (as in the game)"), default=True)
    dye_name: StringProperty(
        name=_L("Dye"), default="",
        description=_L("Name from ship_dyes.lua, e.g. Default_Bronze or Matt_Red01"))
    dye_layer: bpy.props.IntProperty(
        name=_L("Paint layer"), default=0, min=0, max=2,
        description=_L("The layer comes from the _s1 mask channel: 0=R, 1=G, 2=B"))
    paint_mode: EnumProperty(
        name=_L("Paint"),
        items=(("dye", _L("Dye by the layer mask"), _L("Color from ship_dyes.lua by the _s1 mask")),
               ("skin", _L("Default skin"), _L("Multiply _d by _s1 without choosing a dye")),
               ("none", _L("No paint"), _L("Only the original _d albedo"))),
        default="dye")
    decal_name: StringProperty(
        name=_L("Decal"), default="",
        description=_L("Name from ship_decals.lua, e.g. Billiard_01 or Token_01"))
    parts_mode: EnumProperty(
        name=_L("Details"),
        items=(
            ("full", _L("All parts + binding to bones"),
             _L("Import parts with the same prefix (_eng, _cap, _boost) and bind them rigidly to bones through a vertex group (like InfluenceBones 1)")),
            ("static", _L("All parts without binding"),
             _L("Import parts but do not build the armature and binding — geometry only")),
            ("only", _L("This model only (no parts)"),
             _L("Import only the selected base: handy when a single detail is needed, e.g. capacitor or a separate module")),
        ),
        default="full")
    foreign_parts: EnumProperty(
        name=_L("Parts from other paks"),
        items=(
            ("load", _L("Load them"), _L("Ship parts living in other paks are loaded automatically (Phoenix: boosters, capacitor)")),
            ("skip", _L("Do not load them"), _L("Import only the files of the selected pak — handy to grab a single detail")),
        ),
        default="load")
    brightness: bpy.props.FloatProperty(
        name=_L("Brightness"), default=1.8, min=0.1, max=6.0,
        description=_L("Multiplier for the base color: game textures are designed for game lighting and dyes, in Blender they look dark"))
    import_skeleton: BoolProperty(
        name=_L("Import skeleton"), default=True,
        description=_L("Create an armature from .mdl-skl: 34 bones with hierarchy"))
    pose_name: StringProperty(
        name=_L("Pose"), default="",
        description=_L("Name of a static pose next to the model: x_d, x_up, z_l, z_r. Applied to the BONES like an animation (the whole ship, including the parts). If an animation is also set, the animation wins"))
    part_offsets: BoolProperty(
        name=_L("Offset parts to bones"), default=True,
        description=_L("Move the vertices of the parts to the ABSOLUTE position of their bone (sum over the parent chain). Without this the parts sit lower than in the game"))
    anim_name: StringProperty(
        name=_L("Animation"), default="",
        description=_L("Animation name: idle, landing, takeoff… Matched by substring. Empty — animations are not imported"))
    import_geo: BoolProperty(
        name=_L("Model"), default=True,
        description=_L("Import geometry, skeleton and materials"))
    import_anims: BoolProperty(
        name=_L("Animations"), default=False,
        description=_L("Import all .anim of this model"))
    anim_fps: bpy.props.FloatProperty(
        name=_L("Frames/s"), default=0.0, min=0.0, max=240.0,
        description=_L("0 — take it from the model (24 fps), taking the speed from .model into account"))
    scale: bpy.props.FloatProperty(name=_L("Scale"), default=1.0, min=0.0001, max=1000.0)
    materials: BoolProperty(name=_L("Load materials and textures"), default=True)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        box = layout.box()
        box.label(text=_L("Animation"))
        box.prop(self, "import_geo")
        box.prop(self, "import_anims")
        col = box.column()
        col.enabled = not self.import_anims
        col.prop(self, "anim_name")
        col.prop(self, "anim_fps")

        box = layout.box()
        box.label(text=_L("Model"))
        box.prop(self, "scale")
        box.prop(self, "materials")
        box.prop(self, "axes")
        box.prop(self, "lod")
        box.prop(self, "flip_v")

        box = layout.box()
        box.label(text=_L("Parts and skeleton"))
        box.prop(self, "parts_mode")
        box.prop(self, "import_skeleton")
        box.prop(self, "part_offsets")
        box.prop(self, "pose_name")

        box = layout.box()
        box.label(text=_L("Paint job"))
        box.prop(self, "dye_name")
        box.prop(self, "dye_layer")
        box.prop(self, "paint_mode")
        box.prop(self, "decal_name")
        box.prop(self, "brightness")
        layout.operator(SC_MODEL_IEX_OT_show_faq.bl_idname, icon="QUESTION")

    def execute(self, context):
        global PART_PLACE_DETAILS
        PART_PLACE_DETAILS = bool(getattr(self, "move_parts", True))
        _fp = bpy.path.abspath(self.filepath)
        if _fp.lower().endswith(".pak"):
            return bpy.ops.sc_model_iex.import_pak_model(
                filepath=_fp, inner=self.inner, lod=self.lod, axes=self.axes,
                flip_v=self.flip_v, dye_name=self.dye_name,
                dye_layer=self.dye_layer, paint_mode=self.paint_mode,
                decal_name=self.decal_name, parts_mode=self.parts_mode,
                brightness=self.brightness,
                import_skeleton=self.import_skeleton,
                pose_name=self.pose_name, part_offsets=self.part_offsets,
                import_anims=self.import_anims,
                anim_name=self.anim_name, anim_fps=self.anim_fps,
                scale=self.scale, materials=self.materials)
        if self.batch_folder:
            folder = os.path.dirname(_fp)
            try:
                files = sorted(f for f in os.listdir(folder)
                               if f.lower().endswith(".mdl-hdr"))
            except OSError as exc:
                self.report({"ERROR"}, str(exc))
                return {"CANCELLED"}
            if not files:
                self.report({"ERROR"}, "no .mdl-hdr in the folder")
                return {"CANCELLED"}
            done = fail = 0
            for i, f in enumerate(files, 1):
                try:
                    bpy.ops.sc_model_iex.import_model(
                        filepath=os.path.join(folder, f), lod=self.lod,
                        axes=self.axes, flip_v=self.flip_v)
                    done += 1
                except Exception as exc:
                    fail += 1
                    print("[SC_MODEL_IEX] batch: %s — %s" % (f, exc))
            self.report({"INFO"}, "imported %d of %d, errors %d"
                        % (done, len(files), fail))
            return {"FINISHED"}
        try:
            M, MAT, TEX = _ensure_sc_tools(context)
            from sc_tools import model as _M
            try:
                _total = len(_M.mesh_files(_base_of(self.filepath)))
            except Exception:
                _total = 1
            _prog = _Progress(context, _total)
            try:
                n, v, t = _import_base(context, M, MAT, TEX, self.filepath,
    self.scale, self.materials,
    axes=self.axes, lod=self.lod,
    flip_v=self.flip_v,
    dye_name=self.dye_name, dye_layer=self.dye_layer,
    paint_mode=self.paint_mode,
    decal_name=self.decal_name,
    collect_parts=(self.parts_mode != "only"),
    use_bones=(self.parts_mode != "static"),
    brightness=self.brightness,
    import_skeleton=self.import_skeleton,
    pose_name=self.pose_name,
    part_offsets=self.part_offsets,
    anim_name=("*" if self.import_anims else self.anim_name),
    anim_fps=self.anim_fps,
    on_mesh=lambda _i, _n: _prog.step())
            finally:
                _prog.end()
            _restore_view_state(LAST_IMPORT_OBJECTS, LAST_IMPORT_ARM, LAST_PREFER_ARM)
            self.report({"INFO"}, "models %d, vertices %d, polygons %d%s" % (n, v, t, LAST_BIND_NOTE))
        except Exception as exc:
            import traceback
            _log("""[SC_MODEL_IEX] IMPORT ERROR:
%s""" % traceback.format_exc())
            _restore_view_state()
            self.report({"ERROR"}, "%s (details in the SC_MODEL_IEX_log text)" % exc)
            return {"CANCELLED"}
        return {"FINISHED"}


def _normalize_inner(inner):
    """Model path/name inside the pak -> the base without extension.

    Accepts both "models\\ships\\...\\name" and "...\\name.mdl-hdr",
    and "...\\name.mdl-msh000" — the extension is dropped.
    """
    s = (inner or "").strip().replace("/", os.sep)
    if not s:
        return ""
    low = s.lower()
    m = re.search(r"\.mdl-msh\d*$", low)
    if m:
        return s[:m.start()]
    for tail in (".mdl-hdr", ".mdl-skl", ".mdl-zon", ".model", ".mdf", ".mdp"):
        if low.endswith(tail):
            return s[:-len(tail)]
    return s


class SC_MODEL_IEX_OT_import_pak(bpy.types.Operator, ImportHelper):
    "Star Conflict model import from .pak (unpacked into a temporary folder)"
    bl_idname = "sc_model_iex.import_pak_model"
    bl_label = _L("Star Conflict model from .pak")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".pak"
    filter_glob = StringProperty(default="*.pak", options={"HIDDEN"})
    inner: StringProperty(name=_L("Path inside the pak"), default="",
                          description=_L("For example models\\ships\\race_1\\s\\r1_s_t1"))
    axes: EnumProperty(
        name=_L("Orientation"),
        items=(("auto", _L("Auto (rotate if the long axis is not Y)"), ""),
               ("yup", _L("Y-up -> Z-up (as in the game, correct)"), ""),
               ("raw", _L("No conversion (as in the file)"), "")),
        default="yup")
    lod: bpy.props.IntProperty(
        name=_L("LOD"), default=0, min=-1, max=9,
        description=_L("-1 — import all levels, 0 — the most detailed one"))
    move_parts: bpy.props.BoolProperty(
        name=_L("Move details to bones"), default=False,
        description=_L("Place details at the attachment bones; turn off to keep them where the geometry is"))
    flip_v: BoolProperty(name=_L("Flip V (as in the game)"), default=True)
    dye_name: StringProperty(
        name=_L("Dye"), default="",
        description=_L("Name from ship_dyes.lua, e.g. Default_Bronze or Matt_Red01"))
    dye_layer: bpy.props.IntProperty(
        name=_L("Paint layer"), default=0, min=0, max=2,
        description=_L("The layer comes from the _s1 mask channel: 0=R, 1=G, 2=B"))
    paint_mode: EnumProperty(
        name=_L("Paint"),
        items=(("dye", _L("Dye by the layer mask"), _L("Color from ship_dyes.lua by the _s1 mask")),
               ("skin", _L("Default skin"), _L("Multiply _d by _s1 without choosing a dye")),
               ("none", _L("No paint"), _L("Only the original _d albedo"))),
        default="dye")
    decal_name: StringProperty(
        name=_L("Decal"), default="",
        description=_L("Name from ship_decals.lua, e.g. Billiard_01 or Token_01"))
    parts_mode: EnumProperty(
        name=_L("Details"),
        items=(
            ("full", _L("All parts + binding to bones"),
             _L("Import parts with the same prefix (_eng, _cap, _boost) and bind them rigidly to bones through a vertex group (like InfluenceBones 1)")),
            ("static", _L("All parts without binding"),
             _L("Import parts but do not build the armature and binding — geometry only")),
            ("only", _L("This model only (no parts)"),
             _L("Import only the selected base: handy when a single detail is needed, e.g. capacitor or a separate module")),
        ),
        default="full")
    foreign_parts: EnumProperty(
        name=_L("Parts from other paks"),
        items=(
            ("load", _L("Load them"), _L("Ship parts living in other paks are loaded automatically (Phoenix: boosters, capacitor)")),
            ("skip", _L("Do not load them"), _L("Import only the files of the selected pak — handy to grab a single detail")),
        ),
        default="load")
    brightness: bpy.props.FloatProperty(
        name=_L("Brightness"), default=1.8, min=0.1, max=6.0,
        description=_L("Multiplier for the base color: game textures are designed for game lighting and dyes, in Blender they look dark"))
    import_skeleton: BoolProperty(
        name=_L("Import skeleton"), default=True,
        description=_L("Create an armature from .mdl-skl: 34 bones with hierarchy"))
    pose_name: StringProperty(
        name=_L("Pose"), default="",
        description=_L("Name of a static pose next to the model: x_d, x_up, z_l, z_r. Applied to the BONES like an animation (the whole ship, including the parts). If an animation is also set, the animation wins"))
    part_offsets: BoolProperty(
        name=_L("Offset parts to bones"), default=True,
        description=_L("Move the vertices of the parts to the ABSOLUTE position of their bone (sum over the parent chain). Without this the parts sit lower than in the game"))
    import_anims: bpy.props.BoolProperty(name=_L("Animation import"), default=False)
    anim_name: StringProperty(
        name=_L("Animation"), default="",
        description=_L("Animation name: idle, landing, takeoff… You do not need the exact one — the plug-in finds it by substring. EMPTY = choose automatically (idle/aim/landing/takeoff, otherwise the longest file). The list of found files is written to the report and to SC_MODEL_IEX_log"))
    anim_fps: bpy.props.FloatProperty(
        name=_L("Frames/s"), default=0.0, min=0.0, max=240.0,
        description=_L("0 — take it from the model (24 fps), taking the speed from .model into account"))
    scale: bpy.props.FloatProperty(name=_L("Scale"), default=1.0, min=0.0001, max=1000.0)
    materials: BoolProperty(name=_L("Load materials and textures"), default=True)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        box = layout.box()
        box.label(text=_L("Pak"))
        box.prop(self, "inner")

        box = layout.box()
        box.label(text=_L("Animation"))
        box.prop(self, "import_anims")
        col = box.column()
        col.enabled = not self.import_anims
        col.prop(self, "anim_name")
        col.prop(self, "anim_fps")

        box = layout.box()
        box.label(text=_L("Model"))
        box.prop(self, "scale")
        box.prop(self, "materials")
        box.prop(self, "axes")
        box.prop(self, "lod")
        box.prop(self, "flip_v")

        box = layout.box()
        box.label(text=_L("Parts and skeleton"))
        box.prop(self, "parts_mode")
        box.prop(self, "foreign_parts")
        box.prop(self, "import_skeleton")
        box.prop(self, "part_offsets")
        box.prop(self, "pose_name")

        box = layout.box()
        box.label(text=_L("Paint job"))
        box.prop(self, "dye_name")
        box.prop(self, "dye_layer")
        box.prop(self, "paint_mode")
        box.prop(self, "decal_name")
        box.prop(self, "brightness")
        layout.operator(SC_MODEL_IEX_OT_show_faq.bl_idname, icon="QUESTION")

    def execute(self, context):
        global PART_PLACE_DETAILS
        PART_PLACE_DETAILS = bool(getattr(self, "move_parts", True))
        try:
            M, MAT, TEX = _ensure_sc_tools(context)
            from sc_tools import pak as P
            # a cache per pak separately: a repeated import does not unpack again
            pak_name = os.path.splitext(os.path.basename(self.filepath))[0]
            work = os.path.join(_texture_cache_dir(context), "pak_" + pak_name)
            marker = os.path.join(work, ".unpacked")
            # Маркер распаковки может остаться от прошлого раза, а кэш — быть
            # очищенным. Тогда моделей не находится; проверяем содержимое.
            need_unpack = not os.path.exists(marker)
            if not need_unpack and not _walk_mdl_hdr(work):
                _log("[SC_MODEL_IEX] кэш пака пуст (%s) — распаковываю заново" % work)
                try:
                    os.remove(marker)
                except Exception:
                    pass
                need_unpack = True
            if need_unpack:
                os.makedirs(work, exist_ok=True)
                _log("[SC_MODEL_IEX] unpacking pak %s -> %s" % (pak_name, work))
                P.unpack_file(self.filepath, work)
                open(marker, "w").write("ok")
            else:
                _log("[SC_MODEL_IEX] pak already unpacked: %s" % work)
            inner = _normalize_inner(self.inner)
            base = os.path.join(work, inner) if inner else None
            if inner and not os.path.exists(base + ".mdl-hdr"):
                # a path is given, but there is no such model in the pak — we do not silently substitute
                raise RuntimeError(
                    "no model at path \"%s\" in the pak (expected the file %s.mdl-hdr)"
                    % (self.inner, base))
            if not base or not any(os.path.exists(base + ext)
                                   for ext in (".mdl-hdr", ".mdl-msh000")):
                # We choose the model BY THE CONTENT OF THIS PAK, not by scanning the cache:
                # parts from other paks are now loaded into the cache (misc.pak and
                # others), and "the first model in the folder" turned out to be a map object
                # (s1340_01) instead of the ship.
                base = _model_base_from_pak(P, self.filepath, work)
            if not base:
                models = M.find_models(work)
                if not models:
                    # понятная диагностика: что вообще лежит в паке
                    kinds = {}
                    for _dp, _dirs, _files in os.walk(work):
                        for _f in _files:
                            _e = os.path.splitext(_f)[1].lower() or "?"
                            kinds[_e] = kinds.get(_e, 0) + 1
                    total = sum(kinds.values())
                    top = ", ".join("%s: %d" % (k, v) for k, v in
                                    sorted(kinds.items(), key=lambda kv: -kv[1])[:5])
                    raise RuntimeError(
                        "в паке нет моделей (.mdl-hdr): файлов %d, из них %s. "
                        "Похоже, это пак текстур или частей, а не пак с моделью "
                        "корабля — выберите другой пак."
                        % (total, top or "нет файлов"))
                base = models[0]
                _log("[SC_MODEL_IEX] model not specified, took the first one: %s" % base)
            if self.parts_mode != "only" and self.foreign_parts == "load":
                # ship parts often live in OTHER paks and are named differently
                # (r4_l_phoenix_boost, race4_l_phoenix_capacitor) — we load them additionally
                _unpack_sibling_paks(context, work, base, P)
            n, v, t = _import_base(context, M, MAT, TEX, base, self.scale,
                                   self.materials, axes=self.axes, lod=self.lod,
                                   flip_v=self.flip_v,
                                   dye_name=self.dye_name, dye_layer=self.dye_layer,
                                   paint_mode=self.paint_mode,
                                   decal_name=self.decal_name,
                                   collect_parts=(self.parts_mode != "only"),
                                   use_bones=(self.parts_mode != "static"),
                                   brightness=self.brightness,
                                   import_skeleton=self.import_skeleton,
                                   pose_name=self.pose_name,
                                   part_offsets=self.part_offsets,
                                   anim_name=("*" if self.import_anims else self.anim_name),
                                   anim_fps=self.anim_fps,
                                   )
            _restore_view_state(LAST_IMPORT_OBJECTS, LAST_IMPORT_ARM, LAST_PREFER_ARM)
            self.report({"INFO"}, "models %d, vertices %d, polygons %d%s" % (n, v, t, LAST_BIND_NOTE))
        except Exception as exc:
            import traceback
            _log("""[SC_MODEL_IEX] IMPORT ERROR:
%s""" % traceback.format_exc())
            _restore_view_state()
            self.report({"ERROR"}, "%s (details in the SC_MODEL_IEX_log text)" % exc)
            return {"CANCELLED"}
        return {"FINISHED"}



class SC_MODEL_IEX_OT_import_batch(bpy.types.Operator):
    "Import all models from a folder: progress and cancel with Esc"
    bl_idname = "sc_model_iex.import_batch"
    bl_label = _L("SC: import models from folder")
    bl_options = {"REGISTER", "UNDO"}

    directory: bpy.props.StringProperty(name=_L("Folder"), subtype="DIR_PATH", default="")
    lod: bpy.props.IntProperty(name=_L("LOD"), default=0, min=-1, max=9)
    import_anims: bpy.props.BoolProperty(name=_L("Animations"), default=False)
    parts_mode: bpy.props.EnumProperty(
        name=_L("Details"), default="only",
        items=(("only", _L("The model itself only"), _L("Each base separately: the details in the folder are separate too")),
               ("full", _L("With details"), _L("As in a normal import (details are pulled into the main one)"))))

    def _files(self):
        folder = bpy.path.abspath(self.directory)
        if not folder or not os.path.isdir(folder):
            return []
        return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                      if f.lower().endswith(".mdl-hdr"))

    def execute(self, context):
        self._list = self._files()
        if not self._list:
            self.report({"ERROR"}, "no .mdl-hdr in the folder")
            return {"CANCELLED"}
        self._i = 0
        self._done = 0
        self._fail = 0
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        try:
            context.window_manager.progress_begin(0, len(self._list))
            context.window_manager.modal_handler_add(self)
        except Exception:
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        self.report({"INFO"}, "importing %d models..." % len(self._list))
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self._finish(context)
            self.report({"WARNING"}, "cancelled: imported %d of %d, errors %d"
                        % (self._done, len(self._list), self._fail))
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        if self._i >= len(self._list):
            self._finish(context)
            self.report({"INFO"}, "imported %d of %d, errors %d"
                        % (self._done, len(self._list), self._fail))
            return {"FINISHED"}
        path = self._list[self._i]
        self._i += 1
        try:
            bpy.ops.sc_model_iex.import_model(
                filepath=path, lod=self.lod,
                import_anims=self.import_anims,
                parts_mode=self.parts_mode)
            self._done += 1
        except Exception as exc:
            self._fail += 1
            print("[SC_MODEL_IEX] batch: %s — %s" % (os.path.basename(path), exc))
        try:
            context.window_manager.progress_update(self._i)
        except Exception:
            pass
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        try:
            context.window_manager.progress_end()
        except Exception:
            pass



class SC_MODEL_IEX_OT_export_batch(bpy.types.Operator):
    "Export all scene models by base: progress and cancel with Esc"
    bl_idname = "sc_model_iex.export_batch"
    bl_label = _L("SC: export models from scene")
    bl_options = {"REGISTER", "UNDO"}

    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    template: bpy.props.StringProperty(name=_L(".mdl-msh template (optional)"),
                                       subtype="FILE_PATH", default="")
    hdr_template: bpy.props.StringProperty(name=_L(".mdl-hdr template (optional)"),
                                           subtype="FILE_PATH", default="")
    gen_lod: bpy.props.BoolProperty(name=_L("Generate LOD"), default=False)
    lod_levels: bpy.props.IntProperty(name=_L("LOD levels"), default=2, min=1, max=4)
    with_model: bpy.props.BoolProperty(name=_L("Write .model/.skins"), default=True)
    with_mdp: bpy.props.BoolProperty(name=_L("Write .mdp"), default=True)
    with_skl: bpy.props.BoolProperty(name=_L("Write .mdl-skl"), default=False)
    with_anim: bpy.props.BoolProperty(name=_L("Write .anim"), default=False)
    only_selected: bpy.props.BoolProperty(name=_L("Selected only"), default=False)

    def _groups(self, context):
        objs = ([o for o in context.selected_objects if o.type == "MESH"]
                if self.only_selected else
                [o for o in context.scene.objects if o.type == "MESH"])
        out = {}
        for o in objs:
            base = o.get("sc_src_base") or ""
            if not base:
                src = o.get("sc_src_msh") or ""
                if ".mdl-msh" in src:
                    # the detail base — by the mesh file name
                    base = os.path.basename(src).split(".mdl-msh")[0]
                else:
                    base = o.name.split(".")[0]
            out.setdefault(base, []).append(o)
        return sorted(out.items())

    def execute(self, context):
        self._out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
        if not self._out:
            self.report({"ERROR"}, "export folder not selected")
            return {"CANCELLED"}
        self._list = self._groups(context)
        if not self._list:
            self.report({"ERROR"}, "no meshes in the scene")
            return {"CANCELLED"}
        self._i = 0
        self._done = 0
        self._fail = 0
        self._timer = context.window_manager.event_timer_add(0.05, window=context.window)
        try:
            context.window_manager.progress_begin(0, len(self._list))
            context.window_manager.modal_handler_add(self)
        except Exception:
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        self.report({"INFO"}, "exporting %d bases..." % len(self._list))
        return {"RUNNING_MODAL"}

    def _export_one(self, context, name, objs):
        arms = []
        for o in objs:
            for m in o.modifiers:
                if m.type == "ARMATURE" and m.object is not None and m.object not in arms:
                    arms.append(m.object)
        bpy.ops.object.select_all(action="DESELECT")
        for o in objs:
            o.select_set(True)
        context.view_layer.objects.active = objs[0]
        bpy.ops.sc_model_iex.export_mesh_new(
            base=name, out_dir=self._out,
            template=self.template, hdr_template=self.hdr_template,
            gen_lod=self.gen_lod,
            lod_levels=(self.lod_levels if self.gen_lod else 0))
        if self.with_model:
            bpy.ops.sc_model_iex.export_model_file(base=name, out_dir=self._out)
        if self.with_mdp:
            bpy.ops.sc_model_iex.export_mdp(base=name, out_dir=self._out)
        if arms and (self.with_skl or self.with_anim):
            bpy.ops.object.select_all(action="DESELECT")
            arm = arms[0]
            arm.select_set(True)
            context.view_layer.objects.active = arm
            if self.with_skl:
                bpy.ops.sc_model_iex.export_skeleton(base=name, out_dir=self._out)
            if self.with_anim:
                bpy.ops.sc_model_iex.export_animation(base=name, out_dir=self._out)

    def modal(self, context, event):
        if event.type == "ESC":
            self._finish(context)
            self.report({"WARNING"}, "cancelled: exported %d of %d, errors %d"
                        % (self._done, len(self._list), self._fail))
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        if self._i >= len(self._list):
            self._finish(context)
            self.report({"INFO"}, "exported %d of %d, errors %d"
                        % (self._done, len(self._list), self._fail))
            return {"FINISHED"}
        name, objs = self._list[self._i]
        self._i += 1
        try:
            self._export_one(context, name, objs)
            self._done += 1
        except Exception as exc:
            self._fail += 1
            print("[SC_MODEL_IEX] batch export: %s — %s" % (name, exc))
        try:
            context.window_manager.progress_update(self._i)
        except Exception:
            pass
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        try:
            context.window_manager.progress_end()
        except Exception:
            pass



class SC_MODEL_IEX_OT_import_mdp(bpy.types.Operator, ImportHelper):
    ".mdp import: collision as a convex hull (or an AABB box)"
    bl_idname = "sc_model_iex.import_mdp"
    bl_label = _L("SC: collision (.mdp)")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mdp"
    filter_glob = StringProperty(default="*.mdp", options={"HIDDEN"})
    scale: bpy.props.FloatProperty(name=_L("Scale"), default=1.0, min=0.0001, max=1000.0)
    per_body: bpy.props.BoolProperty(name=_L("A separate object per body"), default=True)
    base: bpy.props.StringProperty(name=_L("Object name"), default="")

    def _hull(self, body, scale):
        "Type 6 is a shell, type 7 is a ready trimesh, otherwise a cube by AABB."
        import bmesh
        mn, mx = body["min"], body["max"]
        if body.get("typ") == 7 and body.get("tris_idx"):
            verts = body.get("verts") or []
            faces = body["tris_idx"]
            me = bpy.data.meshes.new((self.base or "mdp") + "_mesh")
            me.from_pydata([(-v[0] * scale, -v[2] * scale, v[1] * scale) for v in verts],
                           [], [tuple(f) for f in faces])
            me.update()
            return me
        src = body.get("tris") or []
        pts = [(v[0], v[1], v[2]) for v in src]
        box = False
        if len(pts) < 4:
            x0, y0, z0 = mn
            x1, y1, z1 = mx
            pts = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
            box = True
        me = bpy.data.meshes.new((self.base or "mdp") + "_mesh")
        bm = bmesh.new()
        for p in pts:
            bm.verts.new((-p[0] * scale, -p[2] * scale, p[1] * scale))
        bmesh.ops.convex_hull(bm, input=bm.verts, use_existing_faces=False)
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context=_L("VERTS"))
        bm.to_mesh(me)
        bm.free()
        me["sc_mdp_box"] = box
        return me

    def execute(self, context):
        from sc_tools import mdp_write as DP
        path = bpy.path.abspath(self.filepath)
        if not path or not os.path.exists(path):
            self.report({"ERROR"}, ".mdp not found")
            return {"CANCELLED"}
        try:
            data = open(path, "rb").read()
            parsed = DP.parse_mdp(data)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        made = 0
        name0 = self.base or os.path.basename(path)[:-4]
        for i, body in enumerate(parsed.get("bodies") or []):
            try:
                me = self._hull(body, self.scale)
                me.name = name0 if (i == 0 and not self.per_body) else "%s_%d" % (name0, i)
                ob = bpy.data.objects.new(me.name, me)
                context.collection.objects.link(ob)
                ob["sc_mdp_body"] = i
                ob["sc_mdp_planes"] = len(body.get("tris") or [])
                made += 1
                if not self.per_body:
                    break
            except Exception as exc:
                self.report({"WARNING"}, "body %d: %s" % (i, exc))
        if not made:
            self.report({"ERROR"}, "failed to create meshes")
            return {"CANCELLED"}
        self.report({"INFO"}, "collision: %d bodies, %d objects" % (len(parsed.get("bodies") or []), made))
        return {"FINISHED"}



class SC_MODEL_IEX_OT_mdp_scale(bpy.types.Operator, ImportHelper):
    "Scale the .mdp collision (in-place edit, names are preserved)"
    bl_idname = "sc_model_iex.mdp_scale"
    bl_label = _L("SC: collision scale (.mdp)")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mdp"
    filter_glob = StringProperty(default="*.mdp", options={"HIDDEN"})
    scale: bpy.props.FloatProperty(name=_L("Multiplier"), default=10.0, min=0.01, max=1000.0)
    out_dir: bpy.props.StringProperty(name=_L("Destination (empty — next to it)"), subtype="DIR_PATH", default="")

    def execute(self, context):
        import struct
        from sc_tools import mdp_write as DP
        path = bpy.path.abspath(self.filepath)
        if not path or not os.path.exists(path):
            self.report({"ERROR"}, ".mdp not found")
            return {"CANCELLED"}
        try:
            with open(path, "rb") as fh:
                data = bytearray(fh.read())
            m = DP.parse_mdp(bytes(data))
            k = float(self.scale)
            base = 16 + m["doff"] + 0x10
            for b in m["bodies"]:
                if b["typ"] == 6:
                    shape = base + b["offset"]
                    mn = struct.unpack_from("<3f", data, shape + 0x10)
                    mx = struct.unpack_from("<3f", data, shape + 0x20)
                    struct.pack_into("<3f", data, shape + 0x10, *[v * k for v in mn])
                    struct.pack_into("<3f", data, shape + 0x20, *[v * k for v in mx])
                    for i in range(b["count"]):
                        off = shape + 0x50 + i * 16
                        x, y, z, w = struct.unpack_from("<4f", data, off)
                        struct.pack_into("<4f", data, off, x * k, y * k, z * k, w)
                elif b["typ"] == 2:
                    pass          # boxes — with a separate pass below
                else:
                    self.report({"WARNING"}, "type %d cannot be scaled" % b["typ"])
            if any(b["typ"] == 2 for b in m["bodies"]):
                bo = 0x34 if m["version"] == 0x0C else 0x40
                for i, b in enumerate(m["bodies"]):
                    if b["typ"] != 2:
                        continue
                    rec = bo + i * 0x4C
                    hx, hy, hz = struct.unpack_from("<3f", data, rec + 0x40)
                    struct.pack_into("<3f", data, rec + 0x40, abs(hx) * k, abs(hy) * k, abs(hz) * k)
            out = bpy.path.abspath(self.out_dir) or os.path.dirname(path)
            os.makedirs(out, exist_ok=True)
            name = os.path.splitext(os.path.basename(path))[0] + "_x%g.mdp" % k
            dst = os.path.join(out, name)
            with open(dst, "wb") as fh:
                fh.write(bytes(data))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "collision x%g -> %s" % (k, dst))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_import_anim(bpy.types.Operator, ImportHelper):
    "Apply a Star Conflict animation (.anim) to an armature"
    bl_idname = "sc_model_iex.import_anim"
    bl_label = _L("Star Conflict animation (.anim)")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".anim"
    filter_glob = StringProperty(default="*.anim", options={"HIDDEN"})
    fps: bpy.props.FloatProperty(
        name=_L("Frames/s"), default=24.0, min=1.0, max=240.0,
        description=_L(".anim key times are normalized to [0, duration] and converted to frames by this value"))
    do_rot: BoolProperty(name=_L("Rotations"), default=True,
                         description=_L("Rotation channels (types 0 and 2)"))
    do_loc: BoolProperty(name=_L("Translations"), default=True,
                         description=_L("Position channels (types 0 and 1); the position in the file is absolute, in Blender it is written as a difference from rest"))
    clear_prev: BoolProperty(name=_L("Clear the previous action"), default=True,
                             description=_L("Unlink the current Action before applying"))

    def execute(self, context):
        try:
            arm = None
            ob = context.view_layer.objects.active
            if ob is not None and ob.type == "ARMATURE":
                arm = ob
            if arm is None:
                sel = [o for o in context.selected_objects if o.type == "ARMATURE"]
                if len(sel) == 1:
                    arm = sel[0]
            if arm is None:
                arms = [o for o in context.scene.objects if o.type == "ARMATURE"]
                if len(arms) == 1:
                    arm = arms[0]
            if arm is None:
                raise RuntimeError(
                    "select an armature (skeleton object): the active object is not an armature, and there are %d armatures in the scene" % len([o for o in context.scene.objects
                                                  if o.type == "ARMATURE"]))
            if self.clear_prev and arm.animation_data is not None:
                arm.animation_data.action = None
            n = _apply_anim(context, arm, self.filepath, fps=self.fps,
                            do_rot=self.do_rot, do_loc=self.do_loc)
            if not n:
                self.report({"ERROR"}, "animation not applied")
                return {"CANCELLED"}
            self.report({"INFO"}, "bones in the animation: %d" % n)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class SC_MODEL_IEX_OT_game_lighting(bpy.types.Operator):
    "Set up lighting and color correction like in the game (dark blue-gray space)"
    bl_idname = "sc_model_iex.game_lighting"
    bl_label = _L("Lighting like in the game")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        sc = context.scene
        world = bpy.data.worlds.get("SC_GameWorld") or bpy.data.worlds.new("SC_GameWorld")
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs[0].default_value = (0.70, 0.75, 0.80, 1.0)
            bg.inputs[1].default_value = 1.0
        sc.world = world

        sun = bpy.data.lights.get("SC_Key") or bpy.data.lights.new("SC_Key", "SUN")
        sun.energy = 1.6
        sun.color = (0.82, 0.93, 1.0)
        if "SC_Key" not in bpy.data.objects:
            obj = bpy.data.objects.new("SC_Key", sun)
            context.collection.objects.link(obj)
            obj.rotation_euler = (0.55, 0.15, 0.8)
        try:
            sc.view_settings.view_transform = "AgX"
        except Exception:
            try:
                sc.view_settings.view_transform = "Filmic"
            except Exception:
                pass
        sc.view_settings.exposure = 0.6
        _log("[SC_MODEL_IEX] lighting: SC_GameWorld + SC_Key, view transform %s"
             % sc.view_settings.view_transform)
        self.report({"INFO"}, "lighting like in the game applied")
        return {"FINISHED"}



def _faq_path():
    "Path to FAQ.md next to the add-on."
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "FAQ.md")


def _open_faq_external():
    "Open FAQ.md with the system application (Windows/macOS/Linux)."
    path = _faq_path()
    if not os.path.isfile(path):
        return False
    try:
        import subprocess
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 — open the file with the associated program
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


def _faq_text():
    """FAQ text block (English + Russian), rebuilt on each call."""
    parts = [FAQ_TEXT]
    try:
        from .sc_tools import faq as _faqmod
        parts.append(_faqmod.FAQ_IMPORT_EXPORT)
        parts.append(_faqmod.FAQ_TEXT_RU)
        parts.append(_faqmod.FAQ_IMPORT_EXPORT_RU)
    except Exception as exc:
        _log("[SC_MODEL_IEX] FAQ import/export text not loaded: %s" % exc)
    full = "\n\n".join(p for p in parts if p)
    txt = bpy.data.texts.get("SC_MODEL_IEX_FAQ")
    if txt is None:
        txt = bpy.data.texts.new("SC_MODEL_IEX_FAQ")
    try:
        txt.clear()
        txt.write(full)
    except Exception:
        pass
    return txt


def _open_faq_window(context, txt):
    "Open the FAQ in a new Blender window with the text editor."
    wm = getattr(context, "window_manager", None)
    if wm is None:
        return False
    before = set(wm.windows)
    try:
        bpy.ops.wm.window_new()
    except Exception:
        return False
    new = [w for w in wm.windows if w not in before]
    if not new:
        return False
    win = new[-1]
    screen = getattr(win, "screen", None)
    areas = list(getattr(screen, "areas", [])) if screen is not None else []
    for area in areas:
        try:
            area.type = "TEXT_EDITOR"
            area.spaces.active.text = txt
            return True
        except Exception:
            continue
    return False


def _show_faq(context):
    "Open the FAQ: existing text editor, new window, split area."
    txt = _faq_text()
    screen = getattr(context, "screen", None)
    window = getattr(context, "window", None)
    if screen is None and window is not None:
        screen = getattr(window, "screen", None)
    if screen is None:
        return False
    for area in screen.areas:
        if area.type == "TEXT_EDITOR":
            area.spaces.active.text = txt
            return True
    if _open_faq_window(context, txt):
        return True
    for area in list(screen.areas):
        if area.type != "VIEW_3D":
            continue
        region = area.regions[-1] if area.regions else None
        try:
            with context.temp_override(area=area, window=window, screen=screen,
                                       region=region):
                bpy.ops.screen.area_split(direction="VERTICAL", factor=0.45)
        except Exception:
            continue
        break
    for area in screen.areas:
        if area.type == "TEXT_EDITOR":
            area.spaces.active.text = txt
            return True
    return False


class SC_MODEL_IEX_OT_show_faq(bpy.types.Operator):
    "Open the import/export FAQ"
    bl_idname = "sc_model_iex.faq"
    bl_label = _L("FAQ: import and export")
    bl_options = {"REGISTER"}

    def execute(self, context):
        if _show_faq(context):
            return {"FINISHED"}
        if _open_faq_external():
            self.report({"INFO"}, "FAQ opened in the external editor: %s" % _faq_path())
            return {"FINISHED"}
        try:
            context.window_manager.clipboard = FAQ_TEXT
            self.report({"INFO"},
                        "text editor unavailable — the FAQ was copied to the clipboard (file: %s)" % _faq_path())
            return {"FINISHED"}
        except Exception:
            self.report({"WARNING"}, "failed to open the FAQ: %s" % _faq_path())
            return {"CANCELLED"}


class SC_MODEL_IEX_OT_reload_tools(bpy.types.Operator):

    "Reload SC_MODEL_IEX modules from disk (after editing sc_tools without restarting)"
    bl_idname = "sc_model_iex.reload_tools"
    bl_label = _L("Reload SC_MODEL_IEX modules")
    bl_options = {"REGISTER"}

    def execute(self, context):
        import importlib
        names = [m for m in list(sys.modules)
                 if m == "sc_tools" or m.startswith("sc_tools.")
                 or m in ("sc_anim", "anim_pose", "sc_sound", "sc_dye_preview")]
        ok = 0
        errs = []
        for name in names:
            try:
                importlib.reload(sys.modules[name])
                ok += 1
            except Exception as exc:
                errs.append("%s: %s" % (name, exc))
        _log("[SC_MODEL_IEX] modules reloaded: %d%s"
             % (ok, ("; errors: " + "; ".join(errs)) if errs else ""))
        self.report({"INFO"}, "modules reloaded: %d" % ok)
        return {"FINISHED"}


# --------------------------------------------------------------------------
# settings and menu
# --------------------------------------------------------------------------

class SC_MODEL_IEX_Prefs(bpy.types.AddonPreferences):
    bl_idname = __name__

    export_dir: StringProperty(
        name=_L("Model export folder"),
        subtype="DIR_PATH",
        default="",
        description=_L("Where .pak files with exported models are stored. On the first export it must be chosen"))
    parts_from_index: BoolProperty(
        name=_L("Parts from the index (other paks)"),
        default=True,
        description=_L("Take ship parts from sc_index.json so that details living in other paks are found too"))
    cache_dir: StringProperty(
        name=_L("Texture cache"),
        subtype="DIR_PATH",
        default="",
        description=_L("Where to unpack .tfh/.tfd; empty — a temporary folder"))
    game_root: StringProperty(
        name=_L("Game root (data)"),
        subtype="DIR_PATH",
        default="",
        description=_L("data folder with .pak files; empty — detected automatically"))
    dyes_path: StringProperty(
        name=_L("ship_dyes.lua"),
        subtype="FILE_PATH",
        default="",
        description=_L("Paint table; empty — taken from data\\gamedata"))
    decals_path: StringProperty(
        name=_L("ship_decals.lua"),
        subtype="FILE_PATH",
        default="",
        description=_L("Decal table; empty — taken from data\\gamedata"))
    index_path: StringProperty(
        name=_L("Index (sc_index.json)"),
        subtype="FILE_PATH",
        default="",
        description=_L("OPTIONAL: which .pak holds the models and textures. Without the index, textures are searched in the unpacked model folder"))
    armor_name: StringProperty(
        name=_L("Armor pattern (ShipArmors)"),
        default="",
        description=_L("Name from ship_armors.lua (e.g. armor02). Empty — no armor. Armor gives engines and details the dark metal look like in the game"))
    unpacked_root: StringProperty(
        name=_L("Unpacked tree (optional)"),
        subtype="DIR_PATH",
        default="",
        description=_L("Optional folder with unpacked paks. Not required: textures are extracted from the game .pak files automatically"))

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "game_root")
        layout.prop(self, "export_dir")
        layout.prop(self, "cache_dir")
        layout.prop(self, "dyes_path")
        layout.prop(self, "decals_path")
        layout.prop(self, "index_path")
        layout.prop(self, "armor_name")
        layout.label(text=_L("Paths may be left empty: data and the tables are detected automatically"))
        layout.label(text=_L("Textures are unpacked into the cache and packed into the .blend"))
        layout.operator(SC_MODEL_IEX_OT_reload_tools.bl_idname, icon="FILE_REFRESH")
        layout.operator(SC_MODEL_IEX_OT_game_lighting.bl_idname, icon="LIGHT_SUN")
        layout.operator(SC_MODEL_IEX_OT_show_faq.bl_idname, icon="QUESTION")


class SC_MODEL_IEX_MT_import(bpy.types.Menu):
    bl_idname = "SC_MODEL_IEX_MT_import"
    bl_label = _L("Star Conflict")

    def draw(self, context):
        l = self.layout
        l.operator(SC_MODEL_IEX_OT_import_model.bl_idname,
                   text=_L("Model (.mdl-hdr / .pak, \"Whole folder\")"))
        l.operator(SC_MODEL_IEX_OT_import_anim.bl_idname, text=_L("Animation (.anim)"))


def menu_func_import(self, context):
    self.layout.operator(SC_MODEL_IEX_OT_import_model.bl_idname,
                         text=_L("Star Conflict…"))


def _set_action(arm, act):
    "Assign the action to the armature (respecting Blender 4.4+/5.x slots)."
    if arm is None or arm.animation_data is None:
        return False
    ad = arm.animation_data
    ad.action = act
    try:
        if act is not None and getattr(act, "slots", None):
            for slot in act.slots:
                try:
                    ad.action_slot = slot
                    break
                except Exception:
                    continue
    except Exception:
        pass
    # scene frame range — by the action, otherwise playback stays on frame 1
    try:
        if act is not None:
            lo, hi = act.frame_range
            if hi > lo:
                sc = getattr(bpy.context, "scene", None)
                if sc is not None:
                    sc.frame_start = int(round(lo))
                    sc.frame_end = int(round(hi))
    except Exception:
        pass
    return True



# ============================== EXPORT ===================================
# From Blender back to the game files. Stage 1: editing the SHAPE (vertex positions) with
# the same number of vertices and triangles; we do not touch normals/UVs/skeleton/indices.
# Verified: the .mdl-msh writer assembles all 4642 ship meshes byte-exactly.

EXPORT_MODES = (
    ("new_pak", "New .pak named after the model",
     "Create a separate archive; file name = model name"),
    ("folder", "Into a separate folder",
     "Write the model files into a folder, without an archive"),
    ("append", "Append to an existing .pak",
     "Add the model files to an existing archive (keep same-named files)"),
    ("replace", "Append to an existing .pak with overwrite",
     "Add to an existing archive, replacing same-named files"),
)

LAST_MODEL_BASE = ""
LAST_MODEL_DIR = ""


def _blender_to_game_vec(v):
    "Blender (x, y, z) -> game (x, z, -y). Inverse of the import one (x, -z, y)."
    return (-float(v[0]), float(v[2]), -float(v[1]))


def _export_objects():
    out = {}
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            out.setdefault(o.name.lower(), o)
    return out


def _export_purge_temp(src_dir):
    "Delete our temporary folders left over after failures."
    import shutil as _sh
    for d in (os.path.join(src_dir or "", "_tmp_export"),
              os.path.join(os.path.dirname(src_dir or ""), "_tmp_export")):
        if d and os.path.isdir(d):
            try:
                _sh.rmtree(d, ignore_errors=True)
                _log("[SC_MODEL_IEX] cleaned up the temporary folder %s" % d)
            except Exception:
                pass


def _export_archive_prefix(src_dir):
    """Path of the model folder INSIDE the pak, e.g. models/ships/race_2/l/<base>.

    The unpacked pak mirrors the archive structure, so we walk up to the
    "models" folder and rebuild the path back. Without this the files landed in the
    pak root (the pak came out flat: race2_l_t5_confessor.mdl-msh000 instead of
    models/ships/race_2/l/race2_l_t5_confessor/race2_l_t5_confessor.mdl-msh000).
    """
    p = os.path.abspath(src_dir or "")
    parts = []
    while True:
        head, tail = os.path.split(p)
        if not tail or head == p:
            return ""
        parts.append(tail)
        if tail.lower() == "models":
            return "/".join(reversed(parts))
        p = head


def _export_collect(src_dir, base, index_models=None, home_pak=None, exclude_prefixes=None):
    """Model files: only in their own folder (no recursion).

    The walk used to be recursive, and if our temporary folder
    (_tmp_export) was nearby, its files got into the build — paths nested
    (…\\_tmp_export\\sc_model_iex_merge_x\\models\\ships\\…) and on a long Windows path
    Windows returned ENOENT. All model files live in the same folder as the .mdl-hdr.
    """
    files = {}
    low = (base or "").lower()
    if not low or not src_dir or not os.path.isdir(src_dir):
        return files
    # ONLY the files that originally lay in this same pak get into the pak.
    # The game does not allow identical paths in two paks ("2 or more
    # identical copies detected"), while the Phoenix ship parts live in other paks
    # (r4_l_phoenix_boost — models_ships_race_4_l.pak, capacitor — misc.pak).
    prefix = _export_archive_prefix(src_dir)
    for f in sorted(os.listdir(src_dir)):
        if f.startswith("_"):
            continue
        fl = f.lower()
        if not fl.startswith(low):
            continue
        if exclude_prefixes and any(fl.startswith(e.lower()) for e in exclude_prefixes):
            continue
        if index_models and home_pak:
            stem_name = os.path.splitext(f)[0]
            key = ((prefix + "/" + stem_name) if prefix else stem_name).replace("/", "\\")
            entry = index_models.get(key) or index_models.get(key.replace("\\", "/"))
            other = (entry or {}).get("pak") or ""
            if other and other != home_pak:
                continue
        full = os.path.join(src_dir, f)
        if not os.path.isfile(full):
            continue
        rel = (prefix + "/" + f).replace("//", "/") if prefix else f
        try:
            with open(full, "rb") as fh:
                files[rel] = fh.read()
        except Exception as exc:
            _log("[SC_MODEL_IEX] %s not read: %s" % (f, exc))
    return files


def _matrix_is_identity(m, eps=1e-9):
    "Whether the object matrix is identity (then we do not touch coordinates needlessly)."
    for i in range(4):
        for j in range(4):
            want = 1.0 if i == j else 0.0
            if abs(float(m[i][j]) - want) > eps:
                return False
    return True


def _export_patch_meshes(MSH, files, normals=False):
    "Write vertex positions from Blender into the .mdl-msh."
    objs = _export_objects()
    changed = {}
    notes = []
    for rel in list(files):
        low = rel.lower()
        if ".mdl-msh" not in low:
            continue
        name = os.path.basename(rel).lower()
        # Parts duplicated over bones (.i2/.i3/…) have different offsets for one
        # and the same file: rebuilding from any instance corrupts the positions, so
        # we leave such files as in the source.
        if any(k.startswith(name + ".i") for k in objs):
            notes.append("%s — the part is duplicated across bones, the file is left as is" % name)
            continue
        obj = objs.get(name)
        if obj is None:
            notes.append("%s — the object is not in the scene, the file is unchanged" % name)
            continue
        if obj.get("sc_model_iex_bone"):
            notes.append("%s — the detail is bound to a bone, the file is left as is" % name)
            continue
        data = files[rel]
        try:
            info = MSH.parse_bytes(data)
        except Exception as exc:
            notes.append("%s — not parsed: %s" % (name, exc))
            continue
        n_file = int(info.get("n_vtx") or 0)
        n_blend = len(obj.data.vertices)
        if n_file != n_blend:
            notes.append("%s — vertices in the file %d, in Blender %d: skipped"
                         % (name, n_file, n_blend))
            continue
        # the inverse of the import shift of the detail (the position of its bone)
        off = obj.get("sc_model_iex_offset") or (0.0, 0.0, 0.0)
        ox, oy, oz = float(off[0]), float(off[1]), float(off[2])
        # IMPORTANT: we also take the OBJECT transformations (location/rotation/scale
        # in Object Mode) into account. For an untouched object the matrix is identity, so
        # the byte-exact "import -> export" round is preserved.
        mw = obj.matrix_world
        ident = _matrix_is_identity(mw)
        pos = []
        for v in obj.data.vertices:
            p = (v.co.x - ox, v.co.y - oy, v.co.z - oz)
            if not ident:
                p = mw @ Vector(p)
            pos.append(_blender_to_game_vec(p))

        nrm = None
        if normals:
            # direction of the original normals: if the recomputed normal of a vertex
            # points the opposite way, we flip it. Otherwise, on
            # thin plates, some vertices get a normal pointing inward and in the game
            # black spots appear (verified: msh002 — 50 vertices out of 139).
            orig_n = None
            try:
                from sc_tools import model as _MOD
                _noff, _ntyp = _MOD._find_element(info, 3)
                if _noff is not None and _ntyp not in (None, 4):
                    orig_n = []
                    for i in range(n_file):
                        o = info["vtx_off"] + i * info["stride"] + _noff
                        orig_n.append(_MOD.dec_vec(data, o, _ntyp))
            except Exception:
                orig_n = None
            # normals: with a non-uniform scale the inverse-transposed one is needed
            nmat = None
            if not ident:
                try:
                    nmat = mw.to_3x3().inverted().transposed()
                except Exception:
                    nmat = mw.to_3x3()
            nrm = []
            for v in obj.data.vertices:
                n = v.normal
                g = Vector((n.x, n.y, n.z))
                if nmat is not None:
                    g = nmat @ g
                g = _blender_to_game_vec(g)
                ln = (g[0] * g[0] + g[1] * g[1] + g[2] * g[2]) ** 0.5
                if ln > 1e-9:
                    nrm.append((g[0] / ln, g[1] / ln, g[2] / ln))
                else:
                    nrm.append((0.0, 1.0, 0.0))
        if nrm is not None and orig_n and len(orig_n) == len(nrm):
            fixed = 0
            out = []
            for i, nn in enumerate(nrm):
                on = orig_n[i]
                d = on[0] * nn[0] + on[1] * nn[1] + on[2] * nn[2]
                if d < 0.0:
                    out.append((-nn[0], -nn[1], -nn[2]))
                    fixed += 1
                else:
                    out.append(nn)
            if fixed:
                notes.append("%s — flipped normals on %d vertices (they pointed inward)"
                             % (name, fixed))
            nrm = out
        try:
            block = MSH.rebuild_vertices(info, data=data, positions=pos, normals=nrm)
            MSH.set_vertices(info, block)
            out = MSH.build(info, opaque=MSH.opaque_fields(info))
        except Exception as exc:
            notes.append("%s — not written: %s" % (name, exc))
            continue
        if out != data:
            files[rel] = out
            changed[rel] = n_blend
    return changed, notes


def _export_patch_hdr(MSH, files, base):
    """Update the bounds in .mdl-hdr (min at 0x00, max at 0x10).

    The bounds are computed from the CONTENTS of the model files, not from scene objects:
    the scene usually has only LOD0, so object-based bounds came out smaller than the
    original (for Confessor -3.04 vs -3.06). The original .mdl-hdr contains the
    union of ALL meshes of the model — that is how we compute it.
    """
    import struct as _st
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    stem = (base or "").lower() + ".mdl-msh"
    used = 0
    for rel, data in files.items():
        if stem not in os.path.basename(rel).lower():
            continue
        try:
            info = MSH.parse_bytes(data)
        except Exception:
            continue
        for p in (info.get("positions") or []):
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
        used += 1
    if not used or lo[0] > hi[0]:
        return False
    want = (base + ".mdl-hdr").lower()
    for rel in list(files):
        if os.path.basename(rel).lower() != want:
            continue
        data = bytearray(files[rel])
        if len(data) < 0x20:
            return False
        _st.pack_into("<3f", data, 0x00, lo[0], lo[1], lo[2])
        _st.pack_into("<3f", data, 0x10, hi[0], hi[1], hi[2])
        files[rel] = bytes(data)
        _log("[SC_MODEL_IEX] .mdl-hdr bounds updated from %d meshes: %.3f..%.3f  %.3f..%.3f  %.3f..%.3f"
             % (used, lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))
        return True
    return False


def _export_write_tree(dst, files):
    for rel, data in files.items():
        p = _lp(os.path.join(dst, rel.replace("/", os.sep)))
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


def _lp(path):
    """A path suitable for Windows and longer than 260 characters.

    Previously, with deep nesting (cache + models/ships/race_2/l + temporary
    folder + path inside the pak) open() failed with Errno 2: MAX_PATH.
    """
    path = os.path.abspath(path)
    if os.name == "nt" and len(path) > 240 and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def _export_mkdir(root, prefix):
    """Create the working folder with NORMAL permissions.

    tempfile.mkdtemp on Windows creates a directory with mode 0700 (owner only),
    and under a restricted token writes inside it are refused (PermissionError).
    os.makedirs on the same folder works.
    """
    import random
    base = prefix + "%08x" % random.getrandbits(32)
    path = os.path.join(root, base) if root else base
    os.makedirs(path, exist_ok=True)
    return path


def _export_temp_root():
    """Absolute folder for temporary pak builds.

    We take the first one that is actually writable:
      1) the cache directory from the add-on settings;
      2) the folder of the last import (next to the model);
      3) the Blender temporary directory;
      4) the system temporary directory.
    Previously, with an empty cache the path came out RELATIVE and landed in the current
    process folder (the game directory, read-only) — hence WinError 5.
    """
    def ok(d):
        try:
            if not d:
                return None
            d = os.path.abspath(bpy.path.abspath(d))
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, "_sc_model_iex_write_probe")
            with open(probe, "wb") as fh:
                fh.write(b"x")
            os.remove(probe)
            return d
        except Exception:
            return None

    cands = []
    if bpy.app.tempdir:
        cands.append(os.path.join(bpy.app.tempdir, "sc_model_iex_tmp"))
    try:
        prefs = _addon_prefs(bpy.context)
        if prefs and prefs.cache_dir:
            cands.append(os.path.join(bpy.path.abspath(prefs.cache_dir),
                                      "_tmp_export"))
    except Exception:
        pass
    if LAST_MODEL_DIR:
        # NOT inside the model folder: otherwise the builder would pull it into itself
        cands.append(os.path.join(os.path.dirname(LAST_MODEL_DIR), "_tmp_export"))
    cands.append(os.path.join(bpy.app.tempdir or "", "sc_model_iex_export"))
    cands.append(os.path.join(tempfile.gettempdir(), "sc_model_iex_export"))
    for c in cands:
        d = ok(c)
        if d:
            return d
    raise RuntimeError("no writable folder available for building the .pak")


def _export_pack(files, out_pak):
    "Build a .pak from a set of files (in a temporary folder)."
    import shutil as _sh
    from sc_tools import pak as SP
    root = _export_temp_root()
    if not root:
        raise RuntimeError("no folder available for the build")
    tmp = _export_mkdir(root, "sc_model_iex_exp_")
    try:
        _export_write_tree(tmp, files)
        SP.pack_dir(tmp, out_pak)
    finally:
        _sh.rmtree(tmp, ignore_errors=True)
    return out_pak


def _export_merge_pak(target_pak, files, replace):
    "Append files to an existing .pak. Returns (added, skipped, backup)."
    import random
    import shutil as _sh
    from sc_tools import pak as SP
    root = _export_temp_root()
    if not root:
        raise RuntimeError("no folder available for the build")
    tmp = _export_mkdir(root, "sc_model_iex_merge_")
    bak = ""
    try:
        SP.unpack_file(target_pak, tmp)
        added = kept = replaced = 0
        for rel, data in files.items():
            p = _lp(os.path.join(tmp, rel.replace("/", os.sep)))
            exists = os.path.exists(p)
            if exists and not replace:
                kept += 1
                continue
            d = os.path.dirname(p)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(data)
            if exists:
                replaced += 1
            else:
                added += 1
        new_pak = os.path.join(root, "sc_model_iex_new_%08x.pak" % random.getrandbits(32))
        tdir = os.path.dirname(os.path.abspath(target_pak)) or "."
        probe = os.path.join(tdir, "_sc_model_iex_probe.tmp")
        try:
            with open(probe, "wb") as fh:
                fh.write(b"x")
            os.remove(probe)
        except Exception:
            raise RuntimeError(
                "the pak folder is write-protected: %s (administrator rights are required)" % tdir)
        SP.pack_dir(tmp, new_pak)
        bak = target_pak + ".bak"
        try:
            if not os.path.exists(bak):
                _sh.copy2(target_pak, bak)
        except Exception as exc:
            _log("[SC_MODEL_IEX] pak backup not created: %s" % exc)
            bak = ""
        _sh.move(new_pak, target_pak)
        return (added, replaced, kept), bak
    finally:
        _sh.rmtree(tmp, ignore_errors=True)



LAST_EXPORT_DIR = ""


def _export_dir_current(context=None):
    "Current export folder: field -> previous choice -> add-on settings."
    try:
        prefs = _addon_prefs(context or bpy.context)
        if prefs is not None and prefs.export_dir:
            return bpy.path.abspath(prefs.export_dir)
    except Exception:
        pass
    return LAST_EXPORT_DIR


def _export_dir_remember(context, path):
    global LAST_EXPORT_DIR
    LAST_EXPORT_DIR = bpy.path.abspath(path) if path else ""
    try:
        prefs = _addon_prefs(context)
        if prefs is not None:
            prefs.export_dir = LAST_EXPORT_DIR
    except Exception:
        pass
    return LAST_EXPORT_DIR


class SC_MODEL_IEX_OT_pick_export_dir(bpy.types.Operator):
    "Choose the folder where exported models are stored"

    bl_idname = "sc_model_iex.pick_export_dir"
    bl_label = _L("Model export folder")
    bl_options = {"REGISTER", "INTERNAL"}

    directory: StringProperty(subtype="DIR_PATH")
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.directory:
            self.directory = _export_dir_current(context) or bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.directory:
            self.report({"ERROR"}, "folder not selected")
            return {"CANCELLED"}
        path = _export_dir_remember(context, self.directory)
        self.report({"INFO"}, "export folder: %s" % path)
        try:
            bpy.ops.sc_model_iex.export_model("INVOKE_DEFAULT")
        except Exception:
            pass
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_model(bpy.types.Operator):
    "Star Conflict model export: from Blender back to the game files"

    bl_idname = "sc_model_iex.export_model"
    bl_label = _L("Star Conflict: export model")
    bl_options = {"REGISTER"}

    mode: EnumProperty(
        name=_L("Where to write"), items=EXPORT_MODES, default="new_pak",
        description=_L("Where to save the model files"))
    base: StringProperty(
        name=_L("Model name"), default="",
        description=_L("Model base name, e.g. race2_l_t5_confessor (empty — take it from the active object)"))
    src_dir: StringProperty(
        name=_L("Folder with the model files"), subtype="DIR_PATH", default="",
        description=_L("Where untouched files come from (pak cache or an unpacked copy). Empty — the folder of the last import"))
    out_dir: StringProperty(
        name=_L("Where to save"), subtype="DIR_PATH", default="",
        description=_L("Folder for the new .pak or for the model files"))
    target_pak: StringProperty(
        name=_L("Existing .pak"), subtype="FILE_PATH", default="",
        description=_L("An existing archive for the append and append-with-overwrite modes"))
    rebuild_foreign: BoolProperty(
        name=_L("Rebuild the paks of parts from other paks"),
        default=False,
        description=_L("If a detail (capacitor, boosters, the common engine) lives in another pak and its geometry was changed — update ITS pak (with a backup). Off: changes to such details are not saved, the log tells which pak they live in"))
    patch_hdr: BoolProperty(
        name=_L("Update bounds (.mdl-hdr)"), default=True,
        description=_L("Recompute the model bbox from the Blender vertices"))
    recalc_normals: BoolProperty(
        name=_L("Recompute normals"), default=False,
        description=_L("Write normals from Blender instead of the original ones. Needed if the shape was changed a lot: otherwise the lighting will not match the new geometry. Off by default — this way exporting an untouched model stays byte-identical to the original"))

    def invoke(self, context, event):
        # FIRST export: the folder does not exist yet — we must ask to choose it.
        # After that the field fills itself and is remembered between sessions.
        if not self.out_dir:
            self.out_dir = _export_dir_current(context)
        if not self.out_dir:
            try:
                bpy.ops.sc_model_iex.pick_export_dir("INVOKE_DEFAULT",
                                           directory=bpy.path.abspath("//"))
            except Exception:
                pass
            return {"FINISHED"}
        self.out_dir = _export_dir_remember(context, self.out_dir)
        if not self.base:
            act = context.view_layer.objects.active
            name = act.name if act is not None else ""
            low = name.lower()
            for tail in (".mdl-hdr", ".mdl-msh", ".mdl-skl"):
                if tail in low:
                    name = name[:low.index(tail)]
                    break
            self.base = LAST_MODEL_BASE or name
        if not self.src_dir and LAST_MODEL_DIR:
            self.src_dir = LAST_MODEL_DIR
        if not self.out_dir and self.src_dir:
            self.out_dir = os.path.dirname(self.src_dir.rstrip("/\\")) or self.src_dir
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "mode")
        layout.prop(self, "base")
        layout.prop(self, "src_dir")
        box = layout.box()
        box.label(text=_L("Export folder: %s")
                  % (self.out_dir or "(not chosen yet)"), icon="FILE_FOLDER")
        row = box.row(align=True)
        row.prop(self, "out_dir", text=_L(""))
        row.operator("sc_model_iex.pick_export_dir", text=_L("Choose folder…"), icon="FILEBROWSER")
        if self.mode in ("new_pak", "folder"):
            pass
        else:
            layout.prop(self, "target_pak")
        layout.prop(self, "patch_hdr")
        layout.prop(self, "recalc_normals")
        layout.prop(self, "rebuild_foreign")
        layout.operator(SC_MODEL_IEX_OT_show_faq.bl_idname, icon="QUESTION")

    def execute(self, context):
        try:
            M, MAT, TEX = _ensure_sc_tools(context)
            from sc_tools import msh as MSH
            base = os.path.basename((self.base or "").strip())
            for tail in (".mdl-hdr", ".mdl-msh", ".mdl-skl", ".model", ".mdf"):
                if base.lower().endswith(tail):
                    base = base[:-len(tail)]
            if not base:
                self.report({"ERROR"}, "model name not set")
                return {"CANCELLED"}
            src_dir = bpy.path.abspath(self.src_dir) if self.src_dir else LAST_MODEL_DIR
            if not src_dir or not os.path.isdir(src_dir):
                self.report({"ERROR"}, "folder with the model files not found")
                return {"CANCELLED"}
            _idx = {}
            try:
                _idx = (_load_index(context) or {}).get("models") or {}
            except Exception:
                _idx = {}
            _prefix = _export_archive_prefix(src_dir)
            _rel = ((_prefix + "/" + os.path.splitext(os.path.basename(base))[0])
                    if _prefix else os.path.splitext(os.path.basename(base))[0])
            _home = ((_idx.get(_rel.replace("/", "\\")) or _idx.get(_rel) or {}).get("pak") or "")
            _excl = set()
            try:
                _excl = set((context.scene.get("sc_model_iex_siblings") or {}).keys())
            except Exception:
                _excl = set()
            files = _export_collect(src_dir, base, index_models=_idx, home_pak=_home,
                                    exclude_prefixes=_excl)
            _export_purge_temp(src_dir)
            if not files:
                self.report({"ERROR"},
                            "no model files \"%s\" in the folder" % base)
                return {"CANCELLED"}
            _log_reset()
            _log("[SC_MODEL_IEX] model export %s: files found %d" % (base, len(files)))
            changed, notes = _export_patch_meshes(
                MSH, files, normals=bool(self.recalc_normals))
            for n in notes:
                _log("[SC_MODEL_IEX] %s" % n)
            _log("[SC_MODEL_IEX] meshes changed: %d" % len(changed))
            if not changed:
                _log("[SC_MODEL_IEX] geometry unchanged — the files match the originals")
            if self.patch_hdr:
                _export_patch_hdr(MSH, files, base)
            out_dir = bpy.path.abspath(self.out_dir) if self.out_dir else src_dir
            if self.mode in ("new_pak", "folder"):
                _export_dir_remember(context, out_dir)

            if self.mode == "folder":
                dst = os.path.join(out_dir, base)
                _export_write_tree(dst, files)
                self.report({"INFO"}, "model files written: %s" % dst)
            elif self.mode == "new_pak":
                os.makedirs(out_dir, exist_ok=True)
                out_pak = os.path.join(out_dir, base + ".pak")
                _export_pack(files, out_pak)
                self.report({"INFO"}, "created %s (%.1f MB)"
                            % (out_pak, os.path.getsize(out_pak) / 1048576.0))
            else:
                target = bpy.path.abspath(self.target_pak) if self.target_pak else ""
                if not target or not os.path.exists(target):
                    self.report({"ERROR"}, "existing .pak not found")
                    return {"CANCELLED"}
                res, bak = _export_merge_pak(target, files,
                                            self.mode == "replace")
                added, replaced, kept = res
                self.report({"INFO"},
                            "pak %s: added %d, replaced %d, skipped %d%s"
                            % (os.path.basename(target), added, replaced, kept,
                               (" (backup %s)" % os.path.basename(bak)) if bak else ""))
            # Parts from other paks (Phoenix: capacitor -> misc.pak, boosters and
            # r4_l_eng -> models_ships_race_4_l.pak). They must not be put into the
            # hull pak — the engine will not start because of duplicate paths.
            try:
                _sib = {}
                try:
                    _sib = dict(context.scene.get("sc_model_iex_siblings") or {})
                except Exception:
                    _sib = {}
                _game = bpy.path.abspath(getattr(_addon_prefs(context), "game_root", "") or "")
                for _part, _pak in sorted(_sib.items()):
                    if not _pak:
                        continue
                    _pl = _part.lower()
                    _pfiles = {}
                    _pfx = _export_archive_prefix(src_dir)
                    for _f in sorted(os.listdir(src_dir)):
                        if not _f.lower().startswith(_pl):
                            continue
                        _full = os.path.join(src_dir, _f)
                        if not os.path.isfile(_full):
                            continue
                        _rel = ((_pfx + "/" + _f) if _pfx else _f)
                        with open(_full, "rb") as _fh:
                            _pfiles[_rel] = _fh.read()
                    if not _pfiles:
                        continue
                    _changed, _notes = _export_patch_meshes(MSH, _pfiles,
                                                           normals=bool(self.recalc_normals))
                    for _n in _notes:
                        _log("[SC_MODEL_IEX] %s" % _n)
                    if not _changed:
                        continue
                    _target = os.path.join(_game, _pak)
                    if self.rebuild_foreign and os.path.exists(_target):
                        _res, _bak = _export_merge_pak(_target, _pfiles, True)
                        _log("[SC_MODEL_IEX] detail %s: pak %s updated (%s)%s"
                             % (_part, _pak, "replaced %d" % _res[1],
                                (" backup %s" % os.path.basename(_bak)) if _bak else ""))
                    else:
                        _log("[SC_MODEL_IEX] WARNING: detail %s was changed, it lives in %s — it does not go into the hull pak (enable \"Rebuild the paks of parts from other paks\" to update its pak)" % (_part, _pak))
            except Exception as _exc:
                _log("[SC_MODEL_IEX] parts from other paks not processed: %s" % _exc)
            _log("[SC_MODEL_IEX] export finished")
        except Exception as exc:
            import traceback
            _log("""[SC_MODEL_IEX] EXPORT ERROR:
%s""" % traceback.format_exc())
            self.report({"ERROR"}, "%s (details in SC_MODEL_IEX_log)" % exc)
            return {"CANCELLED"}
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_all(bpy.types.Operator):
    "Star Conflict export: only what is checked gets written"
    bl_idname = "sc_model_iex.export_sc"
    bl_label = _L("Star Conflict export")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    template: bpy.props.StringProperty(name=_L(".mdl-msh template"), subtype="FILE_PATH", default="")
    hdr_template: bpy.props.StringProperty(name=_L(".mdl-hdr template"), subtype="FILE_PATH", default="")
    write_meshes: bpy.props.BoolProperty(name=_L("Meshes + .mdl-hdr"), default=True)
    gen_lod: bpy.props.BoolProperty(name=_L("Auto-LOD"), default=False)
    lod_levels: bpy.props.IntProperty(name=_L("LOD levels"), default=2, min=1, max=4)
    write_model: bpy.props.BoolProperty(name=_L(".model/.skins"), default=True)
    write_mdp: bpy.props.BoolProperty(name=_L("Collision (.mdp)"), default=True)
    write_skl: bpy.props.BoolProperty(name=_L("Skeleton (.mdl-skl)"), default=False)
    write_anim: bpy.props.BoolProperty(name=_L("Animations (.anim)"), default=False)
    write_mdf: bpy.props.BoolProperty(name=_L(".mdf from materials"), default=False)
    pack: bpy.props.BoolProperty(name=_L("Build .pak"), default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def execute(self, context):
        out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
        os.makedirs(out, exist_ok=True)
        meshes = ([o for o in context.selected_objects if o.type == "MESH"]
                  or [o for o in context.scene.objects if o.type == "MESH"])
        arm = context.view_layer.objects.active
        if arm is None or arm.type != "ARMATURE":
            arms = [o for o in context.selected_objects if o.type == "ARMATURE"]
            arm = arms[0] if arms else None
        if not meshes and arm is None:
            self.report({"ERROR"}, "neither meshes nor an armature")
            return {"CANCELLED"}
        base = self.base
        if not base and meshes:
            src = meshes[0].get("sc_src_base") or meshes[0].get("sc_src_msh") or meshes[0].name
            base = os.path.basename(src).split(".mdl-msh")[0]
        base = base or "model"
        done = []
        try:
            if meshes and self.write_meshes:
                bpy.ops.object.select_all(action="DESELECT")
                for o in meshes:
                    o.select_set(True)
                context.view_layer.objects.active = meshes[0]
                bpy.ops.sc_model_iex.export_mesh_new(
                    base=base, out_dir=out, template=self.template,
                    hdr_template=self.hdr_template, gen_lod=self.gen_lod,
                    lod_levels=(self.lod_levels if self.gen_lod else 0))
                done.append("meshes")
            if meshes and self.write_model:
                bpy.ops.sc_model_iex.export_model_file(base=base, out_dir=out)
                done.append(".model")
            if meshes and self.write_mdp:
                bpy.ops.sc_model_iex.export_mdp(base=base, out_dir=out)
                done.append(".mdp")
            if meshes and self.write_mdf:
                bpy.ops.sc_model_iex.export_mdf(base=base, out_dir=out)
                done.append(".mdf")
            if arm is not None and (self.write_skl or self.write_anim):
                bpy.ops.object.select_all(action="DESELECT")
                arm.select_set(True)
                context.view_layer.objects.active = arm
                if self.write_skl:
                    bpy.ops.sc_model_iex.export_skeleton(base=base, out_dir=out)
                    done.append(".skl")
                if self.write_anim:
                    bpy.ops.sc_model_iex.export_animation(base=base, out_dir=out)
                    done.append(".anim")
            # без арматуры: скелет и анимации берём из шаблона (переименовывая базу)
            if arm is None and (self.write_skl or self.write_anim) and self.template:
                tmpl_dir = os.path.dirname(bpy.path.abspath(self.template))
                tmpl_base = os.path.basename(self.template).split(".mdl-msh")[0]
                if self.write_skl:
                    src = os.path.join(tmpl_dir, tmpl_base + ".mdl-skl")
                    if os.path.exists(src):
                        os.makedirs(out, exist_ok=True)
                        import shutil as _sh
                        _sh.copy2(src, os.path.join(out, base + ".mdl-skl"))
                        done.append(".skl(из шаблона)")
                if self.write_anim:
                    import glob as _glob
                    import shutil as _sh
                    for p in _glob.glob(os.path.join(tmpl_dir, tmpl_base + "_*.anim")):
                        name = os.path.basename(p).replace(tmpl_base, base, 1)
                        _sh.copy2(p, os.path.join(out, name))
                        done.append(os.path.splitext(name)[1] + "(из шаблона)")
            if self.pack:
                from sc_tools import pak as PAK
                PAK.pack_dir(out, os.path.join(out, base + ".pak"))
                done.append(".pak")
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "export %s: %s -> %s" % (base, ", ".join(done) or "nothing", out))
        return {"FINISHED"}


def menu_func_export(self, context):
    self.layout.operator(SC_MODEL_IEX_OT_export_all.bl_idname, text=_L("Star Conflict…"))




# ============================ EXPORT "FROM SCRATCH" ============================
# New mesh topology, skeleton and animations from Blender (not just shape editing).

class SC_MODEL_IEX_OT_export_mesh_new(bpy.types.Operator):
    "Write .mdl-msh for the selected objects: any topology"
    bl_idname = "sc_model_iex.export_mesh_new"
    bl_label = _L("Export meshes (new topology)")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    template: bpy.props.StringProperty(
        name=_L(".mdl-msh template"), subtype="FILE_PATH", default="",
        description=_L("File the vertex format (fmt/stride) is taken from"))
    scale: bpy.props.FloatProperty(name=_L("Scale"), default=1.0, min=0.0001, max=1000.0)
    hdr_template: bpy.props.StringProperty(
        name=_L(".mdl-hdr template (optional)"), subtype="FILE_PATH", default="",
        description=_L("Unknown header fields are taken from it; bbox and LOD are recomputed"))
    check_after_export: bpy.props.BoolProperty(
        name=_L("Validate after export"), default=True,
        description=_L("Compare the result with the source and show a report"))
    gen_lod: bpy.props.BoolProperty(
        name=_L("Generate LOD"), default=False,
        description=_L("Simplify LOD0 and add levels 1..N (Decimate)"))
    lod_levels: bpy.props.IntProperty(
        name=_L("Auto-LOD levels"), default=0, min=0, max=4,
        description=_L("Generate this many simplified LODs from LOD0 (Decimate)"))
    keep_normals: bpy.props.BoolProperty(
        name=_L("Keep original normals"), default=False,
        description=_L("By default normals are recomputed when geometry is edited; on — write the normals from the file even if vertices were moved"))

    def execute(self, context):
        from sc_tools import export_new as EX
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "select meshes")
            return {"CANCELLED"}
        tpl = bpy.path.abspath(self.template)
        if tpl and not os.path.exists(tpl):
            self.report({"ERROR"}, ".mdl-msh template not found: %s" % tpl)
            return {"CANCELLED"}
        # an empty template — the built-in vertex format (32, with skeleton 36)
        arm = None
        for o in objs:
            for m in o.modifiers:
                if m.type == "ARMATURE" and m.object is not None:
                    arm = m.object
                    break
            if arm:
                break
        try:
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            _prog_exp = _Progress(context, max(1, len(objs)))
            try:
                groups = EX.export_meshes(objs, self.base, out, tpl, self.scale, arm,
                                      keep_normals=self.keep_normals,
                                      lod_levels=(self.lod_levels if self.gen_lod else 0),
                                      progress=_prog_exp.step)
            finally:
                _prog_exp.end()
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        # if the vertex count of the objects did not change — the model was not edited,
        # and we keep the original bbox from the template (per base separately)
        unchanged_by_src = {}
        from sc_tools import msh as _MSH
        for o in objs:
            src = o.get("sc_src_msh") or ""
            key = os.path.basename(src).split(".mdl-msh")[0] if ".mdl-msh" in src else ""
            ok = False
            if src and os.path.exists(src):
                try:
                    ok = _MSH.parse_file(src)["n_vtx"] == len(o.data.vertices)
                except Exception:
                    ok = False
            unchanged_by_src[key] = unchanged_by_src.get(key, True) and ok
        total = sum(len(g["files"]) for g in groups.values())
        msg = "meshes written: %d into %d base(s) -> %s" % (total, len(groups), out)
        hdrs = []
        for out_base, info in groups.items():
            tpl_hdr = info.get("template") or bpy.path.abspath(self.hdr_template)
            try:
                mat_count = None
                cand_mdf = os.path.join(out, out_base + ".mdf")
                if os.path.exists(cand_mdf):
                    try:
                        from sc_tools import material as _MAT
                        mat_count = len(_MAT.parse_mdf(cand_mdf))
                    except Exception:
                        mat_count = None
                hdr = EX.export_hdr(info["files"], out_base, out, tpl_hdr,
                                    lod_sizes=info["lod_sizes"],
                                    keep_template_bbox=unchanged_by_src.get(
                                        info.get("src_base", ""), False),
                                    material_count=mat_count)
                hdrs.append(os.path.basename(hdr))
            except Exception as exc:
                msg += "; hdr %s not built: %s" % (out_base, exc)
        if hdrs:
            msg += "; hdr: %s" % ", ".join(hdrs)

        if self.check_after_export:
            try:
                from sc_tools import validate as _V
                ok = warn = err = 0
                for out_base2, info2 in groups.items():
                    src_dir = os.path.dirname(info2.get("template") or "")
                    src_base2 = info2.get("src_base") or ""
                    if not (src_dir and src_base2):
                        continue
                    rows2 = _V.roundtrip(os.path.join(src_dir, src_base2), out, out_base2,
                                         kinds=("hdr", "mesh"))
                    a2, b2, c2 = _V.summarize(rows2)
                    ok += a2
                    warn += b2
                    err += c2
                if ok or warn or err:
                    msg += "; check: OK %d, WARN %d, ERR %d" % (ok, warn, err)
            except Exception as exc:
                msg += "; check not performed: %s" % exc
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_skeleton(bpy.types.Operator):
    "Write .mdl-skl from the active armature"
    bl_idname = "sc_model_iex.export_skeleton"
    bl_label = _L("Export skeleton (.mdl-skl)")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")

    def execute(self, context):
        from sc_tools import export_new as EX
        arm = context.view_layer.objects.active
        if arm is None or arm.type != "ARMATURE":
            arms = [o for o in context.selected_objects if o.type == "ARMATURE"]
            arm = arms[0] if len(arms) == 1 else None
        if arm is None:
            self.report({"ERROR"}, "select an armature")
            return {"CANCELLED"}
        try:
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            path = EX.export_skeleton(arm, self.base, out)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "skeleton: %s" % path)
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_animation(bpy.types.Operator):
    "Write .anim from the armature action"
    bl_idname = "sc_model_iex.export_animation"
    bl_label = _L("Export animation (.anim)")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("File name"), default="anim")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    fps: bpy.props.FloatProperty(name=_L("Frames/s"), default=24.0, min=1.0, max=240.0)

    def execute(self, context):
        from sc_tools import export_new as EX
        arm = context.view_layer.objects.active
        if arm is None or arm.type != "ARMATURE":
            arms = [o for o in context.selected_objects if o.type == "ARMATURE"]
            arm = arms[0] if len(arms) == 1 else None
        if arm is None or arm.animation_data is None or arm.animation_data.action is None:
            self.report({"ERROR"}, "no armature with an active action")
            return {"CANCELLED"}
        try:
            action = arm.animation_data.action
            scales = [fc.data_path for fc in _action_fcurves(action)
                      if fc.data_path.endswith(".scale") or fc.data_path == "scale"]
            if scales:
                self.report({"WARNING"}, "scale is not written: .anim stores only position and rotation (%d scale channels skipped)" % len(scales))
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            path = EX.export_animation(arm, arm.animation_data.action, self.base, out, self.fps)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "animation: %s" % path)
        return {"FINISHED"}




class SC_MODEL_IEX_OT_export_hdr(bpy.types.Operator):
    "Rebuild .mdl-hdr from ready .mdl-msh### files (bbox + LOD)"
    bl_idname = "sc_model_iex.export_hdr"
    bl_label = _L("Build .mdl-hdr from meshes")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    meshes_dir: bpy.props.StringProperty(name=_L("Folder with .mdl-msh###"), subtype="DIR_PATH", default="")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    template: bpy.props.StringProperty(name=_L(".mdl-hdr template"), subtype="FILE_PATH", default="")
    lod: bpy.props.StringProperty(
        name=_L("LOD: group sizes"), default="",
        description=_L("For example \"4,4,1,1\" — how many meshes in each LOD; empty — no LOD"))
    mdf: bpy.props.StringProperty(
        name=_L(".mdf (optional)"), subtype="FILE_PATH", default="",
        description=_L("material_count for +0x38 is taken from it; without it — from the template"))

    def execute(self, context):
        from sc_tools import hdrwrite as HW
        src = bpy.path.abspath(self.meshes_dir) or bpy.path.abspath("//")
        out = bpy.path.abspath(self.out_dir) or src
        try:
            files = [os.path.join(src, f) for f in sorted(os.listdir(src))
                     if f.startswith(self.base + ".mdl-msh")]
            if not files:
                raise RuntimeError("no %s.mdl-msh### in %s" % (self.base, src))
            mdf = bpy.path.abspath(self.mdf) or ""
            if not mdf:
                cand_mdf = os.path.join(src, self.base + ".mdf")
                if os.path.exists(cand_mdf):
                    mdf = cand_mdf
            lod_sizes = None
            if self.lod.strip():
                try:
                    lod_sizes = [int(v) for v in self.lod.replace(";", ",").split(",") if v.strip()]
                except ValueError:
                    lod_sizes = None
            mat = None
            if mdf and os.path.exists(mdf):
                try:
                    from sc_tools import material as MAT
                    mat = len(MAT.parse_mdf(mdf))
                except Exception:
                    mat = None
            data = HW.build_hdr(files, bpy.path.abspath(self.template) or None,
                                material_count=mat, lod_sizes=lod_sizes)
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, self.base + ".mdl-hdr")
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "hdr: %s (%d meshes)" % (path, len(files)))
        return {"FINISHED"}




class SC_MODEL_IEX_OT_export_model_file(bpy.types.Operator):
    "Write <base>.model and <base>.skins (model description)"
    bl_idname = "sc_model_iex.export_model_file"
    bl_label = _L("Export .model/.skins")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    actions_prefix: bpy.props.StringProperty(
        name=_L("Only actions with the prefix"), default="",
        description=_L("Empty — all Blender actions; otherwise only those starting with the prefix"))

    def execute(self, context):
        from sc_tools import model_write as MW
        try:
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            os.makedirs(out, exist_ok=True)
            # we keep the original .model/.skins instead of rebuilding them: they contain
            # comments, token order and unknown fields
            src_base = ""
            for o in bpy.context.scene.objects:
                src_base = o.get("sc_src_base") or ""
                if src_base:
                    break
            if not src_base:
                for o in context.scene.objects:
                    src = o.get("sc_src_msh") or ""
                    if ".mdl-msh" in src:
                        src_base = src.split(".mdl-msh")[0]
                        break
            old_name = os.path.basename(src_base) if src_base else ""
            # which actions belong to THIS model: its armatures and the base name
            used_actions = set()
            for o in context.scene.objects:
                if o.type != "ARMATURE" or not o.animation_data:
                    continue
                if old_name and old_name not in o.name:
                    continue
                ad = o.animation_data
                if ad.action:
                    used_actions.add(ad.action.name)
                for tr in ad.nla_tracks:
                    for st in tr.strips:
                        if st.action:
                            used_actions.add(st.action.name)
            anims = []
            for act in bpy.data.actions:
                name = act.name
                if self.actions_prefix and not name.startswith(self.actions_prefix):
                    continue
                if old_name:
                    if old_name not in name and name not in used_actions:
                        continue   # an action from another model in the same scene
                elif name not in used_actions:
                    # a new model without a source: we take only the actions
                    # bound to its armature (otherwise we drag foreign ones from the scene)
                    continue
                clip = name
                # the importer puts actions with the add-on prefix — it is not needed in .model
                if clip.startswith("SC_MODEL_IEX_"):
                    clip = clip[len("SC_MODEL_IEX_"):]
                if clip.lower().startswith("idle") or clip.lower().startswith("aim"):
                    activity = "IDLE"
                else:
                    activity = "CUSTOM1"
                short = clip
                if old_name and short.startswith(old_name + "_"):
                    short = short[len(old_name) + 1:]
                elif short.startswith(self.base + "_"):
                    short = short[len(self.base) + 1:]
                anims.append((clip, "%s_%s.anim" % (self.base, short), activity, 1.0))
            model = None
            skins = None
            if src_base:
                # the template is also used when renaming the base: the name
                # changes only in the code fields, the comments are preserved
                old_name = os.path.basename(src_base)
                cand = src_base + ".model"
                if os.path.exists(cand):
                    model = MW.patch_model(open(cand, "rb").read(), anims,
                                           old_base=old_name,
                                           new_base=self.base)
                cand_s = src_base + ".skins"
                if os.path.exists(cand_s):
                    data_s = open(cand_s, "rb").read()
                    if old_name != self.base:
                        data_s = data_s.replace(old_name.encode("latin1"),
                                                self.base.encode("latin1"))
                    skins = data_s
            if model is None:
                model = MW.build_model(self.base, anims)
            if skins is None:
                skins = MW.build_skins(self.base)
            with open(os.path.join(out, self.base + ".model"), "wb") as fh:
                fh.write(model)
            with open(os.path.join(out, self.base + ".skins"), "wb") as fh:
                fh.write(skins)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "model/skins: %d clips -> %s" % (len(anims), out))
        return {"FINISHED"}


def _hull_volume(pts):
    "Volume of the convex hull from points (bmesh)."
    import bmesh
    if len(pts) < 4:
        return 0.0
    bm = bmesh.new()
    try:
        for c in pts:
            bm.verts.new(c)
        bm.verts.ensure_lookup_table()
        bmesh.ops.convex_hull(bm, input=bm.verts)
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        vol = 0.0
        for f in bm.faces:
            vs = [v.co for v in f.verts]
            if len(vs) != 3:
                continue
            a, b, c = vs
            vol += a.dot(b.cross(c)) / 6.0
        return abs(vol)
    finally:
        bm.free()


def _hull_reduce(pts, target):
    """Greedy vertex removal: at each step we drop the vertex that
    reduces the hull volume the least."""
    pts = list(pts)
    if len(pts) <= target:
        return pts
    if len(pts) > 160:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        cz = sum(p[2] for p in pts) / len(pts)
        pts = sorted(pts, key=lambda p: -((p[0]-cx)**2 + (p[1]-cy)**2 + (p[2]-cz)**2))[:120]
    while len(pts) > target and len(pts) > 4:
        vol_now = _hull_volume(pts)
        best = None
        for i in range(len(pts)):
            cand = pts[:i] + pts[i + 1:]
            v = _hull_volume(cand)
            if best is None or v > best[0]:
                best = (v, i)
        if best is None or best[0] <= 0:
            break
        pts = pts[:best[1]] + pts[best[1] + 1:]
    return pts


def _mdp_hull_tris(objs, target=12):
    """Game collision for a ship: LOD0 convex hull simplified to ~12
    vertices with volume maximization (median ratio to the game one is 0.97)."""
    import bmesh
    lods = {}
    for o in objs:
        lods.setdefault(int(o.get("sc_lod", 0)), []).append(o)
    chosen = lods.get(min(lods), objs) if lods else objs
    pts = []
    try:
        for o in chosen:
            mw = o.matrix_world
            for v in o.data.vertices:
                p = mw @ v.co
                pts.append((-p.x, p.z, -p.y))
    except Exception:
        return None
    if len(pts) < 4:
        return None
    bm = bmesh.new()
    try:
        for c in pts:
            bm.verts.new(c)
        bm.verts.ensure_lookup_table()
        res = bmesh.ops.convex_hull(bm, input=bm.verts)
        extra = list(set(res.get("geom_interior", []) + res.get("geom_unused", [])))
        if extra:
            bmesh.ops.delete(bm, geom=extra, context=_L("VERTS"))
        hull_pts = [(v.co.x, v.co.y, v.co.z) for v in bm.verts]
    except Exception:
        hull_pts = []
    finally:
        bm.free()
    if len(hull_pts) < 4:
        return None
    hull_pts = _hull_reduce(hull_pts, max(4, int(target or 12)))
    bm2 = bmesh.new()
    try:
        for c in hull_pts:
            bm2.verts.new(c)
        bm2.verts.ensure_lookup_table()
        bmesh.ops.convex_hull(bm2, input=bm2.verts)
        bmesh.ops.triangulate(bm2, faces=bm2.faces[:])
        verts = [(v.co.x, v.co.y, v.co.z) for v in bm2.verts]
        tris = [tuple(verts[v.index] for v in f.verts) for f in bm2.faces]
    except Exception:
        tris = []
    finally:
        bm2.free()
    return tris or None


def _mdp_decimate(tris, target):
    "Shrink triangles with the Decimate modifier (collapse)."
    me = bpy.data.meshes.new("_mdp_tmp")
    verts = [v for t in tris for v in t]
    faces = [(i * 3, i * 3 + 1, i * 3 + 2) for i in range(len(tris))]
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new("_mdp_tmp", me)
    bpy.context.scene.collection.objects.link(ob)
    try:
        mod = ob.modifiers.new("dec", "DECIMATE")
        mod.ratio = max(0.02, min(1.0, target / float(len(faces))))
        mod.use_collapse_triangulate = True
        bpy.context.view_layer.objects.active = ob
        ob.select_set(True)
        bpy.ops.object.modifier_apply(modifier=mod.name)
        out = []
        for p in me.polygons:
            vs = [tuple(me.vertices[i].co) for i in p.vertices]
            if len(vs) == 3:
                out.append(tuple(vs))
        return out or tris
    except Exception:
        return tris
    finally:
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.meshes.remove(me)




class SC_MODEL_IEX_OT_assign_lod(bpy.types.Operator):
    "Assign LOD to the selected meshes (manual layout)"
    bl_idname = "sc_model_iex.assign_lod"
    bl_label = _L("Assign LOD")
    bl_options = {"REGISTER", "UNDO"}

    lod: bpy.props.IntProperty(name=_L("LOD"), default=0, min=0, max=15)

    def execute(self, context):
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "select meshes")
            return {"CANCELLED"}
        n = 0
        for o in objs:
            try:
                o["sc_lod"] = int(self.lod)
                n += 1
            except Exception:
                pass
        self.report({"INFO"}, "LOD %d assigned to %d objects" % (self.lod, n))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_texture(bpy.types.Operator):
    "Write .tfh/.tfd from .dds (DXT1/3/5 and uncompressed, with mips)"
    bl_idname = "sc_model_iex.export_texture"
    bl_label = _L("Export texture (.tfh/.tfd)")
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(name=_L("DDS"), subtype="FILE_PATH", default="")
    base: bpy.props.StringProperty(name=_L("Name without extension"), default="")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    idx: bpy.props.IntProperty(
        name=_L("Format code (0 — auto)"), default=0, min=0, max=63,
        description=_L("11 DXT1, 13 DXT3, 10 DXT5; 0 — detect by fourCC"))

    def execute(self, context):
        from sc_tools import texture as T
        path = bpy.path.abspath(self.filepath)
        if not path or not os.path.exists(path):
            self.report({"ERROR"}, ".dds not found")
            return {"CANCELLED"}
        try:
            data = open(path, "rb").read()
            tfh, tfd = T.dds_to_pair(data, idx=(self.idx or None))
            out = bpy.path.abspath(self.out_dir) or os.path.dirname(path)
            os.makedirs(out, exist_ok=True)
            base = self.base or os.path.splitext(os.path.basename(path))[0]
            with open(os.path.join(out, base + ".tfh"), "wb") as fh:
                fh.write(tfh)
            with open(os.path.join(out, base + ".tfd"), "wb") as fh:
                fh.write(tfd)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "texture: %s.tfh (%d B) + .tfd (%d B)" % (base, len(tfh), len(tfd)))
        return {"FINISHED"}



class SC_MODEL_IEX_OT_validate_model(bpy.types.Operator):
    "Validate the model: header, LOD, .mdf, skeleton, collision, textures"
    bl_idname = "sc_model_iex.validate_model"
    bl_label = _L("Validate model")
    bl_options = {"REGISTER"}

    base: bpy.props.StringProperty(
        name=_L("Model base (without extension)"), subtype="FILE_PATH", default="",
        description=_L("Empty — take it from the selected objects (sc_src_base/sc_src_msh)"))

    def execute(self, context):
        from sc_tools import validate as V
        base = bpy.path.abspath(self.base) if self.base else ""
        if not base:
            for o in context.scene.objects:
                src = o.get("sc_src_base") or o.get("sc_src_msh") or ""
                if src:
                    base = src.split(".mdl-msh")[0]
                    break
        if not base:
            self.report({"ERROR"}, "model base not found — specify it manually")
            return {"CANCELLED"}
        rows = V.check_model(base)
        ok, warn, err = V.summarize(rows)
        text = V.format_rows(rows)
        report_path = ""
        import tempfile
        for cand in (os.path.join(os.path.dirname(base), "SC_MODEL_IEX_report.txt"),
                     os.path.join(tempfile.gettempdir(), "SC_MODEL_IEX_report.txt")):
            try:
                with open(cand, "w", encoding="utf-8") as fh:
                    fh.write(text + "\n")
                report_path = cand
                break
            except Exception:
                continue
        print(text)
        level = "ERROR" if err else ("WARNING" if warn else "INFO")
        self.report({level}, "check: OK %d, WARN %d, ERR %d%s"
                    % (ok, warn, err, (" -> " + report_path) if report_path else ""))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_roundtrip_check(bpy.types.Operator):
    "Compare the source model and the export result (round-trip)"
    bl_idname = "sc_model_iex.roundtrip_check"
    bl_label = _L("Check round-trip")
    bl_options = {"REGISTER"}

    source: bpy.props.StringProperty(name=_L("Source (base)"), subtype="FILE_PATH", default="")
    out_dir: bpy.props.StringProperty(name=_L("Export folder"), subtype="DIR_PATH", default="")
    out_base: bpy.props.StringProperty(name=_L("Name in the export"), default="")

    def execute(self, context):
        from sc_tools import validate as V
        source = bpy.path.abspath(self.source)
        out = bpy.path.abspath(self.out_dir)
        if not source or not out:
            self.report({"ERROR"}, "source and export folder are required")
            return {"CANCELLED"}
        rows = V.roundtrip(source, out, self.out_base or None)
        ok, warn, err = V.summarize(rows)
        text = V.format_rows(rows)
        report_path = ""
        try:
            report_path = os.path.join(out, "SC_MODEL_IEX_roundtrip.txt")
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            report_path = ""
        print(text)
        level = "ERROR" if err else ("WARNING" if warn else "INFO")
        self.report({level}, "round-trip: OK %d, WARN %d, ERR %d%s"
                    % (ok, warn, err, (" -> " + report_path) if report_path else ""))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_mdf(bpy.types.Operator):
    "Write <base>.mdf from the materials of the selected objects"
    bl_idname = "sc_model_iex.export_mdf"
    bl_label = _L("Export .mdf (materials)")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    template: bpy.props.StringProperty(
        name=_L("Source .mdf (structure)"), subtype="FILE_PATH", default="",
        description=_L("The structure is taken from it; empty — look next to the meshes"))

    def execute(self, context):
        from sc_tools import model_write as MW
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            # handy for a new model: if nothing is selected — all scene meshes
            objs = [o for o in context.scene.objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "no meshes in the scene")
            return {"CANCELLED"}
        mats = []
        seen = set()
        for o in objs:
            for slot in o.material_slots:
                m = slot.material
                if m is not None and m.name not in seen:
                    seen.add(m.name)
                    mats.append(m)
        try:
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            os.makedirs(out, exist_ok=True)
            template = bpy.path.abspath(self.template)
            if not template:
                # we look for the .mdf next to the source meshes: <folder>/<base>.mdf
                for o in objs:
                    src = o.get("sc_src_msh") or ""
                    if ".mdl-msh" not in src:
                        continue
                    cand = os.path.join(os.path.dirname(src), self.base + ".mdf")
                    if os.path.exists(cand):
                        template = cand
                        break
            if template and os.path.exists(template):
                from sc_tools import material as _MAT
                _names = []
                for _m in mats:
                    _n = _m.name.split(".")[0]
                    if _n not in _names:
                        _names.append(_n)
                data = _MAT.patch_mdf_file(template, _names)
            else:
                data = MW.build_mdf(mats)
            path = os.path.join(out, self.base + ".mdf")
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "mdf: materials %d -> %s" % (len(mats), path))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_export_mdp(bpy.types.Operator):
    "Write .mdp: body meshes are built from the selected objects"
    bl_idname = "sc_model_iex.export_mdp"
    bl_label = _L("Export .mdp (collision)")
    bl_options = {"REGISTER", "UNDO"}

    base: bpy.props.StringProperty(name=_L("Model name"), default="model")
    out_dir: bpy.props.StringProperty(name=_L("Destination"), subtype="DIR_PATH", default="")
    trimesh_max_tris: bpy.props.IntProperty(
        name=_L("Triangle limit"), default=224, min=8, max=224,
        description=_L("Type 7: maximum 224 (server BVH stack 128/256); more — the server crashes"))
    collision_type: bpy.props.EnumProperty(
        name=_L("Collision type"), default="hull",
        items=(("hull", _L("Shell (type 6)"), _L("Convex hull from LOD0")),
               ("trimesh", _L("Trimesh (7)"), _L("LOD0 triangles + BVH; requires a .mdp template")),
               ("box", _L("Box (type 2)"), _L("One box by the bounds of the selected objects"))))
    per_object: bpy.props.BoolProperty(
        name=_L("Body per object"), default=False,
        description=_L("For compound_model: a separate shell for each object"))
    template: bpy.props.StringProperty(
        name=_L(".mdp template (hash/trailer)"), subtype="FILE_PATH", default="",
        description=_L("Optional: only the hash and the trailing 16 bytes are taken from it"))
    hull: bpy.props.BoolProperty(
        name=_L("Convex hull"), default=True,
        description=_L("Build the collision as a convex hull (like in the game); off — all mesh triangles"))
    target_tris: bpy.props.IntProperty(
        name=_L("Vertices in the hull"), default=12, min=4, max=64,
        description=_L("The hull is simplified to N vertices (the game median is 12, up to 16)"))
    max_tris: bpy.props.IntProperty(
        name=_L("Maximum triangles"), default=0, min=0,
        description=_L("0 — all; otherwise the model is simplified evenly (every N-th triangle)"))

    def execute(self, context):
        from mathutils import Vector
        from sc_tools import mdp_write as DP
        objs = [o for o in context.selected_objects if o.type == "MESH"]
        if not objs:
            self.report({"ERROR"}, "select meshes")
            return {"CANCELLED"}
        # we keep the original .mdp as is: it holds the convex hull (tens of
        # triangles), not the whole geometry — rebuilding from meshes gives a different file
        src_base = ""
        for o in context.scene.objects:
            src_base = o.get("sc_src_base") or ""
            if src_base:
                break
        if not src_base:
            for obj in objs:
                src = obj.get("sc_src_msh") or ""
                if ".mdl-msh" in src:
                    src_base = src.split(".mdl-msh")[0]
                    break
        src_mdp = (src_base + ".mdp") if src_base else ""
        if src_base and not self.max_tris and os.path.exists(src_mdp):
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, self.base + ".mdp")
            with open(src_mdp, "rb") as fh:
                data = fh.read()
            with open(path, "wb") as fh:
                fh.write(data)
            self.report({"INFO"}, "mdp: original saved -> %s" % path)
            return {"FINISHED"}

        mn = [1e30] * 3
        mx = [-1e30] * 3
        tris = []
        for obj in objs:
            me = obj.data
            me.calc_loop_triangles()
            mw = obj.matrix_world
            for v in me.vertices:
                p = mw @ v.co
                g = (-p.x, p.z, -p.y)
                for k in range(3):
                    mn[k] = min(mn[k], g[k])
                    mx[k] = max(mx[k], g[k])
            for t in me.loop_triangles:
                pts = []
                for li in t.loops:
                    p = mw @ me.vertices[me.loops[li].vertex_index].co
                    pts.append((-p.x, p.z, -p.y))
                tris.append((pts[0], pts[1], pts[2]))
        if self.hull:
            # in the game the .mdp is a coarse hull (median 16 triangles per 968
            # files), so we take the hull of the coarsest LOD and shrink it to
            # target_tris; if no hull came out — all triangles remain
            if self.collision_type != "trimesh":
                hull_tris = _mdp_hull_tris(objs, self.target_tris)
                if hull_tris:
                    tris = hull_tris
                mn = [min(v[i] for t in tris for v in t) for i in range(3)]
                mx = [max(v[i] for t in tris for v in t) for i in range(3)]
        if self.max_tris and len(tris) > self.max_tris:
            step = max(1, len(tris) // self.max_tris)
            tris = tris[::step][: self.max_tris]
        hsh, trailer = 0, None
        tpl = bpy.path.abspath(self.template)
        if tpl and os.path.exists(tpl):
            try:
                m = DP.parse_mdp(open(tpl, "rb").read())
                hsh, trailer = m["hash"], m["trailer"]
            except Exception:
                pass
        try:
            if self.collision_type == "trimesh":
                tpl_hdr = None
                tpl_bvh = None
                tpl_m = None
                if tpl and os.path.exists(tpl):
                    try:
                        tpl_m = DP.parse_mdp(open(tpl, "rb").read())
                        for tb in tpl_m.get("bodies") or []:
                            if tb.get("typ") == 7 and tb.get("raw"):
                                raw_t = tb["raw"]
                                nv_t = len(tb.get("verts") or [])
                                nt_t = len(tb.get("tris_idx") or [])
                                tpl_hdr = raw_t[:0xF0]
                                bstart = 0xF0 + nv_t * 16 + nt_t * 12
                                tpl_bvh = raw_t[bstart:bstart + 0xC0]
                                break
                    except Exception:
                        tpl_hdr = None
                        tpl_bvh = None
                        tpl_m = None
                seen = {}
                verts, faces = [], []
                for tri in tris:
                    idx3 = []
                    for v in tri:
                        key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                        if key not in seen:
                            seen[key] = len(verts)
                            verts.append(v)
                        idx3.append(seen[key])
                    faces.append(tuple(idx3))
                lim = min(224, max(8, int(self.trimesh_max_tris)))
                if lim != int(self.trimesh_max_tris):
                    self.report({"WARNING"}, "type 7: limit lowered to 224 (server BVH stack)")
                body = DP.build_trimesh(mn, mx, verts, faces, template=tpl_hdr,
                                        bvh_header=tpl_bvh, max_tris=lim)
                if tpl_m is not None:
                    data = DP.build_mdp([body], hsh=tpl_m["hash"],
                                        trailer=tpl_m["trailer"], doff=tpl_m["doff"],
                                        tail=tpl_m["tail"], version=tpl_m["version"],
                                        gap=tpl_m["gap"])
                else:
                    data = DP.build_mdp([body], hsh=hsh, trailer=trailer)
                    self.report({"WARNING"}, "type 7 without a .mdp template: the container and BVH fields are empty")
            elif self.collision_type == "box":
                c = [(mn[i] + mx[i]) * 0.5 for i in range(3)]
                half = [(mx[i] - mn[i]) * 0.5 for i in range(3)]
                body = dict(typ=2, kind=10, pos=tuple(c), half=tuple(half),
                            min=tuple(mn), max=tuple(mx))
                data = DP.build_mdp([body], hsh=hsh, trailer=trailer)
            elif self.per_object:
                bodies = []
                for o in objs:
                    ht = _mdp_hull_tris([o], self.target_tris)
                    if not ht:
                        continue
                    seen_o = {}
                    vs = []
                    for tri in ht:
                        for v in tri:
                            key = (round(v[0], 6), round(v[1], 6), round(v[2], 6))
                            if key not in seen_o:
                                seen_o[key] = len(vs)
                                vs.append(v)
                    if len(vs) < 4:
                        continue
                    omn = [min(v[i] for v in vs) for i in range(3)]
                    omx = [max(v[i] for v in vs) for i in range(3)]
                    bodies.append(dict(min=omn, max=omx, typ=6,
                                       verts=[(v[0], v[1], v[2], 0.0) for v in vs]))
                if not bodies:
                    data = DP.build_from_mesh(mn, mx, tris, hsh=hsh, trailer=trailer)
                else:
                    data = DP.build_mdp(bodies, hsh=hsh, trailer=trailer)
            else:
                data = DP.build_from_mesh(mn, mx, tris, hsh=hsh, trailer=trailer)
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, self.base + ".mdp")
            with open(path, "wb") as fh:
                fh.write(data)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "mdp: %s (%d vertices)" % (path, len(tris)))
        # for type 6 the file holds hull vertices, not triangles
        return {"FINISHED"}




def _check_blender_version():
    "Warn about an unsupported Blender version (the add-on is tested on 5.2)."
    try:
        ver = tuple(bpy.app.version[:3])
    except Exception:
        return
    tested = (5, 2)
    if ver < tested:
        print("[SC_MODEL_IEX] WARNING: Blender %d.%d is below the tested %d.%d — incompatibilities are possible" % (ver[0], ver[1], tested[0], tested[1]))
    else:
        print("[SC_MODEL_IEX] Blender %d.%d — tested version or newer"
              % (ver[0], ver[1]))


class _Progress:
    "Blender progress bar: begin/update/end with exception guards."

    def __init__(self, context, total):
        self.wm = getattr(context, "window_manager", None)
        self.total = max(1, int(total or 1))
        self.value = 0
        try:
            self.wm.progress_begin(0, self.total)
        except Exception:
            self.wm = None

    def step(self, text=_L("")):
        self.value = min(self.total, self.value + 1)
        if self.wm:
            try:
                self.wm.progress_update(self.value)
            except Exception:
                self.wm = None

    def end(self):
        if self.wm:
            try:
                self.wm.progress_end()
            except Exception:
                pass
            self.wm = None

class SC_MODEL_IEX_OT_obj_to_game(bpy.types.Operator):
    """Импорт OBJ-модели в игру: меши, текстуры DXT5, .mdf, .pak"""
    bl_idname = "sc_model_iex.obj_to_game"
    bl_label = "OBJ -> игра (модель)"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(name="OBJ", subtype="FILE_PATH")
    base: bpy.props.StringProperty(name="Имя модели", default="model")
    template_dir: bpy.props.StringProperty(name="Шаблон (папка)", subtype="DIR_PATH",
                                           default=r"E:\SC_Unpacked\models\ships\race_3\l")
    out_dir: bpy.props.StringProperty(name="Куда сохранить", subtype="DIR_PATH")
    target_len: bpy.props.FloatProperty(name="Длина модели", default=5.5, min=0.1)
    install: bpy.props.BoolProperty(name="Установить в игру", default=False)
    game_data: bpy.props.StringProperty(name="data игры", subtype="DIR_PATH", default="")

    def execute(self, context):
        from .sc_tools import obj_pipeline as OP
        out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
        try:
            pak = OP.obj_to_game(
                bpy.path.abspath(self.filepath), self.base,
                bpy.path.abspath(self.template_dir), out,
                target_len=self.target_len,
                install_root=(bpy.path.abspath(self.game_data) if self.install else None),
                log=lambda m: print(m))
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "готово: %s" % pak)
        return {"FINISHED"}


class SC_MODEL_IEX_OT_dds_to_tfh(bpy.types.Operator):
    """DDS -> .tfh/.tfd (формат игры)"""
    bl_idname = "sc_model_iex.dds_to_tfh"
    bl_label = "DDS -> .tfh/.tfd"
    bl_options = {"REGISTER"}

    filepath: bpy.props.StringProperty(name="DDS", subtype="FILE_PATH")
    out_dir: bpy.props.StringProperty(name="Куда", subtype="DIR_PATH")
    cube: bpy.props.BoolProperty(name="Кубическая", default=False)

    def execute(self, context):
        from .sc_tools import texture as TEX
        src = bpy.path.abspath(self.filepath)
        out = bpy.path.abspath(self.out_dir) or os.path.dirname(src)
        try:
            data = open(src, "rb").read()
            if not data[:4] == b"DDS ":
                raise RuntimeError("это не DDS")
            tfh, tfd = TEX.dds_to_pair(data)
            if self.cube:
                import struct as _st
                tfh = bytearray(tfh)
                h1 = _st.unpack_from("<I", tfh, 4)[0] | (1 << 21)
                _st.pack_into("<I", tfh, 4, h1)
                tfh = bytes(tfh)
            base = os.path.splitext(os.path.basename(src))[0]
            open(os.path.join(out, base + ".tfh"), "wb").write(tfh)
            open(os.path.join(out, base + ".tfd"), "wb").write(tfd)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "%s.tfh/.tfd готовы" % base)
        return {"FINISHED"}


class SC_MODEL_IEX_OT_obj_to_msh(bpy.types.Operator):
    """OBJ -> игровые меши (.mdl-msh### + .mdl-hdr)"""
    bl_idname = "sc_model_iex.obj_to_msh"
    bl_label = "OBJ -> игровые меши"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(name="OBJ", subtype="FILE_PATH")
    base: bpy.props.StringProperty(name="Имя модели", default="model")
    template: bpy.props.StringProperty(name="Шаблон .mdl-msh", subtype="FILE_PATH")
    out_dir: bpy.props.StringProperty(name="Куда", subtype="DIR_PATH")

    def execute(self, context):
        import bmesh
        from mathutils import Matrix
        from .sc_tools import export_new as EX
        from .sc_tools import hdrwrite as HW
        try:
            out = bpy.path.abspath(self.out_dir) or bpy.path.abspath("//")
            os.makedirs(out, exist_ok=True)
            before = set(bpy.data.objects)
            bpy.ops.wm.obj_import(filepath=bpy.path.abspath(self.filepath),
                                  forward_axis="NEGATIVE_Y", up_axis="Z")
            new = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
            obj = max(new, key=lambda o: len(o.data.vertices))
            for o in new:
                if o is not obj:
                    bpy.data.objects.remove(o, do_unlink=True)
            me = obj.data
            made = []
            for mi in range(len(me.materials)):
                if not any(p.material_index == mi for p in me.polygons):
                    continue
                dup = bpy.data.objects.new("%s_%02d" % (self.base, len(made)), me.copy())
                bpy.context.scene.collection.objects.link(dup)
                bm = bmesh.new(); bm.from_mesh(dup.data)
                rm = [f for f in bm.faces if f.material_index != mi]
                bmesh.ops.delete(bm, geom=rm, context="FACES")
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
                bm.to_mesh(dup.data); bm.free(); dup.data.update()
                dup["sc_material_index"] = len(made)
                made.append(dup)
            bpy.data.objects.remove(obj, do_unlink=True)
            if not made:
                raise RuntimeError("нет материалов с гранями")
            EX.export_meshes(made, self.base, out, bpy.path.abspath(self.template),
                             scale=1.0, arm=None)
            meshes = sorted(f for f in os.listdir(out) if f.startswith(self.base + ".mdl-msh"))
            tpl_hdr = bpy.path.abspath(self.template).replace(".mdl-msh001", ".mdl-hdr")
            hdr = HW.build_hdr([os.path.join(out, f) for f in meshes],
                               template=tpl_hdr if os.path.exists(tpl_hdr) else None,
                               material_count=len(made), mesh_count=len(meshes))
            open(os.path.join(out, self.base + ".mdl-hdr"), "wb").write(hdr)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "%d мешей + hdr готовы" % len(meshes))
        return {"FINISHED"}


class SC_MODEL_IEX_OT_rotate_selected(bpy.types.Operator):
    """Повернуть выделенное для просмотра; при экспорте поворот отменяется"""
    bl_idname = "sc_model_iex.rotate_selected"
    bl_label = "Повернуть выделенное (визуально)"
    bl_options = {"REGISTER", "UNDO"}

    axis: bpy.props.EnumProperty(
        name="Поворот",
        items=(("x-90", "X -90 (положить на бок)", ""),
               ("x90", "X +90", ""),
               ("z90", "Z +90 (развернуть)", ""),
               ("z-90", "Z -90", ""),
               ("y180", "Y 180 (перевернуть)", "")))
    reset: bpy.props.BoolProperty(name="Сбросить поворот", default=False)

    def execute(self, context):
        import math
        from mathutils import Matrix
        angles = {"x-90": (math.radians(-90), "X"), "x90": (math.radians(90), "X"),
                  "z90": (math.radians(90), "Z"), "z-90": (math.radians(-90), "Z"),
                  "y180": (math.radians(180), "Y")}
        objs = [o for o in context.selected_objects
                if o.type in ("MESH", "ARMATURE", "EMPTY")]
        if not objs:
            self.report({"ERROR"}, "ничего не выделено")
            return {"CANCELLED"}
        if self.reset:
            for o in objs:
                for key in ("sc_orient_inv",):
                    if key in o:
                        del o[key]
            self.report({"INFO"}, "метки поворота сброшены (сам поворот — вручную Ctrl+Z)")
            return {"FINISHED"}
        ang, ax = angles[self.axis]
        R = Matrix.Rotation(ang, 4, ax)
        Ri = R.inverted()
        inv9 = [Ri[r][c] for r in range(3) for c in range(3)]
        for o in objs:
            o.matrix_world = R @ o.matrix_world
            prev = o.get("sc_orient_inv")
            if prev and len(prev) == 9:
                P = Matrix(((prev[0], prev[1], prev[2], 0.0),
                            (prev[3], prev[4], prev[5], 0.0),
                            (prev[6], prev[7], prev[8], 0.0),
                            (0.0, 0.0, 0.0, 1.0)))
                M = Ri @ P
                o["sc_orient_inv"] = [M[r][c] for r in range(3) for c in range(3)]
            else:
                o["sc_orient_inv"] = inv9
        context.view_layer.update()
        self.report({"INFO"}, "повёрнуто %s (при экспорте отменится)" % self.axis)
        return {"FINISHED"}


CLASSES = (SC_MODEL_IEX_OT_import_model, SC_MODEL_IEX_OT_import_batch,
           SC_MODEL_IEX_OT_import_mdp, SC_MODEL_IEX_OT_mdp_scale,
           SC_MODEL_IEX_OT_import_pak, SC_MODEL_IEX_OT_import_anim,
           SC_MODEL_IEX_OT_export_mesh_new, SC_MODEL_IEX_OT_export_skeleton,
           SC_MODEL_IEX_OT_export_animation, SC_MODEL_IEX_OT_export_hdr,
           SC_MODEL_IEX_OT_export_model_file, SC_MODEL_IEX_OT_export_mdp,
           SC_MODEL_IEX_OT_export_mdf, SC_MODEL_IEX_OT_export_texture, SC_MODEL_IEX_OT_assign_lod,
           SC_MODEL_IEX_OT_export_batch,
           SC_MODEL_IEX_OT_validate_model, SC_MODEL_IEX_OT_roundtrip_check,
           SC_MODEL_IEX_OT_export_all,
           SC_MODEL_IEX_OT_export_model, SC_MODEL_IEX_OT_pick_export_dir,
           SC_MODEL_IEX_OT_game_lighting, SC_MODEL_IEX_OT_show_faq,
           SC_MODEL_IEX_OT_reload_tools, SC_MODEL_IEX_Prefs, SC_MODEL_IEX_OT_obj_to_game, SC_MODEL_IEX_OT_dds_to_tfh, SC_MODEL_IEX_OT_obj_to_msh, SC_MODEL_IEX_OT_rotate_selected)
def register():
    _check_blender_version()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    try:
        bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
        try:
            bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
        except Exception:
            pass
    except Exception:
        pass
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()










