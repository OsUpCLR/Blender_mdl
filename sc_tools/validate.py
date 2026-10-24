# -*- coding: utf-8 -*-
"""Проверки модели и round-trip. Возвращают [(level, text)], без исключений наружу.

level: "ok" | "warn" | "error".
Модуль чистый (без bpy), чтобы его можно было гонять в тестах/CI.
"""
import os
import struct

from . import hdrwrite as HW
from . import material as MAT
from . import mdp_write as DP
from . import skeleton as SK
from . import texture as TEX


def _anim_module():
    try:
        import sc_anim  # лежит в корне аддона
        return sc_anim
    except Exception:
        return None


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _add(rows, level, text):
    rows.append((level, text))


def _mesh_files(folder, stem):
    try:
        return sorted(f for f in os.listdir(folder) if f.startswith(stem + ".mdl-msh"))
    except OSError:
        return []


def check_model(base):
    """Проверить модель по базовому пути (без расширения)."""
    rows = []
    hdr_path = base + ".mdl-hdr"
    if not os.path.exists(hdr_path):
        _add(rows, "error", "нет %s" % os.path.basename(hdr_path))
        return rows
    try:
        raw = _read(hdr_path)
        m = HW.parse(raw)
        starts = HW.lod_starts(raw)
    except Exception as exc:
        _add(rows, "error", "заголовок не читается: %s" % exc)
        return rows

    folder, stem = os.path.dirname(base), os.path.basename(base)
    files = _mesh_files(folder, stem)
    n = len(files)
    _add(rows, "ok", "мешей %d, материалов %d, LOD %s" % (n, m["material_count"], starts))
    if m["mesh_count"] != n:
        _add(rows, "error", "mesh_count %d, а файлов %d" % (m["mesh_count"], n))
    if n and starts and starts[-1] >= n:
        _add(rows, "error", "границы LOD (%s) выходят за число мешей %d" % (starts, n))
    thr = [v for v in struct.unpack_from("<4f", raw, 0x28) if v]
    if len(thr) != len(starts) - 1:
        _add(rows, "warn", "порогов %d, ожидалось %d" % (len(thr), len(starts) - 1))

    mdf = base + ".mdf"
    if os.path.exists(mdf):
        try:
            mats = MAT.parse_mdf(mdf)
            if len(mats) != m["material_count"]:
                _add(rows, "warn", "материалов в .mdf %d, в заголовке %d"
                     % (len(mats), m["material_count"]))
            else:
                _add(rows, "ok", ".mdf: материалов %d" % len(mats))
        except Exception as exc:
            _add(rows, "error", ".mdf не читается: %s" % exc)
    else:
        _add(rows, "warn", "нет .mdf (материалов %d)" % m["material_count"])

    skl = base + ".mdl-skl"
    if os.path.exists(skl):
        try:
            info = SK.parse_skl(skl)
            _add(rows, "ok", "скелет: костей %d" % info["count"])
            jig = [b["name"] for b in info["bones"] if "jiggle" in b["name"].lower()]
            if jig:
                _add(rows, "ok", "jiggle-кости: %s" % ", ".join(jig))
        except Exception as exc:
            _add(rows, "error", "скелет не читается: %s" % exc)

    mdp = base + ".mdp"
    if os.path.exists(mdp):
        try:
            mm = DP.parse_mdp(_read(mdp))
            v6 = sum(b["count"] for b in mm["bodies"] if b.get("typ") == 6)
            t7 = sum(len(b.get("tris_idx") or []) for b in mm["bodies"])
            boxes = sum(1 for b in mm["bodies"] if b.get("typ") == 2)
            _add(rows, "ok", "коллизия: тел %d, вершин %d, треугольников %d, коробок %d%s"
                 % (len(mm["bodies"]), v6, t7, boxes,
                    ", версия 0x%X" % mm["version"]))
        except Exception as exc:
            _add(rows, "error", ".mdp не читается: %s" % exc)

    anim = _anim_module()
    if anim:
        for f in sorted(os.listdir(folder)):
            if f.startswith(stem + "_") and f.endswith(".anim"):
                try:
                    a = anim.read_anim(os.path.join(folder, f))
                    _add(rows, "ok", "%s: каналов %d, %.3f с" % (f, len(a.channels), a.duration))
                except Exception as exc:
                    _add(rows, "error", "%s не читается: %s" % (f, exc))

    for f in sorted(os.listdir(folder)):
        if not f.endswith(".tfh"):
            continue
        pair = os.path.join(folder, f[:-4] + ".tfd")
        if not os.path.exists(pair):
            _add(rows, "warn", "%s без .tfd" % f)
            continue
        try:
            info = TEX.parse_tfh(_read(os.path.join(folder, f)))
            _add(rows, "ok", "%s: %s %dx%d, мипов %d"
                 % (f, info["name"], info["w"], info["h"], info["mips"]))
        except Exception as exc:
            _add(rows, "error", "%s не читается: %s" % (f, exc))
    return rows


