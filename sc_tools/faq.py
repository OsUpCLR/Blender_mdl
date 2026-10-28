# -*- coding: utf-8 -*-
"FAQ text on model import/export (an addition to the add-on FAQ_TEXT)."

FAQ_IMPORT_EXPORT = """==========================================================================
MODEL IMPORT AND EXPORT: STEP BY STEP
==========================================================================

MODEL FILES <base>
  .mdl-hdr     header: LOD list and mesh bounds
  .mdl-msh###  meshes (LOD0 is the first range)
  .mdl-skl     skeleton (bones, hierarchy, matrices)
  .mdf         materials: texture samplers and UserParam*
  .mdp         collision (TCF container)
  .model       parameters (fps, speed, parts)
  .anim        animations (one file per animation)
  ship parts (_boost, _capacitor, r4_l_eng) - separate bases in other paks

--------------------------------------------------------------------------
1. IMPORT FROM THE GAME (FOLDER)
--------------------------------------------------------------------------
File > Import > Star Conflict model, choose a .mdl-hdr.
  [x] Animations      - all *.anim as actions
  [x] Skeleton        - create an armature
      Details         - All parts + binding / without binding / model only
      Parts from other paks - Load them (for Phoenix: boosters, capacitor)
      Paint           - Dye by the layer mask (default)
      Brightness      - 1.8

Expected result for a ship:
  - armatures: hull + its own rigs for the parts;
  - actions: cap_anim, capacitor_cap_rot, x_d, x_up, z_l, z_r;
  - cap_anim - on the hull armature (bones cap_anim_jnt*);
  - capacitor_cap_rot - on the capacitor rig;
  - scene frame range = action range (for example 1..49);
  - materials: ship_hull, ship_decals, dyn_glass, dyn_animated_mock.

2. IMPORT FROM .pak
Choose a .pak and specify the path inside the pak, for example
  models\\ships\\race_4\\l\\race4_l_phoenix
The pak is unpacked into %TEMP%\\sc_import_cache\\pak_<name>\\.

--------------------------------------------------------------------------
3. NEW MODEL
--------------------------------------------------------------------------
  - the mesh is triangulated (Ctrl+T), scale 1:1 (a ship is 10-30 units);
  - nose along -Y, up along +Z;
  - materials with textures (their names go into the .mdf);
  - your own skeleton or "Static".

EXPORT: SC: export > Star Conflict
  Model name (base)   name of the <base>.mdl-* files
  Destination         folder or pak
  .mdl-msh template   required for the game (format/structure)
  .mdl-hdr template   the same for the header
  Meshes + .mdl-hdr   geometry
  Auto-LOD            generate LOD1..N
  .model/.skins       model description
  Collision (.mdp)    physics
  Skeleton (.mdl-skl) skeleton
  Animations (.anim)  actions
  .mdf from materials materials
  Build .pak          pack it

COLLISION (Export .mdp):
  Shell (type 6)    - ships, 12 vertices;
  Trimesh (type 7)  - small details only, NO MORE than 224 triangles;
  Box (type 2)      - one box by the bounds;
  Body per object   - for compound_model (several parts).

--------------------------------------------------------------------------
4. EDITING A GAME MODEL AND RETURNING IT
--------------------------------------------------------------------------
  1) import the model (optionally with .mdl-msh/.mdl-hdr templates);
  2) do not change object, bone and material names;
  3) edit: the geometry is triangulated; materials are edited in the nodes;
  4) export with the same base and templates;
  5) put the files into THE SAME pak where the model lives
     (models_objects_part3.pak for statics, models_ships_*.pak for ships);
  6) DO NOT ALLOW duplicates of the same file in two paks - the game will not start
     ("2 or more identical files");
  7) delete %LOCALAPPDATA%\\Targem\\StarConflict\\store_cache.dat;
  8) fully restart the server and the client;
  9) check the model, collision and animations in the game.

Checks: "Validate model" (validate_model) and "Round-trip".
For .mdp: 16*chunkCount + chunkTableOffset == fileSize,
          0x20 + size == chunkTableOffset.

--------------------------------------------------------------------------
5. COLLISION ERRORS
--------------------------------------------------------------------------
No collision model for entity def   - no type 9 record in the .mdp or the file is not in
                                      the model pak; write the .mdp with the plugin.
Only 1 collision shape permited!    - a multi-body .mdp with solid.model;
                                      use one body or compound_model.
Compound object should contains...  - a single body with compound_model;
                                      "Body per object" or solid.model.
body 1 shape is outside the file     - body offsets are not set; build with the plugin.
Server crash (AV)                   - trimesh > 224 triangles; reduce it
                                      or use type 6.

--------------------------------------------------------------------------
6. MATERIALS
--------------------------------------------------------------------------
  _d   albedo (alpha is NOT transparency);
  _s1  masks of the 3 paint layers (R/G/B), A - the crystal mask; do not multiply onto _d;
  _nm  DXT5nm: X in alpha, Y in G, R = AO;
  _sc  specular, alpha = gloss;
  _pdo shading, UV2;
  _msk G=AO, B=gloss;
  dyn_animated_mock - transparent layer (UV animation disabled).

An .mdf saved with a template keeps UserParam*; without a template (build_mdf) the parameters
are lost - tiling and animations will disappear in the game.

--------------------------------------------------------------------------
7. ANIMATIONS
--------------------------------------------------------------------------
  - action = an .anim file, the name = the file name (without the prefix);
  - the skeleton is chosen by bone names: cap_anim -> hull (cap_anim_jnt*),
    capacitor_cap_rot -> the capacitor rig (cap_rot_anim_jnt);
  - x_d/x_up/z_l/z_r are static poses (1 frame), do not assign them as animations;
  - if there are no animations: check the "Animations" checkbox on import.

--------------------------------------------------------------------------
8. FREQUENTLY ASKED QUESTIONS
--------------------------------------------------------------------------
No details (capacitor, boosters)?
  Enable "Parts from other paks: Load them", check game_root.

The animation does not play?
  Scene frame range (1..49 for cap_anim), the action must be on the armature, not on the mesh.

Textures are black/missing?
  Check index_path, unpacked_root, cache_dir; look for
  "texture not found" in SC_MODEL_IEX_log.

Collision only, without a model?
  Export .mdp from the selected objects (type 6 or 7).

Can I put it into a new pak?
  Yes: "Build .pak", then delete the old file from the previous pak.

Where are the path settings?
  Preferences > Add-ons > SC_MODEL_IEX: game_root, unpacked_root, index_path,
  cache_dir.

Blender "ate" the add-on preferences?
  Do not save userpref from a factory session; set the paths and save.

--------------------------------------------------------------------------
9. CHECK-LIST
--------------------------------------------------------------------------
New model:
  [ ] triangulation, scale, orientation
  [ ] materials and textures
  [ ] .mdl-msh/.mdl-hdr templates
  [ ] .mdp type 6 (or 7 <= 224)
  [ ] validate model / round-trip
  [ ] pak built, no duplicates
  [ ] store_cache.dat deleted, server/client restarted

Editing a game model:
  [ ] imported with details and other paks
  [ ] names unchanged
  [ ] the same templates
  [ ] exported with the same base
  [ ] files in the model pak, no duplicates
  [ ] cache deleted, full restart, checked in the game
"""


