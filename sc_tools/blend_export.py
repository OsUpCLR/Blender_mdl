# -*- coding: utf-8 -*-
"""Экспорт из Blender «с нуля»: меш (новая топология), скелет, анимация.

Здесь нет импорта bpy — только преобразование данных, которые оператор
собирает из объектов Blender. Координаты игры: g = (-b.x, -b.z, b.y).
"""
import math

import struct

from . import msh as MSH
from . import sklwrite as SW
import sc_anim


def g_pos(v):
    """Blender (x, y, z) -> игра (x, y, z).

    Импорт переводит вершины базисом M: (x, y, z) -> (-x, -z, y). Обратное
    преобразование — M^-1: (X, Y, Z) -> (-X, Z, -Y). Раньше здесь стояло то же
    M, и круг импорт→экспорт переворачивал Y/Z (модель выходила зеркальной).
    """
    return (-float(v[0]), float(v[2]), -float(v[1]))


def g_nrm(v):
    """Нормаль: то же обратное преобразование, без переноса."""
    return (-float(v[0]), float(v[2]), -float(v[1]))


def g_quat(q):
    """Кватернион Blender (w, x, y, z) -> игра (x, y, z, w)."""
    return (float(q[1]), -float(q[3]), float(q[2]), float(q[0]))


def build_mesh_file(template, positions, triangles, normals=None, uvs=None, bones=None):
    """Собрать .mdl-msh: template — info шаблона (формат/stride), остальное — массивы."""
    return MSH.build_mesh(template, positions, triangles, normals, uvs, bones)


def build_skeleton(bones):
    """bones — список словарей (name, parent, position, quat) в координатах игры."""
    out = []
    for b in bones:
        out.append(SW.make_bone(b["name"], b.get("parent", -1),
                                b["position"], b["quat"]))
    return SW.build_skl({"bones": out, "flags": 0x00010000})


def build_animation(name, fps, channels):
    """channels — [(bone_name, bone_index, keys)], keys = [(t_сек, pos, quat_игра)].

    Времена нормируются на длительность: u16 = t / duration * 32768.
    """
    a = sc_anim.Anim()
    a.version = 5
    a.t0 = 0.0
    duration = 0.0
    for _n, _i, keys in channels:
        if keys:
            duration = max(duration, keys[-1][0])
    a.duration = float(duration)
    for ch_name, ch_index, keys in channels:
        if not keys:
            continue
        n = len(keys)
        times = []
        pos = []
        quat = []
        for t, p, q in keys:
            u = 0 if a.duration <= 0 else int(round(t / a.duration * sc_anim.TIME_UNITS))
            times.append(max(0, min(0xFFFF, u)))
            pos.append(tuple(p))
            quat.append(tuple(q))
        a.channels.append(sc_anim.Channel(ch_name, int(ch_index), n, 0, times, pos, quat))
    return sc_anim.write_anim(a)
