#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чтение анимаций Star Conflict — файлы `.anim` («TCF ANIM»).

Модуль-обёртка: реальный разбор формата живёт в `SC_MODEL_IEX\\sc_anim.py`
.
Здесь сохранён прежний простой API, которым пользуются `sc_anim_tool.py`
и старые скрипты:

    from sc_tools import anim as AN
    a = AN.parse_anim(r"...\\race1_l_t5_sniper_land.anim")
    print(a["duration"], [n["name"] for n in a["nodes"]])
    for node in a["nodes"]:
        print(node["name"], AN.node_keys(a, node)[:2])

Длительность берётся из `duration` (+0x04), а не из `t0` (+0x00); описатели каналов ищутся по истинной раскладке чанка (`sc_anim.read_anim`).
"""
import importlib.util
import os
import struct

MAGIC = b"TCF ANIM"
HEADER = 80
NODE = 48
KEY = 32
TIME_MAX = 32768.0

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sc_anim():
    path = os.path.join(_ROOT, "sc_anim.py")
    spec = importlib.util.spec_from_file_location("sc_model_iex_sc_anim", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_sa = None


def _api():
    global _sa
    if _sa is None:
        _sa = _sc_anim()
    return _sa


def _to_info(a, path=None):
    nodes, times = [], []
    for i, ch in enumerate(a.channels):
        nodes.append(dict(index=i, name=ch.name, bone=ch.index, keys=ch.count,
                          type=ch.type, flag=ch.type, offset=None))
        times.append(a.seconds(ch))
    return dict(path=path, version=a.version, t0=a.t0, duration=a.duration,
                mask=list(getattr(a, "mask", [0, 0, 0, 0])),
                flags=(a.mask[0] if getattr(a, "mask", None) else 0), flags2=0,
                node_count=len(nodes), nodes=nodes, times=times,
                size=len(a.raw or b""), channels=a.channels, _anim=a)


def parse_anim_bytes(data):
    """Разобрать содержимое `.anim` (bytes)."""
    a = _api().read_anim(bytes(data))
    return _to_info(a)


def parse_anim(path):
    """Прочитать `.anim` с диска."""
    a = _api().read_anim(path)
    info = _to_info(a, path)
    info["_data"] = a.raw
    return info


def node_keys(info, node):
    """Ключи узла: список (время_сек, позиция, кватернион)."""
    chans = info.get("channels") or []
    i = node.get("index", 0)
    if i >= len(chans):
        return []
    ch = chans[i]
    a = info.get("_anim")
    ts = ch.seconds(a.duration) if a is not None else []
    out = []
    for k in range(ch.count):
        t = ts[k] if k < len(ts) else 0.0
        p = ch.pos[k] if k < len(ch.pos) else (0.0, 0.0, 0.0)
        q = ch.quat[k] if k < len(ch.quat) else (0.0, 0.0, 0.0, 1.0)
        out.append((t, tuple(p), tuple(q)))
    return out


def dump(info, keys=True):
    """Текстовое представление для CLI."""
    lines = ["анимация %s: версия %d, узлов %d, длительность %.4f с, размер %d байт"
             % (os.path.basename(info.get("path") or ""), info["version"],
                info["node_count"], info["duration"], info["size"])]
    lines.append("  t0=%.4f  маска %s" % (info.get("t0", 0.0), info.get("mask")))
    for n in info["nodes"]:
        ts = info["times"][n["index"]] if n["index"] < len(info["times"]) else []
        trange = ("%.3f..%.3f" % (ts[0], ts[-1])) if ts else "—"
        lines.append("    %-12s кость %-3d ключей %-3d тип %d  времена %s"
                     % (n["name"], n["bone"], n["keys"], n.get("type", 0), trange))
        if keys:
            for t, p, q in node_keys(info, n)[:2]:
                lines.append("        t=%7.4f поз (%7.4f %7.4f %7.4f) q (%6.3f %6.3f %6.3f %6.3f)"
                             % (t, p[0], p[1], p[2], q[0], q[1], q[2], q[3]))
    return "\n".join(lines)


def find_animations(root):
    """Все `.anim` в дереве каталогов."""
    res = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".anim"):
                res.append(os.path.join(dp, f))
    return sorted(res)
