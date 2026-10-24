#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools — библиотека (SDK) для ресурсов Star Conflict.

Модули:
    sc_tools.pak       — архивы .pak (TPAK): разбор, распаковка, упаковка
    sc_tools.texture   — текстуры .tfh/.tfd -> DDS
    sc_tools.model     — модели .mdl-msh### -> OBJ (нормали и UV)
    sc_tools.material  — материалы .mdf -> .mtl (привязка текстур)

Быстрый старт:

    from sc_tools import pak, texture, model

    # архив
    info = pak.parse(open(r"D:\\file.pak", "rb").read())
    print(info["count"], "файлов")
    pak.unpack_file(r"D:\\file.pak", r"D:\\out")

    # текстура
    out, info = texture.convert(r"D:\\tex.tfh", r"D:\\dds")

    # модель (с материалами: .mtl + привязка текстур)
    files, s = model.model_to_obj(r"D:\\models\\ship", r"D:\\obj")
"""

__version__ = "0.3.0"
__all__ = ["pak", "texture", "model", "material"]
