# Building and releasing SC_MODEL_IEX

## Requirements

* Python 3.9+ (only for the build script and parser tests; the add-on itself runs
  inside Blender).
* Blender **5.2 LTS** for manual testing (older versions are untested).
* No third-party Python packages.

## Build the installable ZIP

```bash
python tools/build_zip.py
```

Output: `dist/SC_MODEL_IEX-<version>.zip`, one top-level `SC_MODEL_IEX/` folder
plus `__init__.py`, `sc_tools/`, `sc_anim.py`, `pak.py`, license and docs.

Options:

```bash
python tools/build_zip.py --out /tmp/release
```

Manual alternative: zip the folder keeping the top-level `SC_MODEL_IEX/` name —
Blender rejects ZIPs whose files sit at the archive root.

## Version bump

1. `__init__.py` → `"version": (0, 4, 3)` (increase the last component, or the
   middle one for a feature release).
2. `ADDON_BUILD` in the same file — short human-readable build line.
3. `CHANGELOG.md` — new section at the top.
4. Rebuild the ZIP; the file name follows `__init__.py` automatically.

## Tests

Parser tests do not need Blender:

```bash
python -m unittest discover -s tests -v
```

Syntax check of every module:

```bash
python -c "import io,os; [compile(io.open(os.path.join(dp,f),encoding='utf-8').read(), f, 'exec') for dp,_,fs in os.walk('.') if '.git' not in dp for f in fs if f.endswith('.py')]"
```

Blender-side smoke test (headless):

```bash
blender -b --factory-startup --python-expr "
import addon_utils, bpy
addon_utils.modules_refresh()
addon_utils.enable('SC_MODEL_IEX')
import SC_MODEL_IEX as P
print(P.bl_info['version'], P.ADDON_BUILD)
"
```

## Install check

1. `Edit → Preferences → Add-ons → Install…` → `dist/SC_MODEL_IEX-<version>.zip`.
2. Enable the add-on, open `File → Import → Star Conflict model` and import a
   `.mdl-hdr` (or a `.pak` with an inner path such as
   `models\ships\race_4\l\race4_l_phoenix`).
3. Check the log text block `SC_MODEL_IEX_log` — no “texture not found” lines for
   a stock ship, materials have textures, animations appear as actions.

## CI

`.github/workflows/ci.yml` runs on every push:

* syntax check of all Python modules;
* `unittest` parser tests;
* build of the ZIP, uploaded as a workflow artifact.

## Release checklist

- [ ] version and `ADDON_BUILD` updated;
- [ ] CHANGELOG section added;
- [ ] parser tests pass;
- [ ] ZIP built; import of a stock ship works in a clean Blender profile;
- [ ] `git tag v<version>` and attach the ZIP to the GitHub release.
