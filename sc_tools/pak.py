#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sc_tools.pak — архивы Star Conflict (.pak, формат TPAK).

Ядро формата лежит в pak.py, здесь —
удобный API для скриптов и GUI.

ШИФР ИМЁН РАСКРЫТ (функция FUN_1400332a0 в game.exe):
    запись таблицы имён = (u32 len)(len байт)
    для байта j записи номер rec:
        key = ((j & 0xFF) * 2 + (len & 0xFF) * 3 + ((len // 5) & 0xFF) * (-5) + rec) & 0xFF
        plain[j] = payload[j] ^ key
    Внутри лежат полные относительные пути, напр.
    'mapskit\\maps\\textures\\test_tec\\test_nm.tfd'.

    from sc_tools import pak

    names = pak.decode_names(r"D:\\file.pak")       # список путей
    info  = pak.info(r"D:\\file.pak")
    pak.unpack_file(r"D:\\file.pak", r"D:\\out")    # имена из архива
    pak.pack_dir(r"D:\\out", r"D:\\out.pak")        # имена кодируются формулой
"""
import os, sys, importlib.util, zlib

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_core():
    """Загружает pak.py как модуль (ядро формата)."""
    path = os.path.join(_ROOT, "pak.py")
    spec = importlib.util.spec_from_file_location("sc_pak_core", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_core = None


DEFLATE_LEVEL = 9   # уровень сжатия игры (проверено на чанках оригиналов)


def _deflate(data):
    """deflate того же уровня, что и в игре (было 6 — пак выходил больше)."""
    c = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    return c.compress(bytes(data)) + c.flush()


def _name_bytes(name):
    """Имя -> байты пака: ASCII и кириллица в cp1251, иначе Latin-1."""
    try:
        return name.encode("cp1251")
    except UnicodeEncodeError:
        pass
    try:
        return name.encode("latin-1")
    except UnicodeEncodeError:
        raise ValueError("имя %r нельзя записать в пак (ASCII/cp1251/Latin-1)" % (name,))


def core():
    """Доступ к низкоуровневому модулю (parse/assemble/deflate/...)."""
    global _core
    if _core is None:
        _core = _load_core()
    return _core


# ------------------------------------------------------ имена (шифр)

def key_byte(pos, length, rec):
    """Ключевой байт шифра имён (формула из FUN_1400332a0)."""
    return ((pos & 0xFF) * 2 + (length & 0xFF) * 3 +
            ((length // 5) & 0xFF) * (-5) + rec) & 0xFF


def decode_names_raw(names_raw):
    """Снять шифр с распакованной таблицы имён -> список путей."""
    out = []
    i, rec = 0, 0
    while i + 4 <= len(names_raw):
        n = int.from_bytes(names_raw[i:i + 4], "little")
        if not (0 < n <= 65536 and i + 4 + n <= len(names_raw)):
            break
        buf = bytearray(names_raw[i + 4:i + 4 + n])
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        out.append(bytes(buf).decode("latin1"))
        i += 4 + n + 1
        rec += 1
    return out


def encode_names(paths):
    """Закодировать список путей в таблицу имён (обратная формула)."""
    out = bytearray()
    for rec, p in enumerate(paths):
        b = p.encode("latin1")
        n = len(b)
        payload = bytearray(b)
        for j in range(n):
            payload[j] ^= key_byte(j, n, rec)
        out += n.to_bytes(4, "little") + payload + b"\x00"
    return bytes(out)


def _pair_expand(info, names, files_resolved):
    """Текстурные архивы: одна запись = пара .tfh/.tfd."""
    if len(info["chunks"]) <= info["count"] + 1:
        return files_resolved
    full = []
    for nm in files_resolved:
        base = nm[:-4] if nm.lower().endswith((".tfh", ".tfd")) else nm
        full.append(base + ".tfh")
        full.append(base + ".tfd")
    return full


def decode_names(path):
    """Имена из архива: список путей (по одному на файловую запись).

    Имя берётся по `field1` — это СМЕЩЕНИЕ payload'а имени в таблице имён.
    4-е поле записи — индекс первого чанка, а не имя (прежняя версия ошибалась
    и теряла ~20,5 % имён).
    """
    info = _names_info(path)
    table = names_by_offset(info["names_raw"])
    by_index = list(table.values())
    res = []
    for f in info["files"]:
        nm = table.get(f["field1"])
        if nm is None:                     # fallback: индекс, если он валиден
            n = f["name_idx"]
            nm = by_index[n] if 0 <= n < len(by_index) else ""
        res.append(nm)
    return res


def names_by_offset(names_raw):
    """Таблица имён -> {смещение payload'а: путь}."""
    out, i, rec = {}, 0, 0
    while i + 4 <= len(names_raw):
        n = int.from_bytes(names_raw[i:i + 4], "little")
        if not (0 < n <= 65536 and i + 4 + n <= len(names_raw)):
            break
        buf = bytearray(names_raw[i + 4:i + 4 + n])
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        out[i + 4] = bytes(buf).decode("latin1")
        i += 4 + n + 1
        rec += 1
    return out


def _names_info(path):
    """Лёгкий разбор: только заголовок и таблицы (без распаковки чанков)."""
    import mmap
    import struct as _st
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if size < 24:
            raise ValueError("файл меньше 24 байт")
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            if _st.unpack_from("<I", mm, 0)[0] != 0x4B415054:   # 'TPAK'
                raise ValueError("не TPAK")
            count = _st.unpack_from("<I", mm, 12)[0]
            p = 20
            _ulen, clen = _st.unpack_from("<II", mm, p)
            p += 8
            blob = bytearray(mm[p:p + clen])
            key = _st.pack("<I", count & 0xFFFFFFFF)
            for i in range(min(4, len(blob))):
                blob[i] ^= key[i]
            import zlib as _z
            raw = _z.decompressobj(-15).decompress(bytes(blob))
            p = (p + clen + 3) & ~3
            index = list(_st.unpack_from("<%dI" % count, mm, p))
            p += count * 4
            csize = _st.unpack_from("<I", mm, p)[0]
            blob2 = bytearray(mm[p + 4:p + 4 + csize])
            key2 = _st.pack("<I", (count + csize) & 0xFFFFFFFF)
            for i in range(min(4, len(blob2))):
                blob2[i] ^= key2[i]
            ftab = _z.decompressobj(-15).decompress(bytes(blob2))
            files = []
            for i in range(count):
                sz, f1, f2, nidx = _st.unpack_from("<4I", ftab, i * 16)
                files.append(dict(size=sz, field1=f1, field2=f2, name_idx=nidx))
            return dict(count=count, index=index, files=files, names_raw=raw)
        finally:
            mm.close()


# ---------------------------------------------------------------- API

def parse(path):
    """Разобрать .pak -> dict (version, count, names, files, chunks, ...)."""
    return core().parse(open(path, "rb").read())


def info(path):
    """Краткая сводка об архиве."""
    it = parse(path)
    one = len(it["chunks"]) <= it["count"] + 1
    return dict(path=path, size=os.path.getsize(path), version=it["version"],
                files=it["count"], chunks=len(it["chunks"]),
                names=len(it["names"]),
                layout=("1 файл = 1 чанк" if one else "tfh+tfd"))


TFD_CHUNK = 1572864   # 1.5 МиБ — предельный размер одного чанка .tfd в игре


def tfd_levels(tfh, tfd_size=None):
    """Уровни, лежащие в `.tfd`: [(off, size, pitch), ...].

    Число записей берётся из смещения таблицы (`a = 8 + n*12`), а не из поля
    `mips`: у кубических текстур в таблице `6 * mips` записей.
    """
    if not tfh or len(tfh) < 20:
        return []
    import struct as _st
    mips = _st.unpack_from("<I", tfh, 4)[0] & 0x0F
    a_off = _st.unpack_from("<H", tfh, 8)[0]
    n = mips
    if a_off >= 8 and (a_off - 8) % 12 == 0 and (a_off - 8) // 12:
        n = (a_off - 8) // 12
    n = min(n, max(0, (len(tfh) - 8) // 12))
    entries = []
    for i in range(n):
        off, size, pitch = _st.unpack_from("<3I", tfh, 8 + i * 12)
        if size:
            entries.append((off, size, pitch))
    entries.sort()
    if tfd_size is not None:
        # уровни .tfd идут непрерывно от 0: у кубов мелкие уровни случайно
        # попадают в диапазон смещений .tfh, и старая проверка их теряла
        out, pos = [], 0
        for off, size, pitch in entries:
            if off == pos and pos + size <= tfd_size:
                out.append((off, size, pitch))
                pos += size
        if pos == tfd_size:
            return out
    return [e for e in entries if not (e[0] >= a_off and e[0] + e[1] <= len(tfh))]


def _split_level(size, pitch):
    """Нарезать уровень как игра: n = ceil(size / 1.5 МиБ), строки поровну.

    Проверено на всех 10 113 `.tfd` из 588 паков: например 4 МиБ / pitch 8192
    -> 170+170+172 строки, 64 МиБ / pitch 16384 -> 95*42 + 106.
    """
    if pitch and size % pitch == 0:
        rows = size // pitch
        n = max(1, min(rows, -(-size // TFD_CHUNK)))
        rpc = rows // n
        return [rpc * pitch] * (n - 1) + [(rows - rpc * (n - 1)) * pitch]
    n = max(1, -(-size // TFD_CHUNK))
    base = size // n
    return [base] * (n - 1) + [size - base * (n - 1)]


def _tfd_chunks(tfd, tfh=None):
    """Разбить `.tfd` на чанки так же, как это делает игра.

    Старая формула (`round(base / 1392640)` для последнего чанка) не совпадала
    с игрой у 247 из 10 113 текстур: теперь каждый уровень режется на
    `ceil(size / 1.5 МиБ)` частей, строки делятся поровну.
    """
    levels = tfd_levels(tfh, len(tfd))
    got = [s for _o, s, _p in levels]
    if got and sum(got) == len(tfd):
        out, pos = [], 0
        for _off, size, pitch in levels:
            for s in _split_level(size, pitch):
                out.append(tfd[pos:pos + s])
                pos += s
        return out
    sizes = _split_level(len(tfd), 0)
    out, pos = [], 0
    for s in sizes:
        out.append(tfd[pos:pos + s])
        pos += s
    return out


def unpack_file(pak_path, out_dir, names_map=None, verbose=False, progress=None):
    """Распаковать архив в каталог, сохраняя структуру папок.

    Имена берутся ИЗ АРХИВА (шифр снят формулой из game.exe); names_map —
    необязательный fallback для архивов с нестандартной таблицей имён.
    Рядом кладётся `_pak.map`: служебные поля архива (порядок записей, field1,
    field2, индексы) — они нужны, чтобы упаковать каталог обратно БЕЗ потерь.
    """
    c = core()
    it = parse(pak_path)
    items = c.resolve_paths(it, names_map)
    dec = decode_names_raw(it["names_raw"])

    if len(dec) == len(items):
        rels = [dec[i] for i in range(len(items))]
    else:
        rels = []
        for payload, rel, data, nidx in items:
            rels.append(dec[nidx] if 0 <= nidx < len(dec) else rel)

    os.makedirs(out_dir, exist_ok=True)
    lines = ["# карта имён: hex(payload)=путь"]
    total = len(items)
    for i, (payload, rel, data, nidx) in enumerate(items):
        rel = (rels[i] or rel).replace("/", "\\")
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        path = os.path.join(out_dir, *parts)
        os.makedirs(os.path.dirname(path) or out_dir, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        lines.append("%s=%s" % (payload.hex(), rel))
        if progress:
            progress(i + 1, total, rel)
    with open(os.path.join(out_dir, "_names.map"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    # служебная карта архива: без неё при упаковке терялись порядок и поля
    try:
        import json
        # Размеры чанков по файлам: нужны, чтобы при перепаковке разложить
        # .tfd точно так же, как это сделала игра (иначе игра читает не те
        # границы и падает). Логика та же, что в assemble: набираем чанки,
        # пока сумма не достигнет размера файла.
        chunk_sizes = []
        ci = 0
        n_files = len(it["files"])
        for i, f in enumerate(it["files"]):
            want = f["size"] or 0
            got, group = 0, []
            while ci < len(it["chunks"]):
                c = it["chunks"][ci]
                ul = c.get("uncompressed") or len(c["data"])
                group.append(ul)
                got += ul
                ci += 1
                if want and got >= want:
                    break
                if len(it["chunks"]) - ci <= n_files - i - 1:
                    break
            chunk_sizes.append(group)

        fmap = []
        for i, f in enumerate(it["files"]):
            fmap.append({"rel": rels[i] if i < len(rels) else "",
                         "size": f["size"], "field1": f["field1"],
                         "field2": f["field2"], "name_idx": f["name_idx"],
                         "chunks": chunk_sizes[i] if i < len(chunk_sizes) else []})
        with open(os.path.join(out_dir, "_pak.map"), "w", encoding="utf-8") as fh:
            json.dump({"version": it["version"], "count": it["count"],
                       "index": it["index"], "files": fmap}, fh, ensure_ascii=False)
    except Exception as e:
        print("   (не удалось записать _pak.map: %s)" % e)
    return total


def pack_dir(src_dir, out_pak, names_map=None, progress=None):
    """Упаковать каталог в .pak. Имена кодируются формулой (или берутся из карты)."""
    c = core()
    root = os.path.abspath(src_dir)
    mapfile = names_map or os.path.join(root, "_names.map")
    m = c.load_map(mapfile)

    entries = []
    for dp, dn, fn in os.walk(root):
        for f in sorted(fn):
            if f in ("_names.map", "_pak.map"):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            entries.append((rel, open(full, "rb").read()))

    rev = {}
    for k, v in m.items():
        rev[v.lower().replace("/", "\\")] = k
    named = []
    for rel, data in entries:
        key = rel.replace("/", "\\")
        hx = rev.get(key.lower())
        named.append((bytes.fromhex(hx) if hx else None, key, data, rel))
    named.sort(key=lambda e: e[3])

    # порядок и служебные поля: если рядом есть _pak.map (его пишет unpack_file),
    # сохраняем ИХ — иначе пересобранный архив отличается по раскладке
    order, fmap, saved_index = None, {}, None
    mapfile = os.path.join(root, "_pak.map")
    if os.path.exists(mapfile):
        try:
            import json
            saved = json.load(open(mapfile, encoding="utf-8"))
            order = [str(f.get("rel", "")).replace("/", "\\").lower() for f in saved["files"]]
            for f in saved["files"]:
                fmap[str(f.get("rel", "")).replace("/", "\\").lower()] = f
            saved_index = saved.get("index")
        except Exception:
            order, fmap, saved_index = None, {}, None

    blobs = {rel.replace("/", "\\").lower(): data for rel, data in entries}

    # одна запись на файл (как в игре): .tfh — один чанк, .tfd — по уровням мипов
    files = []
    for rel, data in entries:
        key = rel.replace("/", "\\")
        low = key.lower()
        hx = rev.get(low)
        payload = bytes.fromhex(hx) if hx else b"\0" * 16
        saved = fmap.get(low)
        saved_chunks = saved.get("chunks") if saved else None
        if saved_chunks and len(saved_chunks) > 1 and sum(saved_chunks) == len(data):
            # Раскладываем чанки ровно по границам оригинала — иначе игра читает по своим.
            chunks, pos = [], 0
            for s in saved_chunks:
                chunks.append(data[pos:pos + s])
                pos += s
        elif low.endswith(".tfd"):
            chunks = _tfd_chunks(data, blobs.get(low[:-4] + ".tfh"))
        else:
            chunks = [data]
        files.append([payload, key, chunks, saved])
    if order:
        rank = {k: i for i, k in enumerate(order)}
        files.sort(key=lambda f: rank.get(f[1].lower(), len(rank)))

    count = len(files)

    # --- таблица имён: ЗАШИФРОВАННЫЙ ПУТЬ, по записи на файл ---
    # field1 в записи файла — это СМЕЩЕНИЕ имени в таблице имён (у оригинала
    # значения 4, 73, 142…, а вся таблица 3922 байта). Поэтому считаем его
    # здесь же: запись = [u32 длина][зашифрованные байты][0x00], значит
    # полезная нагрузка имени начинается с len(names_raw) + 4.
    names_raw = bytearray()
    name_offsets = []
    for rec, (payload, key, ch, saved) in enumerate(files):
        name_offsets.append(len(names_raw) + 4)
        b = _name_bytes(key)
        n = len(b)
        buf = bytearray(b)
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        names_raw += n.to_bytes(4, "little") + buf + b"\x00"

    # --- таблица чанков: coff считается внутри файла ---
    # Данные чанка лежат по doff без префикса; если сжатие не уменьшило размер,
    # чанк хранится как есть (dlen == ul).
    ctab, cdata = bytearray(), bytearray()
    for gi, (payload, key, ch, saved) in enumerate(files):
        inner = 0
        for d in ch:
            comp = _deflate(d)
            if len(comp) >= len(d):
                comp, ul, dl = d, len(d), len(d)      # храним без сжатия
            else:
                ul, dl = len(d), len(comp)
            ctab += c.struct.pack("<4I", inner, ul, len(cdata), dl)
            cdata += comp
            inner += len(d)
        if progress:
            progress(gi + 1, count, key)

    # --- таблица файлов: (размер, field1, ЧИСЛО ЧАНКОВ, индекс первого чанка) ---
    # Четвёртое поле — НЕ номер файла, а ИНДЕКС ЕГО ПЕРВОГО ЧАНКА (в оригинале
    # 0, 1, 9, 10, 18…: файл 0 занимает 1 чанк, файл 1 — 8, отсюда 9). Если
    # записать туда номер файла, игра ищет чанки не там и падает.
    ftab = bytearray()
    first_chunk = 0
    for i, (payload, key, ch, saved) in enumerate(files):
        total = sum(len(d) for d in ch)
        f1 = name_offsets[i]
        ftab += c.struct.pack("<4I", total, f1, len(ch), first_chunk)
        first_chunk += len(ch)
    # index — индексы файлов, отсортированные по имени (так во всех 588 паках
    # игры). Сохранённый в _pak.map index после правок может устареть, поэтому
    # пересчитываем всегда: это инвариант формата, а не «раскладка».
    findex = b"".join(c.struct.pack("<I", i)
                      for i in sorted(range(count), key=lambda k: files[k][1]))

    ncomp = c.xor4(_deflate(bytes(names_raw)), count)
    fcomp = _deflate(bytes(ftab))
    ccomp = _deflate(bytes(ctab))
    n_chunks = len(ctab) // 16          # число ЧАНКОВ (не файлов!)

    blob = bytearray()
    blob += c.struct.pack("<5I", c.MAGIC, 7, 0, count, 0xFFFFFFE3)
    blob += c.struct.pack("<2I", len(names_raw), len(ncomp)) + ncomp
    while len(blob) % 4:
        blob += b"\x00"
    blob += findex + c.struct.pack("<I", len(fcomp)) + \
        c.xor4(fcomp, count + len(fcomp))
    while len(blob) % 4:
        blob += b"\x00"
    # Второе поле записи — число чанков.
    blob += c.struct.pack("<2I", len(ccomp), n_chunks) + \
        c.xor4(ccomp, count + len(ccomp) + n_chunks)
    # ВАЖНО: область данных чанков выравнивается по 4 байта (парсер делает
    # pdata = (p + num_chunks + 3) & ~3). Без этого выравнивания данные
    # сдвигались на 1..3 байта и чанки читались пустыми.
    while len(blob) % 4:
        blob += b"\x00"
    blob += cdata

    os.makedirs(os.path.dirname(os.path.abspath(out_pak)) or ".", exist_ok=True)
    with open(out_pak, "wb") as fh:
        fh.write(blob)
    return out_pak
