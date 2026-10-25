# Импорт OBJ-модели в Star Conflict: полный порядок действий

Проверено на двух моделях (Suigintou, вторая модель из `N0R80…zip`).
Тестовая копия: `E:\WorkspaceDSH\Test_SC\star conflict`. Аддон: `E:\SCSDK\Plugins\SC_MODEL_IEX`.

---

## 0. Предпосылки

- модель: `.obj` + `.mtl` + текстуры `.jpg` в одной папке;
- базовая модель игры для шаблонов и скелета: `E:\SC_Unpacked\models\ships\race_3\l\race3_l_t1.*`;
- клиент и сервер остановлены (иначе `.pak` залочен).

---

## 1. Blender: подготовка сцены

1. Импорт OBJ:
   `bpy.ops.wm.obj_import(filepath=obj, forward_axis="NEGATIVE_Y", up_axis="Z")`
   (для моделей, у которых «нос вверх», помогает именно такая пара осей).
2. **Нормализация осей**: если самая длинная ось — Z или X, повернуть на 90°
   (`rotation_euler` + `transform_apply`), чтобы длина легла по **Y**.
3. **Масштаб**: `scale = target_len / size.y`, `transform_apply(scale=True)`.
   Ориентир: Raptor — длина 9.44. Пользователь просил 5.5 (мелкая) и 11.0 (длинная).
4. **Центрирование**: сдвинуть на `-center`, `transform_apply(location=True)`.
5. **Разбивка по материалам**: на каждый материал OBJ — свой объект
   (`me.copy()` + удалить чужие грани), у объекта свой материал.