FAQ_IMPORT_EXPORT_RU = '==========================================================================\nИМПОРТ И ЭКСПОРТ МОДЕЛЕЙ: ПОШАГОВО\n==========================================================================\n\nФАЙЛЫ МОДЕЛИ <base>\n  .mdl-hdr     заголовок: список LOD и границы мешей\n  .mdl-msh###  меши (LOD0 - первый диапазон)\n  .mdl-skl     скелет (кости, иерархия, матрицы)\n  .mdf         материалы: сэмплеры текстур и UserParam*\n  .mdp         коллизия (контейнер TCF)\n  .model       параметры (fps, speed, части)\n  .anim        анимации (файл на анимацию)\n  части корабля (_boost, _capacitor, r4_l_eng) - отдельные базы в других паках\n\n--------------------------------------------------------------------------\n1. ИМПОРТ ИЗ ИГРЫ (ПАПКА)\n--------------------------------------------------------------------------\nFile > Import > Star Conflict model, выбрать .mdl-hdr.\n  [x] Анимации        - все *.anim как действия\n  [x] Скелет          - создать арматуру\n      Детали          - Все части + привязка / без привязки / только модель\n      Части из других паков - Догружать (для Phoenix: бустеры, капсулятор)\n      Покраска        - Краситель по маске слоёв (по умолчанию)\n      Яркость         - 1.8\n\nОжидаемый результат у корабля:\n  - арматуры: корпус + свои риги частей;\n  - действия: cap_anim, capacitor_cap_rot, x_d, x_up, z_l, z_r;\n  - cap_anim - на арматуре корпуса (кости cap_anim_jnt*);\n  - capacitor_cap_rot - на риге капсулятора;\n  - диапазон кадров сцены = диапазон действия (например 1..49);\n  - материалы: ship_hull, ship_decals, dyn_glass, dyn_animated_mock.\n\n2. ИМПОРТ ИЗ .pak\nВыбрать .pak и указать путь внутри пака, например\n  models\\ships\\race_4\\l\\race4_l_phoenix\nПак распакуется в %TEMP%\\sc_import_cache\\pak_<имя>\\.\n\n--------------------------------------------------------------------------\n3. НОВАЯ МОДЕЛЬ\n--------------------------------------------------------------------------\n  - меш триангулирован (Ctrl+T), масштаб 1:1 (корабль 10-30 единиц);\n  - нос по -Y, верх по +Z;\n  - материалы с текстурами (имена попадут в .mdf);\n  - скелет свой или "Статика".\n\nЭКСПОРТ: SC: экспорт > Star Conflict\n  Имя модели (base)   имя файлов <base>.mdl-*\n  Куда                папка или пак\n  Шаблон .mdl-msh     обязателен для игры (формат/структура)\n  Шаблон .mdl-hdr     то же для заголовка\n  Меши + .mdl-hdr     геометрия\n  Авто-LOD            сгенерировать LOD1..N\n  .model/.skins       описание модели\n  Коллизия (.mdp)     физика\n  Скелет (.mdl-skl)   скелет\n  Анимации (.anim)    действия\n  .mdf из материалов  материалы\n  Собрать .pak        упаковать\n\nКОЛЛИЗИЯ (Экспорт .mdp):\n  Оболочка (тип 6)  - корабли, 12 вершин;\n  Тримеш (тип 7)    - только мелкие детали, НЕ БОЛЕЕ 224 треугольников;\n  Коробка (тип 2)   - одна коробка по габаритам;\n  Тело на объект    - для compound_model (несколько частей).\n\n--------------------------------------------------------------------------\n4. ПРАВКА МОДЕЛИ ИЗ ИГРЫ И ВОЗВРАТ\n--------------------------------------------------------------------------\n 1) импорт модели ( можно с шаблонами .mdl-msh/.mdl-hdr );\n 2) не менять имена объектов, костей и материалов;\n 3) правка: геометрия триангулируется; материалы - в нодах;\n 4) экспорт с тем же base и шаблонами;\n 5) файлы положить в ТОТ ЖЕ пак, где лежит модель\n    (models_objects_part3.pak для статики, models_ships_*.pak для кораблей);\n 6) НЕ ДОПУСКАТЬ дубликатов одного файла в двух паках - игра не запустится\n    ("2 или более файлов одинаковые");\n 7) удалить %LOCALAPPDATA%\\Targem\\StarConflict\\store_cache.dat;\n 8) полностью перезапустить сервер и клиент;\n 9) проверить модель, коллизию и анимации в игре.\n\nПроверки: "Проверить модель" (validate_model) и "Round-trip".\nДля .mdp: 16*chunkCount + chunkTableOffset == fileSize,\n          0x20 + size == chunkTableOffset.\n\n--------------------------------------------------------------------------\n5. ОШИБКИ КОЛЛИЗИИ\n--------------------------------------------------------------------------\nNo collision model for entity def   - нет записи type 9 в .mdp или файл не в\n                                      паке модели; писать .mdp плагином.\nOnly 1 collision shape permited!    - многободовый .mdp при solid.model;\n                                      одно тело или compound_model.\nCompound object should contains...  - один body при compound_model;\n                                      "Тело на объект" или solid.model.\nшейп тела 1 за пределами файла      - не выставлены offset тел; собирать плагином.\nПадение сервера (AV)                - тримеш > 224 треугольников; уменьшить\n                                      или использовать тип 6.\n\n--------------------------------------------------------------------------\n6. МАТЕРИАЛЫ\n--------------------------------------------------------------------------\n  _d   альбедо (альфа НЕ прозрачность);\n  _s1  маски 3 слоёв покраски (R/G/B), A - маска кристаллов; на _d не умножать;\n  _nm  DXT5nm: X в альфе, Y в G, R = AO;\n  _sc  спекуляр, альфа = глянец;\n  _pdo затенение, UV2;\n  _msk G=AO, B=глянец;\n  dyn_animated_mock - прозрачный слой (UV-анимация отключена).\n\n.mdf с шаблоном сохраняет UserParam*; без шаблона (build_mdf) параметры\nтеряются - в игре пропадут тайлинг и анимации.\n\n--------------------------------------------------------------------------\n7. АНИМАЦИИ\n--------------------------------------------------------------------------\n  - действие = файл .anim, имя = имя файла (без префикса);\n  - скелет выбирается по именам костей: cap_anim -> корпус (cap_anim_jnt*),\n    capacitor_cap_rot -> риг капсулятора (cap_rot_anim_jnt);\n  - x_d/x_up/z_l/z_r - статические позы (1 кадр), не назначать как анимацию;\n  - если анимаций нет: проверить галочку "Анимации" при импорте.\n\n--------------------------------------------------------------------------\n8. ЧАСТЫЕ ВОПРОСЫ\n--------------------------------------------------------------------------\nНет деталей (капсулятор, бустеры)?\n  Включить "Части из других паков: Догружать", проверить game_root.\n\nАнимация не проигрывается?\n  Диапазон кадров сцены (1..49 для cap_anim), действие на арматуре, не на меше.\n\nТекстуры чёрные/пропали?\n  Проверить index_path, unpacked_root, cache_dir; в SC_MODEL_IEX_log искать\n  "текстура не найдена".\n\nТолько коллизия, без модели?\n  Экспорт .mdp по выделенным объектам (тип 6 или 7).\n\nМожно в новый пак?\n  Да: "Собрать .pak", старый файл из прежнего пака удалить.\n\nГде настройки путей?\n  Preferences > Add-ons > SC_MODEL_IEX: game_root, unpacked_root, index_path,\n  cache_dir.\n\nBlender "съел" настройки аддона?\n  Не сохранять userpref из фабричной сессии; задать пути и сохранить.\n\n--------------------------------------------------------------------------\n9. ЧЕК-ЛИСТ\n--------------------------------------------------------------------------\nНовая модель:\n  [ ] триангуляция, масштаб, ориентация\n  [ ] материалы и текстуры\n  [ ] шаблоны .mdl-msh/.mdl-hdr\n  [ ] .mdp тип 6 (или 7 <= 224)\n  [ ] проверка модели / Round-trip\n  [ ] пак собран, дубликатов нет\n  [ ] store_cache.dat удалён, сервер/клиент перезапущены\n\nПравка из игры:\n  [ ] импорт с деталями и др. паками\n  [ ] имена не изменены\n  [ ] шаблоны те же\n  [ ] экспорт с тем же base\n  [ ] файлы в пак модели, без дубликатов\n  [ ] кэш удалён, полный перезапуск, проверка в игре\n'

