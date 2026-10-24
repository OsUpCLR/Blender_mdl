# -*- coding: utf-8 -*-
"""Запись `.model` и `.skins` (описание модели: меш, скин, физика, клипы)."""


def build_skins(base, default=True):
    lines = []
    if default:
        lines.append("skin default\t\t%s" % base)
    return ("\n".join(lines) + "\n").encode("latin1")


# паттерны, которым движок требует pins + UserParam2 (иначе материал «not bound»)
SHIP_PATTERNS = {
    "ship_hull", "ship_decals", "ship_static_decals",
    "dyn_object_norm", "dyn_object", "dyn_animated_mock",
    "dyn_blank_object", "blank_object",
}


def build_mdf(materials, tex_root=""):
    """Собрать `.mdf` из материалов Blender (минимальный, для новых моделей).

    Имя сэмплера угадывается по суффиксу картинки: _d -> DiffuseSampler,
    _nm -> NormalSampler, _s1 -> ColormapSampler, _glow -> Diffuse2Sampler,
    _sc -> SpecularColorSampler, _pdo -> LightmapSampler.
    """
    suffixes = (
        ("_nm", "NormalSampler"),
        ("_s1", "ColormapSampler"),
        ("_glow", "Diffuse2Sampler"),
        ("_sc", "SpecularColorSampler"),
        ("_pdo", "LightmapSampler"),
        ("_d", "DiffuseSampler"),
    )
    out = []
    for mat in materials or []:
        name = getattr(mat, "name", "material")
        samplers = {}
        try:
            nodes = mat.node_tree.nodes if mat.use_nodes else []
        except Exception:
            nodes = []
        for node in nodes:
            if getattr(node, "type", "") != "TEX_IMAGE" or not node.image:
                continue
            img = node.image
            raw = (img.filepath or img.name or "").replace("/", "\\")
            if raw.lower().endswith((".png", ".tga", ".jpg", ".dds")):
                raw = raw.rsplit(".", 1)[0]
            low = raw.lower()
            sampler = "DiffuseSampler"
            for suf, smp in suffixes:
                if low.endswith(suf):
                    sampler = smp
                    break
            samplers.setdefault(sampler, raw)
        out.append("material %s" % name)
        out.append("{")
        for sampler, path in sorted(samplers.items()):
            out.append('\t%s "%s"' % (sampler, path))
        # паттерны корабля требуют pins/UserParam2, иначе материал "not bound"
        if name.split(".")[0] in SHIP_PATTERNS:
            out.append("\tUserParam2_Float4 ( 1 1 0 0 )")
            out.append("\tpins")
            out.append("\t{")
            out.append("\t\tUser1 1")
            out.append("\t}")
        out.append("}")
        out.append("")
    return ("\n".join(out) + "\n").encode("latin1", "replace")


def build_model(base, anims=None, physics=True, skin=True, include_skins=True,
                with_physics_file=None):
    """Собрать текст `.model`.

    anims — список (имя_клипа, файл_анимации, activity, speed).
    """
    out = []
    out.append("mesh\t\t\t%s" % base)
    if skin:
        out.append("skin default\t%s" % base)
    out.append("physics\t\t\t%s" % (with_physics_file or base))
    if include_skins:
        out.append("#include \t\t\"%s.skins\"" % base)
    out.append("")
    for name, path, activity, speed in (anims or []):
        out.append("anim %s" % name)
        out.append("{")
        if activity:
            out.append("\tactivity\t\t\t%s" % activity)
        out.append("\tfile %s" % path)
        if speed and abs(speed - 1.0) > 1e-6:
            out.append("\tspeed %.4f" % speed)
        out.append("}")
        out.append("")
    return ("\n".join(out) + "\n").encode("latin1")


def rename_base(text, old, new):
    """Заменить имя базы в полях .model, не трогая комментарии.

    Меняются только «кодовые» части строк: mesh/skin/physics/#include, имена
    .anim-файлов. Комментарии (`//`) остаются как были.
    """
    if not old or not new or old == new:
        return text
    if isinstance(text, bytes):
        text = text.decode("latin1")
    import re
    # одно вхождение, по границе слова: «base_landing.anim» меняется один раз,
    # а не превращается в «renamed_renamed_...»
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9])")
    out = []
    for line in text.splitlines(True):
        code, sep, comment = line.partition("//")
        out.append(pattern.sub(new, code) + sep + comment)
    return "".join(out)


def patch_model(text, anims=None, old_base=None, new_base=None):
    """Дописать в .model недостающие anim-блоки, остальное сохранить как есть.

    Нужно для круга импорт→экспорт: исходный .model хранит комментарии, порядок
    и незнакомые токены, которые build_model теряет.
    """
    if isinstance(text, bytes):
        text = text.decode("latin1")
    if old_base and new_base and old_base != new_base:
        text = rename_base(text, old_base, new_base)
    have = set()
    have_files = set()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("anim "):
            have.add(s.split(None, 1)[1].strip())
        elif s.startswith("file "):
            have_files.add(s.split(None, 1)[1].strip())
        for tok in s.replace("{", " ").replace("}", " ").split():
            if tok.lower().endswith(".anim"):
                have_files.add(tok)
    add = []
    for name, path, activity, speed in (anims or []):
        if name in have or path in have_files:
            continue
        add.append("")
        add.append("anim %s" % name)
        add.append("{")
        if activity:
            add.append("\tactivity\t\t\t%s" % activity)
        add.append("\tfile %s" % path)
        if speed and abs(speed - 1.0) > 1e-6:
            add.append("\tspeed %.4f" % speed)
        add.append("}")
    if not add:
        return text.encode("latin1")
    if not text.endswith("\n"):
        text += "\n"
    return (text + "\n".join(add) + "\n").encode("latin1")


def parse_model_lite(text):
    """Грубый разбор для проверок: mesh/skin/physics/anim-имена."""
    res = {"mesh": None, "skin": None, "physics": None, "anims": []}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("mesh"):
            res["mesh"] = s.split()[-1]
        elif s.startswith("skin default"):
            res["skin"] = s.split()[-1]
        elif s.startswith("physics"):
            res["physics"] = s.split()[-1]
        elif s.startswith("anim "):
            res["anims"].append(s.split(None, 1)[1].strip())
    return res
