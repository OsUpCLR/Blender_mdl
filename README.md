# SC_MODEL_IEX — Star Conflict model tools for Blender

Unofficial Blender add-on for importing and exporting **Star Conflict** assets:
models (`.mdl-hdr`, `.mdl-msh###`), skeletons (`.mdl-skl`), animations (`.anim`),
materials (`.mdf`), textures (`.tfh`/`.tfd`) and collision (`.mdp`).

Works directly with the game formats — no OBJ/FBX conversion round-trip.

* Tested with **Blender 5.2 LTS** (5.2.2). Older versions are not tested.
* Add-on UI: English; if the Blender interface is Russian, the UI switches to
  Russian automatically (restart Blender after changing the language).
* FAQ inside Blender is bilingual (English + Russian): button **FAQ** in the
  import/export dialogs.

## Features

**Import**

* model from a folder (`.mdl-hdr`) or straight from `.pak`;
* all LODs, parts (`_eng`, `_cap`, `_boost`, `_drone`) including parts stored in
  other paks;
* skeleton, rigid binding, per-part rigs (e.g. a capacitor with its own rig);
* all `*.anim` files as Blender actions, routed to the rig by bone names;
* materials from `.mdf`: albedo, paint masks `_s1`, glow, DXT5nm normals,
  specular, `_pdo` occlusion (UV2), `_msk`, decals, default ship paint;
* textures are converted to DDS and packed into the `.blend`; they are searched
  in the game `.pak` files automatically (no index or unpacked folder needed).

**Export**

* `.mdl-msh###` + `.mdl-hdr` (template-based, auto-LOD or manual LOD layout);
* `.model` / `.skins`, `.mdl-skl`, `.anim`, `.mdf`, textures from DDS;
* collision `.mdp`: convex shell (type 6), box (type 2), trimesh + BVH (type 7);
* whole model as a folder or packed `.pak`;
* validation: **Check model** and **Round-trip** operators.

## Installation

### From a release ZIP

1. Download `SC_MODEL_IEX-<version>.zip` from the Releases page.
2. Blender → `Edit → Preferences → Add-ons → Install…` → pick the ZIP
   (do **not** unpack it manually).
3. Enable the `SC_MODEL_IEX` checkbox.

### From source

Copy the repository folder to
`<Blender user folder>/<version>/scripts/addons/SC_MODEL_IEX` and enable it in
Preferences → Add-ons.

## Quick start

Import:

* `File → Import → Star Conflict model (.mdl-hdr)`;
* `File → Import → Star Conflict model from .pak`;
* batch: `SC: import models from folder`.

Export (N-panel **SC** / `File → Export`):

* `SC: export → Star Conflict` — meshes + header, `.model`, collision, skeleton,
  animations, `.mdf`, optional `.pak`;
* separate operators for `.mdp`, `.mdf`, `.tfh/.tfd`, `.anim`, batched export;
* `SC: check model` and `SC: round-trip check`.

### Add-on preferences

* **Game data** — detected automatically for Steam installs (registry +
  `libraryfolders.vdf`); set it manually for non-Steam layouts.
* **Unpacked paks** — optional, kept for legacy layouts. Not required: textures
  are read from the game paks.
* **Texture cache** — defaults to `%TEMP%\sc_import_cache`.

## Collision

| type | use | limit |
|---|---|---|
| 6 — convex shell | ships, most static objects | 482 points (practical: ≤256) |
| 2 — box | simple blockers | — |
| 7 — trimesh + BVH | small details only | **224 triangles** (more crashes the server) |

Ships use a single type-6 body (`solid.model`); multi-body statics use
`compound_model` and the “body per object” option. `.mdp` must live in the **same
pak as the model**; a duplicate file in two paks prevents the game from starting.

## Documentation

* [FAQ.md](FAQ.md) — step-by-step import/export, round-trip, common errors;
* [CHANGELOG.md](CHANGELOG.md) — version history;
* [docs/verification.md](docs/verification.md) — what was verified in game;
* [docs/BUILD.md](docs/BUILD.md) — build and release instructions.

## Building

```bash
python tools/build_zip.py
# -> dist/SC_MODEL_IEX-<version>.zip
```

The ZIP contains a single `SC_MODEL_IEX/` folder that Blender can install
directly. See [docs/BUILD.md](docs/BUILD.md) for tests, version bump and CI.

## Development layout

```
__init__.py            add-on: operators, UI, materials, import/export glue
sc_tools/              format library (no bpy dependency)
  model.py             .mdl-msh parsing, vertex formats
  model_write.py       .mdl-msh/.mdl-hdr writing
  material.py          .mdf parsing/patching
  texture.py           .tfh/.tfd -> DDS
  pak.py               .pak reader/writer
  mdp_write.py         .mdp collision parsing/building
  skeleton.py, sklwrite.py, anim.py
  i18n_ui.py, faq.py   localization and FAQ text
sc_anim.py             animation library wrapper
pak.py                 legacy CLI helpers
tests/                 parser tests (run without Blender)
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Parsers are covered without Blender; Blender-side import/export is verified
manually and via `Round-trip`.

## License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with Targem Games / Star Conflict. Game assets
are not distributed with the add-on; it only reads and writes files from a local
game installation.

---

## Кратко по-русски

Неофициальный аддон Blender для импорта/экспорта моделей Star Conflict:
меши, скелет, анимации, материалы и текстуры, коллизии `.mdp`. Интерфейс
английский, при русском языке Blender переключается на русский; FAQ двуязычный.
Установка — ZIP через `Edit → Preferences → Add-ons → Install…`.
Сборка: `python tools/build_zip.py` → `dist/SC_MODEL_IEX-<версия>.zip`.
Лицензия MIT. Игра и её ассеты не входят в репозиторий.
