# Кастомный двигатель в Star Conflict: как сделать, чтобы работали эффекты

Проверено на практике (Suigintou, race3-L). Эффекты держатся **не на модели**, а на
двигательном модуле и костях детали.

---

## 1. Модуль двигателя (def)

Новый модуль наследует штатный и **обязан иметь имя по шаблону**
`ModuleEngine_<Раса><Класс>_<Имя>_Mk1`. Если имя не по шаблону — движок молча не
находит ни звук, ни эффекты (проверено: с `ModuleEngine_Suigintou_Mk1` звука нет,
с `ModuleEngine_Race3_Large_Suigintou_Mk1` — есть).

```lua
-- def/ex/engine.lua
Def.ModuleEngine_Race3_Large_Suigintou_Mk1={}
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.inherit="ModuleEngine_Race3_Large_Mk1"
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.model="models/ships/race_3/l/suigintou_eng"
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.boost_model1="models/ships/race_3/l/suigintou_boost"
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.boost_model_bone1="boost_1"
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.boost_model2="models/ships/race_3/l/suigintou_boost"
Def.ModuleEngine_Race3_Large_Suigintou_Mk1.boost_model_bone2="boost_2"
```

Наследуются (или переопределяются своими путями):

| поле | что делает |
|---|---|
| `engine_effect` | партикл факела двигателя |
| `trail_effect`, `trail_effect_bone="exhaust"` | струя, цепляется к кости `exhaust` |
| `boost_effect` | эффект ускорения |
| `engine_flare_size/angle/color/nebulosity` | параметры вспышки |
| `engine_light_radius/color` | свет двигателя |
| `engine_sound`, `engine_sound_left/right` | гул двигателя |
| `boost_loop_sound`, `boost_stop_sound` | звук буста |

## 2. Модель детали — обязана быть валидной

- `.mdl-msh` + `.mdl-hdr`: 1 меш, 1 материал, `+0x20`/`+0x28` = нули (один LOD);
- `.mdf`: **полный набор сэмплеров** + `UserParam2_Float4 ( 1 1 0 0 )` + `pins { User1 1 }`.
  Без этого материал «not bound» и модуль не инициализируется;
- `.mdl-skl` детали с костями струи:
  - двигатель: `root_eng`, `exhaust`, `exhaust_1`, `exhaust_2`;
  - бустер: `root_boost1`, `exhaust`, `exhaust_1`;
  - конденсатор: своя кость.

Именно к `exhaust*` цепляются факел, струя и источник звука. Нет костей — нет эффектов,
даже при правильном модуле.

## 3. Привязка к кораблю

```lua
-- def/ex/ship.lua
Def.Ship_X.engine_grades = designers.engine_large_<имя>_grades
Def.Ship_X.default_modules[ ai.SpaceShipModuleSlot.ENGINE_MOD_01 ] = "ModuleEngine_Race3_Large_<Имя>_Mk1"
Def.Ship_X.slot_bones[ ai.SpaceShipModuleSlot.ENGINE_MOD_01 ] = "root"
```

Своя таблица грейдов — чтобы не задеть другие корабли класса:

```lua
designers.engine_large_<имя>_grades = {
	{ "ModuleEngine_Race3_Large_<Имя>_Mk1", designers.engine_large_race1_grades[1][2] },
	-- ... Mk2..Mk6 так же
}
```

Конденсатор — по той же схеме (`ModuleCapacitor_Race3_Large_<Имя>_Mk1`,
`capacitor_grades`, `CAPACITOR_MOD_01`).

## 4. Куда цепляется каждый эффект

| эффект | к чему цепляется | где задаётся |
|---|---|---|
| факел двигателя, струя | кость `exhaust` модели двигателя | модуль: `engine_effect`, `trail_effect`, `trail_effect_bone` |
| вспышка и свет | позиция модуля / кость | `engine_flare_*`, `engine_light_*` |
| струя ускорения | кости `boost_1..4` **скелета корабля** | `boost_model_bone1..4`, `boost_effect` |
| звук | кость `exhaust` + имя модуля | `engine_sound*`, `boost_*_sound` |

## 5. Грабли (все проверены на practice)

- **масштаб в `bind`** скелета детали (например ×0.001) ломает инициализацию →
  пропадает звук и эффекты. Скрывать деталь надо геометрией, не скелетом;
- **обнулённые вершины** меша → деталь не грузится, звук пропадает;
- деталь в **отдельном паке**, а её текстуры в родном → деталь не догружается;
- имя модуля не по шаблону → нет звука/эффектов;
- неполный `.mdf` → «not bound», модуль не работает;
- перенос костей `boost_*`/`engine_1` в скелете корабля двигает и детали, и эффекты
  (это удобно, если нужно совместить их с видимыми кубами/маркерами).

## 6. Порядок отладки

1. Сначала модуль + стоковая деталь: убедиться, что факел и звук есть.
2. Подменить `model` на свою деталь с правильным скелетом → эффекты должны остаться.
3. И только потом двигать/масштабировать видимую геометрию.