FAQ_TEXT_RU = '\nSC Model IEX — частые вопросы\n==========================\n\nУСТАНОВКА\n1. Add-ons -> Install -> папка SC_MODEL_IEX (или zip).\n2. Включить «SC Model IEX».\n3. Настройки аддона: «Корень игры (data)» = ...\\star conflict\\data\n   (там gamedata/def/ex/ship.lua, ship_dyes.lua, ship_armors.lua).\n   «Распакованное дерево» = the game .pak files (extracted automatically) (оттуда берутся текстуры,\n   которых нет россыпью в data, например броня и кристаллы).\n   sc_tools уже встроен в аддон — ничего указывать не нужно.\n\nИМПОРТ\n* Модель: File -> Import -> Star Conflict model (.mdl-hdr).\n* Из пака: File -> Import -> Star Conflict model from .pak.\n* LOD: -1 = все уровни, 0 = самый подробный.\n* Части (_eng/_cap/_boost) собираются галочкой; скелет — отдельной.\n* Покраска: _d — альбедо, _s1 — МАСКИ трёх слоёв (R/G/B), цвет слоя берётся\n  из def-а корабля (default_custom_elements -> ShipDyes.albedo).\n* Детали: список «Детали» — «Все части + привязка к костям» (по умолчанию),\n  «Все части без привязки», «Только эта модель (без частей)». В импорте из пака\n  есть ещё список «Части из других паков»: «Догружать» / «Не догружать».\n* Броня (тёмный металл у деталей): «Паттерн брони (ShipArmors)», например\n  Crossout_Armor02; по умолчанию выключена, потому что в игре паттерн\n  выбирается per-instance и в файлах его нет.\n* Кристаллы Эллидиума: у кораблей Race5 _s1 в DXT5, альфа — маска кристаллов.\n  Аддон пишет в лог «кристаллы: N%» и ставит obj["sc_model_iex_crystals"].\n  ВАЖНО: цвет кристалла НЕ из текстуры (per-instance UserParam4), поэтому\n  визуально кристаллы сейчас рисуются как обычная покраска.\n\nМАТЕРИАЛЫ (как в object.fx)\nДиффуз — _d; _glow — свечение; _nm — DXT5nm (A=X, G=Y, R=AO); _sc — блик,\nальфа = глянец; _pdo — затенение (UV2); _msk — G=AO, B=глянец.\nЦвета красителей переводятся из sRGB в линейное — иначе корпус светлеет,\nа непокрашенные детали остаются чёрными.\n\nОСОБЕННОСТИ BLENDER 5.2\n* Кастомные нормали из файла не задаются — Blender считает свои.\n* _nm/_sc/_pdo грузятся как Non-Color.\n* Сцена НЕ очищается: перед импортом удалите дефолтный куб\n  (select_all -> delete), иначе он попадёт в кадр.\n* Вид «сверху, нос вверх» (Numpad 7): +X — вправо, совпадает с игрой.\n\nЭКСПОРТ\n* Режимы: folder (файлы), new_pak (архив), «дописать/дописать с заменой».\n* Круг импорт -> экспорт без правок: 54/54 файла побайтово.\n* Части из других паков (Phoenix: capacitor в misc.pak, бустеры в\n  models_ships_race_4_l.pak) в пак корпуса НЕ попадают (иначе «2 и более\n  одинаковых копии»). Их правки сохраняются галочкой «Пересобрать паки частей\n  из других паков» (обновляет их пак с .bak), иначе в лог пишется, где деталь.\n* Деплой: закрыть игру -> бэкап исходного .pak -> положить новый пак\n  ПОД ИСХОДНЫМ ИМЕНЕМ -> запуск. Откат — вернуть бэкап.\n\nЧАСТЫЕ ПРОБЛЕМЫ\n* «текстура не найдена» — проверьте «Корень игры» и «Распакованное дерево».\n* Модель тёмная — это покраска/свет; включите «Свет как в игре».\n* В игре правка на другом борту — старое поведение до исправления зеркала;\n  текущая версия зеркалит оси как игра (проверено на _boost).\n* Анимации крутятся наоборот — сообщите, поправим знак кватерниона.\n\nКОЛЛИЗИИ (.mdp)\n* тип 6 (оболочка) — основа статики и все корабли: у кораблей 8–16 вершин,\n  обычно одно тело (solid.model);\n* тип 7 (тримеш) — мелкие детали: медиана 16 треугольников, максимум 224;\n  на крупных объектах даёт «пористую» коллизию;\n* тип 2 (коробка) — редко, строится по габаритам;\n* solid.model требует РОВНО одно тело, compound_model — больше одного\n  (для него есть галочка «Тело на объект»);\n* предел типа 7 — 224 треугольника: 512+ валит сервер физики;\n* .mdp кладётся в тот же пак, где лежит модель; дубликат файла в двух паках\n  даёт «2 или более файлов одинаковые»;\n* контейнер TCF пишется автоматически (таблица чанков типа 9), заголовок\n  шейпа и BVH берутся из шаблона .mdp;\n* тримеш поверхностный (местами односторонний) — для стен и крупных\n  объектов используйте тип 6.\n\nLOD\n* «.mdl-msh###» — это детали модели, а не уровни. У каждого материала обычно\n  несколько версий качества, и LOD k берёт k-ю версию каждого материала.\n* Импорт, поле «LOD»: 0 — самый подробный (все детали), 1..3 — грубее,\n  -1 — все уровни сразу (накладываются). На LOD 0 всегда грузится вся модель:\n  корпус плюс детали _eng/_cap/_boost, у каждой свой LOD0.\n* Экспорт: «LOD: размеры групп» (например 4,4,1,1) пишет +0x20,\n  «Пороги LOD, м» (например 120,60,40) пишет +0x28 как квадраты дистанций,\n  дальний -> ближний; порогов должно быть уровней-1, у одноуровневой модели — нули.\n* Ориентиры: 120/45/15 м — типичные значения в игре; крупные корабли больше\n  (Raptor 120/60/40, Gungnir 300/150/50).\n\nКНОПКА «FAQ» есть здесь и в диалогах импорта/экспорта.\n'
