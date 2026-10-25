#!/usr/bin/env python3
"""Build a Blender-installable ZIP for SC_MODEL_IEX.

Usage:
    python tools/build_zip.py [--out DIR]

Output: dist/SC_MODEL_IEX-<version>.zip with a single SC_MODEL_IEX/ folder.
"""
import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = "SC_MODEL_IEX"

INCLUDE_FILES = ("__init__.py", "sc_anim.py", "pak.py", "LICENSE",
                 "README.md", "CHANGELOG.md", "FAQ.md")
INCLUDE_DIRS = ("sc_tools", "docs")
EXCLUDE_DIRS = ("__pycache__", "tests", "dist", "build", ".git")
EXCLUDE_EXT = (".pyc", ".pyo")


def version():
    src = open(os.path.join(ROOT, "__init__.py"), encoding="utf-8").read()
    m = re.search(r'"version"\s*:\s*\((\d+),\s*(\d+),\s*(\d+)\)', src)
    if not m:
        raise SystemExit("version not found in __init__.py")
    return ".".join(m.groups())


def collect():
    paths = []
    for name in INCLUDE_FILES:
        p = os.path.join(ROOT, name)
        if os.path.isfile(p):
            paths.append(p)
    for d in INCLUDE_DIRS:
        base = os.path.join(ROOT, d)
        for dp, dn, fs in os.walk(base):
            dn[:] = [x for x in dn if x not in EXCLUDE_DIRS]
            for f in sorted(fs):
                if f.endswith(EXCLUDE_EXT):
                    continue
                paths.append(os.path.join(dp, f))
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    ver = version()
    out = os.path.join(args.out, "%s-%s.zip" % (ADDON, ver))
    files = collect()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            z.write(p, ADDON + "/" + rel)
    size = os.path.getsize(out)
    print("built %s (%d files, %.1f KiB)" % (out, len(files), size / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
