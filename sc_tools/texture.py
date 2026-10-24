#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.texture — Star Conflict .tfh/.tfd  ->  .dds

Формат восстановлен по декомпиляции game.exe (Ghidra, FUN_1408aa8c0 —
загрузчик текстур, вызывает D3DXCreateTexture/D3DXCreateCubeTexture).

Заголовок .tfh (не шифр, не ключ — обычный заголовок, little-endian):
    hdr0 = u32 @0x00 ; hdr1 = u32 @0x04
    width  = (hdr0 >> 4) & 0x3FFF
    height =  hdr0 >> 18
    mips   =  hdr1 & 0x0F
    fmtidx = (hdr1 >> 8) & 0x1F          -> таблица форматов
    cube   = (hdr1 >> 0x15) & 1
    A      = u16 @0x08                   -> с этого оффсета в .tfh лежат младшие мипы

.tfd — «сырые» пиксельные данные базового уровня (для cube-текстур — все 6 граней
подряд). Признаков шифрования нет: размеры точно совпадают с dim*dim*bpp либо с
суммой мип-цепочки.
"""
import struct, os

FORMATS = {
    0x00: ("A8",        None, 1, False),
    0x01: ("L8",        None, 1, False),
    0x02: ("A8L8",      None, 2, False),
    0x03: ("R5G6B5",    None, 2, False),
    0x04: ("A1R5G5B5",  None, 2, False),
    0x05: ("A8R8G8B8",  None, 4, False),
    0x06: ("A8R8G8B8",  None, 4, False),
    0x07: ("DXT1", "DXT1", 8, True),
    0x08: ("DXT1", "DXT1", 8, True),
    0x09: ("DXT3", "DXT3", 16, True),
    0x0A: ("DXT5", "DXT5", 16, True),
    0x0B: ("DXT1", "DXT1", 8, True),
    0x0C: ("DXT1", "DXT1", 8, True),
    0x0D: ("DXT3", "DXT3", 16, True),
    0x0E: ("DXT5", "DXT5", 16, True),
    0x0F: ("ABGR16",    None, 8, False),
    0x10: ("D3DFMT_71", None, 4, False),
    0x14: ("R8G8B8",    None, 3, False),
    0x15: ("A8R8G8B8",  None, 4, False),
    0x16: ("X8R8G8B8",  None, 4, False),
    0x17: ("R5G6B5",    None, 2, False),
    0x18: ("X1R5G5B5",  None, 2, False),
    0x19: ("A1R5G5B5",  None, 2, False),
    0x1A: ("A4R4G4B4",  None, 2, False),
    0x1C: ("A8",        None, 1, False),
    0x24: ("ABGR16",    None, 8, False),
    0x32: ("L8",        None, 1, False),
    0x33: ("A8L8",      None, 2, False),
}


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def fmt_info(idx):
    return FORMATS.get(idx, ("DXT5", "DXT5", 16, True))


def level_bytes(w, h, unit, is_block):
    if is_block:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * unit
    return w * h * unit


def levels_of(w, h, mips, unit, is_block):
    out = []
    for i in range(max(1, mips)):
        lw = max(1, w >> i)
        lh = max(1, h >> i)
        out.append((lw, lh, level_bytes(lw, lh, unit, is_block)))
    return out


def parse_tfh(b):
    """Разбор заголовка .tfh -> dict с параметрами текстуры."""
    if len(b) < 16:
        raise ValueError("tfh меньше 16 байт")
    h0, h1 = u32(b, 0), u32(b, 4)
    w = (h0 >> 4) & 0x3FFF
    h = h0 >> 18
    mips = h1 & 0xF
    idx = (h1 >> 8) & 0x1F
    A = u16(b, 8)
    name, fourcc, unit, is_block = fmt_info(idx)
    return dict(h0=h0, h1=h1, w=w, h=h, mips=max(1, mips), idx=idx, A=A,
                name=name, fourcc=fourcc, unit=unit, is_block=is_block,
                cube=bool((h1 >> 0x15) & 1), size=len(b))


def unpack_strip(data, base_w, w, h, unit, is_block):
    """
    Переставить мип-уровень из «полосы» движка в нормальный квадрат.

    ВАЖНО: .tfd хранит уровни НЕ квадратами, а полосами шириной, равной
    ширине базового уровня. Например для 2048x2048 мип 1024x1024 лежит как
    полоса 2048x512, нарезанная на k=2 сегмента 1024x512, которые надо
    поставить стопкой друг под другом.
    Квадраты 0..3 байт на блок, переставляем на уровне блоков DXT.
    """
    if not w or not h or not base_w or base_w % w:
        return data
    strip_h = (w * h) // base_w
    k = base_w // w
    if k <= 1 or strip_h == 0 or base_w % w:
        return data
    bw = 4 if is_block else 1
    bsize = unit
    bw_l = max(1, (w + bw - 1) // bw)            # блоков в строке уровня
    bh_seg = max(1, (strip_h + bw - 1) // bw)    # блоков по высоте сегмента
    bw_s = max(1, (base_w + bw - 1) // bw)       # блоков в строке полосы
    need = bw_s * bh_seg * k * bsize
    if need > len(data) or len(data) < bw_l * bh_seg * k * bsize:
        return data
    out = bytearray(bw_l * bh_seg * k * bsize)
    for r in range(bh_seg):
        for s in range(k):
            for c in range(bw_l):
                src = (r * bw_s + s * bw_l + c) * bsize
                dst = ((s * bh_seg + r) * bw_l + c) * bsize
                if src + bsize <= len(data) and dst + bsize <= len(out):
                    out[dst:dst + bsize] = data[src:src + bsize]
    return bytes(out)


def verify_level_table(info, tfh_bytes):
    """
    Проверка целостности таблицы уровней по ТРЕТЬЕМУ полю записи.

    Третье поле — шаг строки уровня в байтах (pitch). Для блочных форматов
    pitch = (L/4) * размер_блока, с минимумом в один блок:
        L=8    ->  32
        L=64   -> 256
        L=2048 -> 8192   (для DXT3/DXT5)
        L=1,2,4 -> 16    (минимум один блок)

    Возвращает (ok, problems) — список текстовых расхождений.
    """
    table = parse_level_table(info, tfh_bytes)
    if not table:
        return False, ["таблица не читается"]
    problems = []
    n = len(table)
    unit = info["unit"]
    for i in range(n):
        o3 = 8 + i * 12
        try:
            third = struct.unpack_from("<3I", tfh_bytes, o3)[2]
        except Exception:
            problems.append("запись %d: не читается" % i)
            continue
        lvl = n - 1 - i
        L = max(1, info["w"] >> lvl)
        if info["is_block"]:
            pitch = max(unit, ((L + 3) // 4) * unit)
        else:
            # Для неблочных форматов шаг строки = L * байт на пиксель.
            pitch = max(1, L * unit)
        if third != pitch:
            problems.append("запись %d (L=%d): pitch=%d, ожидалось %d" % (i, L, third, pitch))
    return (not problems), problems


def parse_level_table(info, tfh_bytes):
    """
    Таблица уровней в .tfh: записи по 12 байт начиная со смещения 8
    (сразу после двух u32 заголовка), по одной на уровень.
    Запись = (offset, size, pitch). Уровни идут от МЛАДШЕГО к БАЗОВОМУ!

    Пример race1_l_t5_sniper_d (DXT3 2048x2048, 12 уровней):
        0: offset=0x98     size=16        (1x1)      — в .tfh
        1: 0xA8            16
        2: 0xB8            16
        3: 0xC8            64            (8x8)
        4: 0x108           256
        5: 0x208           1024          (32x32)
        6: 0x0             4096          (64x64)    — дальше в .tfd
        7: 0x1000          16384
        8: 0x5000          65536
        9: 0x15000         262144
       10: 0x55000         1048576
       11: 0x155000        4194304       (2048x2048, БАЗОВЫЙ — В КОНЦЕ .tfd)
    Сумма размеров = len(.tfd) + (len(.tfh) - A). Возвращает [(offset, size)] или None.
    """
    n = info["mips"]
    out = []
    for i in range(n):
        o = 8 + i * 12
        if o + 12 > len(tfh_bytes):
            return None
        off, size, _third = struct.unpack_from("<3I", tfh_bytes, o)
        out.append((off, size))
    return out


def _level_data(off, size, tfh_bytes, tfd_bytes, a_off=0):
    """Данные уровня.

    Уровни лежат в .tfh, начиная со смещения A (= offset самого младшего
    уровня), либо в .tfd. Смещения в таблице локальные для своего файла,
    поэтому одного условия «влезает в .tfh» мало: запись .tfd с маленьким
    offset тоже может туда «влезть». Поэтому в .tfh читаем только то, что
    лежит не ниже A. Условие оставлено как защита.
    """
    if off >= a_off and off + size <= len(tfh_bytes):
        return tfh_bytes[off:off + size]
    if off + size <= len(tfd_bytes):
        return tfd_bytes[off:off + size]
    return None


def build_chain(info, tfh_bytes, tfd_bytes):
    """
    Полная мип-цепочка.

    Раскладка берётся из таблицы уровней в .tfh (parse_level_table):
    младшие уровни лежат в .tfh и в НАЧАЛЕ .tfd, а БАЗОВЫЙ УРОВЕНЬ — В КОНЦЕ
    .tfd. Если положить базовый уровень в начало, картинка получается с «гребёнкой».
    Возвращает список (w, h, data) от базового к младшим — как в DDS.
    """
    faces = 6 if info["cube"] else 1
    unit, is_block = info["unit"], info["is_block"]
    lv = levels_of(info["w"], info["h"], info["mips"], unit, is_block)
    table = parse_level_table(info, tfh_bytes)
    out = []
    if table:
        entries = list(reversed(table))          # от базового к младшим
        for idx, (w, h, size) in enumerate(lv):
            data = None
            if idx < len(entries):
                off, tsize = entries[idx]
                raw = _level_data(off, tsize, tfh_bytes, tfd_bytes, info["A"])
                if raw is not None and len(raw) >= size:
                    data = raw[:size]
            if data is None:
                data = b"\0" * size
            out.append((w, h, data))
        return out, len(tfd_bytes) + max(0, len(tfh_bytes) - info["A"])

    # запасной путь, если таблицы нет
    A = info["A"]
    tail = tfh_bytes[A:] if 0 < A <= len(tfh_bytes) else b""
    tp = fp = 0
    for face in range(faces):
        for w, h, size in lv:
            if fp + size <= len(tfd_bytes):
                data = tfd_bytes[fp:fp + size]
                fp += size
            else:
                data = tail[tp:tp + size]
                tp += size
            if len(data) < size:
                data = data + b"\0" * (size - len(data))
            out.append((w, h, data))
    return out, len(tfd_bytes) + len(tail)


def dds_header(w, h, fourcc=None, rgb_bits=32, mipmaps=1, alphapixels=True,
               rm=0x00FF0000, gm=0x0000FF00, bm=0x000000FF, am=0xFF000000,
               cube=False):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if mipmaps > 1 else 0)
    if fourcc:
        pf_flags = 0x4
        pf_fourcc = int.from_bytes(fourcc.encode("latin1")[:4].ljust(4, b"\0"), "little")
        masks = (0, 0, 0, 0)
    else:
        pf_flags = 0x40 | (0x1 if alphapixels else 0)
        pf_fourcc = 0
        masks = (rm, gm, bm, am if alphapixels else 0)
    caps2 = 0x0000FE00 if cube else 0
    caps = 0x1000 | (0x8 if cube else 0) | (0x400000 if mipmaps > 1 else 0)
    # DDS_HEADER: 31 x u32 (после магии "DDS "), всего 124 байта
    fields = [
        124, flags, h, w, 0, 0, mipmaps,                      # размер, флаги, h, w, pitch, depth, mips
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,                      # reserved1[11]
        32, pf_flags, pf_fourcc, rgb_bits,                    # DDS_PIXELFORMAT
        masks[0], masks[1], masks[2], masks[3],               # маски каналов
        caps, caps2, 0, 0, 0,                                 # caps, caps2, caps3, caps4, reserved2
    ]
    return b"DDS " + struct.pack("<31I", *fields)


def make_header(info):
    return dds_header(info["w"], info["h"], fourcc=info["fourcc"],
                      rgb_bits=info["unit"] * 8 if not info["fourcc"] else 0,
                      mipmaps=info["mips"], cube=info["cube"])


def load_pair(path):
    """Принимает путь к .tfh или .tfd, возвращает (info, tfh_bytes, tfd_bytes)."""
    p = str(path)
    base = p[:-4] if p.lower().endswith((".tfh", ".tfd")) else p
    hp, dp = base + ".tfh", base + ".tfd"
    if not os.path.exists(hp):
        raise FileNotFoundError("нет .tfh: %s" % hp)
    if not os.path.exists(dp):
        raise FileNotFoundError("нет .tfd: %s" % dp)
    tfh = open(hp, "rb").read()
    tfd = open(dp, "rb").read()
    return parse_tfh(tfh), tfh, tfd


def convert(path, out_dir, fmt="dds", mips=True):
    """
    Конвертирует пару .tfh/.tfd в DDS. Возвращает (путь_результата, инфо).

    mips=True (по умолчанию) — писать ПОЛНУЮ мип-цепочку.
    Раскладка разгадана: в .tfh со смещения 8 лежит таблица уровней по 12 байт
    (offset, size, pitch), по одной записи на уровень, порядок — от младшего
    (1x1) к базовому; младшие уровни лежат в .tfh, остальные и БАЗОВЫЙ —
    в .tfd, причём базовый уровень стоит в КОНЦЕ .tfd.

    mips=False — записать только базовый уровень (mipmaps=1). Нужно, если
    требуются файлы минимального размера: у 2048x2048 DXT3 это 4.19 МБ вместо
    5.59 МБ. Картинка в обоих случаях корректна.
    """
    info, tfh, tfd = load_pair(path)
    chain, used = build_chain(info, tfh, tfd)
    if not mips:
        chain = chain[:1]
        info = dict(info)
        info["mips"] = 1
    blob = bytearray(make_header(info))
    for w, h, data in chain:
        blob += data
    base = os.path.basename(str(path))
    base = base[:-4] if base.lower().endswith((".tfh", ".tfd")) else base
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, base + ".dds")
    with open(out, "wb") as fh:
        fh.write(blob)
    info = dict(info)
    info["used"] = used
    info["out"] = out
    info["mips"] = len(chain)
    return out, info


def find_pairs(root):
    """Все пары .tfh/.tfd в дереве каталогов."""
    res = []
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith(".tfh"):
                res.append(os.path.join(dp, f))
    return sorted(res)


# --------------------------------------------------------------------------
# Запись .tfh/.tfd (обратная к разбору выше)
# --------------------------------------------------------------------------

def pack_strip(data, base_w, w, h, unit, is_block):
    """
    Обратная к unpack_strip: квадрат -> «полоса», как это хранит движок.

    Игра держит мипы не квадратами, а полосами шириной базового уровня:
    уровень w x h разрезан на k = base_w / w сегментов, которые лежат рядом
    по горизонтали. Обратное преобразование возвращает ровно эту раскладку.
    """
    if not w or not h or not base_w or base_w % w:
        return data
    strip_h = (w * h) // base_w
    k = base_w // w
    if k <= 1 or strip_h == 0 or base_w % w:
        return data
    bw = 4 if is_block else 1
    bsize = unit
    bw_l = max(1, (w + bw - 1) // bw)
    bh_seg = max(1, (strip_h + bw - 1) // bw)
    bw_s = max(1, (base_w + bw - 1) // bw)
    need = bw_l * bh_seg * k * bsize
    if len(data) < need:
        return data
    # Размер полосы = base_w × strip_h блоков: bw_s * bh_seg.
    out = bytearray(bw_s * bh_seg * bsize)
    for r in range(bh_seg):
        for s in range(k):
            for c in range(bw_l):
                src = ((s * bh_seg + r) * bw_l + c) * bsize
                dst = (r * bw_s + s * bw_l + c) * bsize
                if src + bsize <= len(data) and dst + bsize <= len(out):
                    out[dst:dst + bsize] = data[src:src + bsize]
    return bytes(out)


def build_pair(levels, w, h, idx, cube=False, n_tfh=None, h0_extra=0, h1_extra=0,
               already_packed=False):
    """
    Собрать пару .tfh/.tfd из мип-цепочки.

    levels  — [(lw, lh, data)] от БАЗОВОГО к младшему.
    already_packed — data уже в «полосной» раскладке движка (так их отдаёт
              build_chain). Если False, данные считаются квадратными и
              упаковываются pack_strip.
    n_tfh   — сколько САМЫХ МЛАДШИХ уровней положить в .tfh (остальные в .tfd).
    h0_extra, h1_extra — служебные биты заголовка из оригинала (движок хранит
              там флаги; без них заголовок не совпадёт).
    Возвращает (tfh_bytes, tfd_bytes).
    """
    mips = len(levels)
    unit = fmt_info(idx)[2]
    is_block = fmt_info(idx)[3]
    base_w = levels[0][0] if levels else w
    # уровни в порядке «младший -> базовый», как в таблице .tfh
    asc = list(reversed(levels))
    if n_tfh is None:
        n_tfh = sum(1 for _lw, _lh, d in asc if len(d) < 4096)
    n_tfh = max(0, min(mips, n_tfh))

    head_size = 8 + mips * 12
    tfh = bytearray(head_size)
    tfd = bytearray()
    entries = []
    # сначала младшие уровни в .tfh, затем остальные в .tfd (базовый — последним)
    for i, (lw, lh, data) in enumerate(asc):
        packed = data if already_packed else pack_strip(data, base_w, lw, lh, unit, is_block)
        pitch = (max(unit, ((max(1, lw) + 3) // 4) * unit) if is_block
                 else max(1, max(1, lw) * unit))
        size = len(packed)
        if i < n_tfh:
            off = len(tfh)
            tfh += packed
        else:
            off = len(tfd)
            tfd += packed
        entries.append((off, size, pitch))

    mask0 = (0x3FFF << 4) | (0x3FFF << 18)
    mask1 = 0xF | (0x1F << 8) | (1 << 0x15)
    h0 = (((w & 0x3FFF) << 4) | ((h & 0x3FFF) << 18)) | (h0_extra & ~mask0 & 0xFFFFFFFF)
    h1 = ((mips & 0xF) | ((idx & 0x1F) << 8) | ((1 if cube else 0) << 0x15)) | \
        (h1_extra & ~mask1 & 0xFFFFFFFF)
    struct.pack_into("<2I", tfh, 0, h0, h1)
    for i, e in enumerate(entries):
        struct.pack_into("<3I", tfh, 8 + i * 12, *e)
    return bytes(tfh), bytes(tfd)


DDS_IDX = {"DXT1": 11, "DXT3": 13, "DXT5": 10}


def dds_to_pair(data, idx=None, n_tfh=None):
    """DDS -> (.tfh, .tfd). Поддерживаются DXT1/3/5 и несжатые A8/L8/A8L8/32-бит.

    idx — код формата в .tfh; по умолчанию самый частый в корпусе
    (DXT1 -> 11, DXT3 -> 13, DXT5 -> 10, A8R8G8B8 -> 5, A8 -> 0, L8 -> 1, A8L8 -> 2).
    """
    if data[:4] != b"DDS ":
        raise ValueError("это не DDS")
    head = data[4:4 + 124]
    height = struct.unpack_from("<I", head, 8)[0]
    width = struct.unpack_from("<I", head, 12)[0]
    mips = struct.unpack_from("<I", head, 24)[0]
    pf_flags = struct.unpack_from("<I", head, 76)[0]
    fourcc = head[80:84].decode("latin1").strip("\0 ")
    bits = struct.unpack_from("<I", head, 84)[0]
    amask = struct.unpack_from("<I", head, 92)[0]
    caps2 = struct.unpack_from("<I", head, 108)[0]
    if caps2 & 0xFE00:
        raise ValueError("кубические DDS не поддерживаются")
    if fourcc in ("DXT1", "DXT3", "DXT5"):
        is_block = True
        unit = 8 if fourcc == "DXT1" else 16
        if idx is None:
            idx = {"DXT1": 11, "DXT3": 13, "DXT5": 10}[fourcc]
    else:
        is_block = False
        bpp = (bits or 32) // 8
        unit = bpp
        if idx is None:
            if bpp == 32:
                idx = 5
            elif bpp == 24:
                idx = 0x14
            elif bpp == 16:
                idx = 2 if amask else 3
            elif bpp == 1:
                idx = 0 if amask else 1
            else:
                idx = 5
    pos = 4 + 124
    levels = []
    w, h = width, height
    i = 0
    while True:
        if mips and i >= mips:
            break
        if is_block:
            bw = max(1, (w + 3) // 4)
            bh = max(1, (h + 3) // 4)
            size = bw * bh * unit
        else:
            size = max(1, w) * max(1, h) * unit
        if pos + size > len(data):
            break
        levels.append((max(1, w), max(1, h), data[pos:pos + size]))
        pos += size
        i += 1
        if w <= 1 and h <= 1:
            break
        w //= 2
        h //= 2
    if not levels:
        raise ValueError("в DDS нет пиксельных данных")
    tfh, tfd = build_pair(levels, width, height, idx, n_tfh=n_tfh)
    return tfh, tfd


def encode_from_pair(tfh_bytes, tfd_bytes, replace_base=None):
    """
    Перепаковать существующую пару, при необходимости заменив БАЗОВЫЙ уровень.

    replace_base — (w, h, data) квадратный несжатый уровень в формате текстуры
    либо None. Служебные поля (формат, мипы, куб) берутся из оригинального .tfh,
    поэтому упаковка остаётся родной — ровно то, что нужно для игры.
    """
    info = parse_tfh(tfh_bytes)
    chain, _used = build_chain(info, tfh_bytes, tfd_bytes)
    if replace_base is not None:
        _w, _h, data = replace_base
        if data and len(data) <= len(chain[0][2]) + 1:
            chain = [(chain[0][0], chain[0][1], data)] + chain[1:]
    # сколько уровней оригинал держал в .tfh — определяем по его таблице
    table = parse_level_table(info, tfh_bytes) or []
    n_tfh = 0
    for off, size in table:
        if off >= info["A"] and off + size <= len(tfh_bytes):
            n_tfh += 1
        else:
            break
    # chain уже в «полосной» раскладке (build_chain так и отдаёт)
    return build_pair(chain, info["w"], info["h"], info["idx"],
                      cube=info["cube"], n_tfh=n_tfh,
                      h0_extra=info["h0"], h1_extra=info["h1"],
                      already_packed=True)



def alpha_mask_stats(tfh_path, tfd_path=None):
    """Статистика альфа-маски `_s1` (кристаллы Эллидиума).

    Возвращает dict(mean, frac, mips, fmt) или None, если формат не DXT5
    (у обычных кораблей `_s1` — DXT1, альфы нет).

    ВАЖНО: это только МАСКА. Цвет кристалла в текстуре не лежит — в игре он
    приходит per-instance (UserParam4_Float4), поэтому по альфе можно лишь
    определить область, но не цвет.
    """
    import os as _os
    tfh = open(tfh_path, "rb").read()
    info = parse_tfh(tfh)
    if info["idx"] not in (10, 14):          # DXT5
        return None
    tfd = b""
    if tfd_path and _os.path.exists(tfd_path):
        tfd = open(tfd_path, "rb").read()
    table = parse_level_table(info, tfh)
    if not table:
        return None
    off, size = table[-1]                    # базовый уровень
    data = _level_data(off, size, tfh, tfd, info.get("A", 0))
    if not data:
        return None
    bw = max(1, (info["w"] + 3) // 4)
    bh = max(1, (info["h"] + 3) // 4)
    blocks = bw * bh
    if blocks * 16 > len(data):
        return None

    def _block_alpha(b):
        a0, a1 = b[0], b[1]
        bits = int.from_bytes(b[2:8], "little")
        if a0 > a1:
            tab = [a0, a1] + [((7 - i) * a0 + (i - 1) * a1) // 7 for i in range(2, 8)]
        else:
            tab = [a0, a1] + [((5 - i) * a0 + (i - 1) * a1) // 5 for i in range(2, 6)] + [0, 255]
        return [tab[(bits >> (3 * i)) & 7] for i in range(16)]

    step = max(1, blocks // 40000)
    total = cnt = hi = 0
    for i in range(0, blocks, step):
        vals = _block_alpha(data[i * 16:i * 16 + 16])
        total += sum(vals)
        cnt += len(vals)
        hi += sum(1 for v in vals if v > 16)
    if not cnt:
        return None
    return dict(mean=total // cnt, frac=hi / cnt, mips=info["mips"], fmt=info["idx"])