6. **Ориентация граней**:
   - `bmesh.ops.recalc_face_normals` (нормали наружу);
   - `bmesh.ops.reverse_faces` (под игровое соглашение: у рабочего меша
     Raptor'а знаковый объём **отрицательный**);
   - **двусторонняя геометрия**: `bmesh.ops.duplicate` всех граней + `reverse_faces`
     для копии — иначе односторонние плоскости просвечивают.
7. **Поворот на 180°** по Z (модели в игре развёрнуты) + `transform_apply(rotation=True)`.
8. Каждому объекту задать `obj["sc_material_index"] = i` (аддон пишет его в `+0x00` меша).

## 2. Blender: экспорт аддоном

```python
bpy.ops.sc_model_iex.export_sc(
    base="suigintou", out_dir=OUT,
    template=.../race3_l_t1.mdl-msh001,
    hdr_template=.../race3_l_t1.mdl-hdr,
    write_meshes=True, gen_lod=False, write_model=True, write_mdp=True,
    write_skl=False, write_anim=False, write_mdf=False, pack=False)
```

Получаем: `*.mdl-msh###`, `.mdl-hdr`, `.model`, `.skins`, `.mdp`.

## 3. Текстуры: `.jpg` → `.tfh/.tfd`

- DXT5 (`idx = 14`), мипы до 1×1 (`PIL.Image.resize(..., BOX)`);
- энкодер: `E:\SCSDK\SCToolKit\catalog_plugins\sc.edit.texture-0.1.5\dxt.py`, `encode()`;
- сборка пары: `sc_tools.texture.build_pair(levels, w, h, 14, n_tfh=6,
  h0_extra=ref_h0, h1_extra=ref_h1, already_packed=True)`;
- **уровни хранятся квадратами**, `pack_strip` не нужен;
- **текстуры в родном размере**: UV мешей не масштабировать (у части мешей UV
  выходят за 1 — тайлинг, любое масштабирование ломает раскладку);
- альфа: перед кодированием `convert("RGB").convert("RGBA")` (иначе участки
  могут уйти в прозрачность).

## 4. `.mdf`

Для **каждого** материала — полный блок (иначе движок пишет «not bound»):

```
material ship_hull
{
	ColormapSampler      "models\ships\race_3\l\<маска>"     ; своя чёрная
	Diffuse2Sampler      "models\ships\race_3\l\<текстура>"
	DiffuseSampler       "models\ships\race_3\l\<текстура>"
	NormalSampler        "models\ships\race_3\l\race3_l_t1_nm"
	SpecularColorSampler "models\ships\race_3\l\race3_l_t1_msk_sc"
	UserParam2_Float4 ( 1 1 0 0 )
	pins { User1 1 }
}
```

- имя материала = **шейдерный паттерн**; допустимые: `ship_hull`, `ship_decals`,
  `ship_static_decals`, `dyn_object_norm`, `dyn_glass`, `dyn_animated_mock`,
  `dyn_object`, `dyn_blank_object`, `blank_object`. Выдуманное имя → FATAL.
- повторы одного имени в одном `.mdf` допустимы (проверено на bigship), но
  на практике стабильно работает вариант «все материалы `ship_hull`»;
- пути — только с **обратными** слэшами, без расширений.

## 5. `.mdl-hdr`

- `+0x38` = `(material_count << 16) | mesh_count` — должно совпадать с числом
  мешей и материалов;
- `+0x20` (4×u16 старты LOD) и `+0x28` (дистанции) для одного LOD — **нули**.

## 6. Скелет и анимации

- `.mdl-skl` и `*.anim` копируются от базового корабля с заменой имени базы;
- правки костей (наши): орудия `gun_1..4` на точки корпуса, `gun_5/6` внутрь;
  `engine_1`, `boost_1/2`, `capacitor_1/2` — в кубы-заглушки;
- формат записи кости: имя (32 Б), parent, позиция `+64`, bind-матрица `+144`
  (первые 4 float — перенос, следующие 12 — 3×3), обратный перенос `+208`.

## 7. Сборка и установка

1. Сложить всё в `out3\_pak\models\ships\race_3\l\`;
2. `sc_tools.pak.pack_dir(stage, pak)`;
3. скопировать в `data\models_ships_race_3_l_suigintou.pak`
   (перед этим остановить кластер `Hub/Shard/Lobby/DedicatedServer`);
4. удалить `%LOCALAPPDATA%\Targem\StarConflict\store_cache.dat`;
5. запустить сервер и клиент.

---

## Грабли, на которые уже наступили

| симптом | причина |
|---|---|
| модель не рисуется | `+0x18` в меше не равен `n_idx` (оставался из шаблона) |
| «not bound» | в `.mdf` неполный набор сэмплеров / нет `pins` |
| FATAL `Can't find material pattern` | имя материала не из списка паттернов |
| прозрачные участки | односторонняя геометрия → нужна двусторонняя (дубли граней) |
| «нос вверх» | неверная ось длины → повернуть на 90° до масштабирования |
| полосы на текстуре | уровни в `.tfd` были «полосами»/сбитые offset'ы |
| текстура не находится | прямые слэши в пути, расширение `.tfh` в пути, не квадратная пара |
| часть текстур «не так легла» | UV масштабировали под холст, а у мешей UV > 1 (тайлинг) |
| вывернутые грани | неверное направление обхода; критерий — знаковый объём эталона |

## Правки в аддоне (сделаны)

| файл | что исправлено |
|---|---|
| `sc_tools/msh.py` | `0x18 = n_idx`; индекс материала в `+0x00` |
| `sc_tools/export_new.py` | `import struct`; индекс материала из `sc_material_index` |
| `sc_tools/hdrwrite.py` | нулевые LOD при одном уровне |
| `sc_tools/model_write.py` | авто-`pins`/`UserParam2`; срез `.tfh/.tfd` из путей |
| `__init__.py` | `export_sc` копирует `.mdl-skl` и `*.anim` из шаблона |

Скрипты пайплайна: `_modwork/replace/stage_model2.py` (сцена+экспорт),
`build12_full.py` (текстуры+`.mdf`+`.hdr`+пак+установка).
