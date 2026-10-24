#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Чтение описания модели Star Conflict — текстовые файлы `.model`.

`.model` — точка входа в модель: он связывает меш, физику и анимации и задаёт
параметры поз. Пример (`race1_l_t5_sniper.model`):

    mesh            race1_l_t5_sniper
    //~ //skin default race1_l_t5_sniper
    physics         race1_l_t5_sniper
    //~ #include    "race1_l_t5_sniper.skins"

    /////////////////////// pitch ////////////////////////////

    anim race1_m_pitch
    {
        files
        {
            race1_l_t5_sniper_x_d.anim
            race1_l_t5_sniper_x_up.anim
        }
        poseParameter   MovePitch -90 90
    }

Строки `//` (в том числе `//~`) — комментарии. Блоков `anim` может быть
несколько; в каждом — список файлов и один или несколько `poseParameter`.

Использование:

    from sc_tools import model_def as MD
    md = MD.parse_model_def(r"...\\race1_l_t5_sniper.model")
    print(md["mesh"], [a["name"] for a in md["anims"]])
"""
import os


def parse_model_def(path):
    """Разобрать `.model`. Возвращает словарь с mesh/physics/anims.

    Внутри блока `anim` встречаются: `activity <имя>`, `files { … }` (несколько
    файлов) или `file <имя>` (один файл), `speed <число>`, `poseParameter <имя>
    <мин> <макс>`, `addLayer <имя блока>` и блок звуковых событий `fc { time
    <сек> sound <путь> }`.
    """
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    out = {"path": path, "mesh": None, "physics": None, "anims": [],
           "skins": [], "includes": [], "lods_bounds": [], "raw": text}
    cur = None            # текущий блок anim
    in_files = False
    in_fc = False
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("lodsbounds"):
            # lodsBounds N ( a b c ): движок берёт пороги оттуда
            try:
                body = line[line.index("(") + 1:line.index(")")]
                out["lods_bounds"] = [float(v.replace(",", "."))
                                      for v in body.split()][:4]
            except (ValueError, IndexError):
                pass
            continue
        if line.startswith("#include"):
            out["includes"].append(line[len("#include"):].strip().strip('"'))
            continue
        if line.endswith("{"):
            head = line[:-1].strip().lower()
            if head == "files":
                in_files = True
            elif head == "fc":
                in_fc = True
            continue
        if line == "}":
            if in_files:
                in_files = False
            elif in_fc:
                in_fc = False
            elif cur is not None:
                out["anims"].append(cur)
                cur = None
            continue
        if in_files and cur is not None:
            cur["files"].append(line.strip('"'))
            continue
        if in_fc and cur is not None:
            parts = line.split()
            if parts and parts[0].lower() == "time" and len(parts) >= 2:
                ev = {"time": None, "sound": None}
                try:
                    ev["time"] = float(parts[1])
                except ValueError:
                    pass
                if "sound" in parts:
                    i = parts.index("sound")
                    if i + 1 < len(parts):
                        ev["sound"] = parts[i + 1]
                cur["events"].append(ev)
            continue
        parts = line.split()
        key = parts[0].lower()
        if key == "mesh" and len(parts) > 1:
            out["mesh"] = parts[1]
        elif key == "physics" and len(parts) > 1:
            out["physics"] = parts[1]
        elif key == "skin" and len(parts) > 1:
            out["skins"].append(parts[1])
        elif key == "anim" and len(parts) > 1:
            cur = {"name": parts[1], "files": [], "pose": [], "layers": [],
                   "events": [], "activity": None, "speed": None}
        elif cur is not None and key == "file" and len(parts) > 1:
            cur["files"].append(parts[1].strip('"'))
        elif cur is not None and key == "activity" and len(parts) > 1:
            cur["activity"] = parts[1]
        elif cur is not None and key == "speed" and len(parts) > 1:
            try:
                cur["speed"] = float(parts[1])
            except ValueError:
                pass
        elif cur is not None and key == "addlayer" and len(parts) > 1:
            cur["layers"].append(parts[1])
        elif key == "poseparameter" and cur is not None and len(parts) >= 4:
            try:
                lo, hi = float(parts[2]), float(parts[3])
            except ValueError:
                lo = hi = None
            cur["pose"].append({"name": parts[1], "min": lo, "max": hi})
    if cur is not None:
        out["anims"].append(cur)
    return out


def dump(md):
    """Текстовое представление для CLI."""
    lines = ["описание модели %s" % os.path.basename(md["path"])]
    lines.append("  mesh: %s   physics: %s" % (md["mesh"], md["physics"]))
    if md["includes"]:
        lines.append("  include: %s" % ", ".join(md["includes"]))
    lines.append("  блоков анимаций: %d" % len(md["anims"]))
    for a in md["anims"]:
        extra = []
        if a.get("activity"):
            extra.append("activity %s" % a["activity"])
        if a.get("speed") is not None:
            extra.append("speed %s" % a["speed"])
        if a.get("layers"):
            extra.append("addLayer %s" % ", ".join(a["layers"]))
        if a.get("events"):
            extra.append("события: " + ", ".join(
                "t=%s %s" % (e["time"], e["sound"] or "?") for e in a["events"]))
        pose = ", ".join("%s %s..%s" % (p["name"], p["min"], p["max"]) for p in a["pose"])
        lines.append("    %-14s файлов %d: %s" % (a["name"], len(a["files"]),
                                                 ", ".join(a["files"]) or "—"))
        if pose:
            lines.append("                   поза: %s" % pose)
        if extra:
            lines.append("                   %s" % "; ".join(extra))
    return "\n".join(lines)


def find_model_defs(root):
    """Все `.model` в дереве каталогов."""
    res = []
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".model"):
                res.append(os.path.join(dp, f))
    return sorted(res)
