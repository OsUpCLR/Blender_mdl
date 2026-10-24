# -*- coding: utf-8 -*-
"""Тесты чистых парсеров (без Blender).

Запуск:  SC_TEST_DATA=the game .pak files python -m unittest discover -s tests -v
Без SC_TEST_DATA тесты помечаются skip (в CI без корпуса это нормально).
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sc_tools import hdrwrite as HW  # noqa: E402
from sc_tools import msh as MSH  # noqa: E402
from sc_tools import skeleton as SK  # noqa: E402
from sc_tools import validate as V  # noqa: E402

DATA = os.environ.get("SC_TEST_DATA", "")


def _find(ext, limit=None):
    if not DATA or not os.path.isdir(DATA):
        return []
    out = []
    for dp, _dn, fs in os.walk(DATA):
        for f in fs:
            if f.endswith(ext):
                out.append(os.path.join(dp, f))
                if limit and len(out) >= limit:
                    return sorted(out)
    return sorted(out)


class TestMsh(unittest.TestCase):
    def test_roundtrip(self):
        files = _find(".mdl-msh000", 20)
        if not files:
            self.skipTest("нет SC_TEST_DATA")
        for path in files:
            info = MSH.parse_file(path)
            rebuilt = MSH.build(info)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), rebuilt, path)


class TestHdr(unittest.TestCase):
    def test_build_matches_template(self):
        files = _find(".mdl-hdr", 10)
        if not files:
            self.skipTest("нет SC_TEST_DATA")
        for path in files:
            folder = os.path.dirname(path)
            stem = os.path.basename(path)[:-len(".mdl-hdr")]
            meshes = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                      if f.startswith(stem + ".mdl-msh")]
            if not meshes:
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            m = HW.parse(raw)
            self.assertEqual(m["mesh_count"], len(meshes), path)
            rebuilt = HW.build_hdr(meshes, path, keep_template_bbox=True)
            self.assertEqual(raw, rebuilt, path)


class TestSkeleton(unittest.TestCase):
    def test_parse(self):
        files = _find(".mdl-skl", 10)
        if not files:
            self.skipTest("нет SC_TEST_DATA")
        for path in files:
            info = SK.parse_skl(path)
            self.assertGreater(info["count"], 0, path)
            self.assertEqual(info["size"], 32 + info["count"] * 224 + info["trailer"], path)


class TestValidate(unittest.TestCase):
    def test_models_have_no_errors(self):
        files = _find(".mdl-hdr", 5)
        if not files:
            self.skipTest("нет SC_TEST_DATA")
        for path in files:
            rows = V.check_model(path[:-len(".mdl-hdr")])
            errors = [t for lvl, t in rows if lvl == "error"]
            self.assertEqual([], errors, path)


if __name__ == "__main__":
    unittest.main()