def _cmp(rows, a, b, tag):
    if not os.path.exists(a):
        return          # у части баз (детали) файла в источнике нет — не шумим
    if not os.path.exists(b):
        _add(rows, "warn", "%s: нет файла экспорта" % tag)
        return
    da, db = _read(a), _read(b)
    if da == db:
        _add(rows, "ok", "%s: байт-в-байт (%d Б)" % (tag, len(da)))
    else:
        _add(rows, "warn", "%s: отличается (%d vs %d Б)" % (tag, len(da), len(db)))


def roundtrip(source_base, out_dir, out_base=None, kinds=None):
    """Сравнить источник и результат экспорта (по базовому имени)."""
    rows = []
    out_base = out_base or os.path.basename(source_base)
    src_folder, src_stem = os.path.dirname(source_base), os.path.basename(source_base)
    want = set(kinds) if kinds else {"hdr", "model", "mdp", "skl", "mesh", "anim"}
    for ext, kind in ((".mdl-hdr", "hdr"), (".model", "model"),
                      (".mdp", "mdp"), (".mdl-skl", "skl")):
        if kind in want:
            _cmp(rows, source_base + ext, os.path.join(out_dir, out_base + ext), ext)

    src_files = _mesh_files(src_folder, src_stem)
    out_files = _mesh_files(out_dir, out_base)
    # сверяем то, что экспортировали (при экспорте одного LOD мешей меньше,
    # чем в источнике, — это не ошибка)
    same = 0
    for f in out_files:
        idx = f[len(out_base):]
        a = os.path.join(src_folder, src_stem + idx)
        if os.path.exists(a) and _read(a) == _read(os.path.join(out_dir, f)):
            same += 1
    if out_files and "mesh" in want:
        text = "меши: %d/%d байт-в-байт" % (same, len(out_files))
        if len(src_files) != len(out_files):
            text += " (в источнике %d)" % len(src_files)
        _add(rows, "ok" if same == len(out_files) else "warn", text)
    elif src_files:
        _add(rows, "warn", "мешей в экспорте нет (в источнике %d)" % len(src_files))

    anim = _anim_module()
    if anim and "anim" in want:
        for f in sorted(os.listdir(src_folder)):
            if not (f.startswith(src_stem + "_") and f.endswith(".anim")):
                continue
            b = os.path.join(out_dir, out_base + f[len(src_stem):])
            if not os.path.exists(b):
                continue
            try:
                aa = anim.read_anim(os.path.join(src_folder, f))
                bb = anim.read_anim(b)
                ok = abs(aa.duration - bb.duration) < 1e-6 and len(aa.channels) == len(bb.channels)
                _add(rows, "ok" if ok else "warn",
                     "%s: длительность/каналы %s" % (f, "совпадают" if ok else "расходятся"))
            except Exception as exc:
                _add(rows, "error", "%s не читается: %s" % (f, exc))
    return rows


def format_rows(rows):
    """Текст отчёта: [OK]/[WARN]/[ERR]."""
    tags = {"ok": "OK  ", "warn": "WARN", "error": "ERR "}
    return "\n".join("[%s] %s" % (tags.get(lvl, lvl), text) for lvl, text in rows)


def summarize(rows):
    """(ok, warn, error) — для короткого сообщения в операторе."""
    return (sum(1 for l, _t in rows if l == "ok"),
            sum(1 for l, _t in rows if l == "warn"),
            sum(1 for l, _t in rows if l == "error"))
