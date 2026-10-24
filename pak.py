#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TPAK (.pak) Star Conflict — распаковка и упаковка с сохранением имён и папок.

Команды:
    unpack <полный путь к .pak> <папка для выгрузки>
        распаковывает архив в папку (создаётся при необходимости), сохраняя
        структуру каталогов; рядом пишет _names.map

    pack <путь к папке>
        упаковывает папку; результат кладётся рядом со скриптом в папку packed
        (имя архива = <имя_папки>.pak; если внутри папки лежит <имя_папки>.pak,
        путь к нему подсказывается в выводе)

    info <полный путь к .pak>
        краткая информация об архиве

Формат восстановлен по декомпиляции game.exe (FUN_140032ad0) и scunpak.exe:
  заголовок 20 байт: 'TPAK', 7, 0, count, 0xFFFFFFE3
  таблица имён:   (u32 ulen)(u32 clen)(deflate), XOR первого dword на count
  таблица файлов: u32 index[count] + (u32 csize)(deflate), XOR на count+csize
  таблица чанков: u32 num_chunks, u32 n_entries, (deflate), XOR count+num+n_ent
  данные чанков:  raw_inflate(data[cdata_off + delta : ... + compressed])
"""
import struct, zlib, os, sys, argparse

MAGIC = 0x4B415054
HERE = os.path.dirname(os.path.abspath(__file__))
PACKED_DIR = os.path.join(HERE, "packed")


# ------------------------------------------------------ имена (шифр)
# Формула из game.exe, FUN_1400332a0:
#   запись = (u32 len)(len байт); для байта j записи номер rec
#   key = ((j & 0xFF)*2 + (len & 0xFF)*3 + ((len//5) & 0xFF)*(-5) + rec) & 0xFF
#   plain[j] = payload[j] ^ key

def key_byte(pos, length, rec):
    """Ключевой байт шифра имён."""
    return ((pos & 0xFF) * 2 + (length & 0xFF) * 3 +
            ((length // 5) & 0xFF) * (-5) + rec) & 0xFF


def decode_names_raw(names_raw):
    """Снять шифр с распакованной таблицы имён -> список путей."""
    out = []
    i, rec = 0, 0
    while i + 4 <= len(names_raw):
        n = u32(names_raw, i)
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
    """Закодировать пути в таблицу имён (обратная формула)."""
    out = bytearray()
    for rec, p in enumerate(paths):
        b = p.encode("latin1")
        n = len(b)
        buf = bytearray(b)
        for j in range(n):
            buf[j] ^= key_byte(j, n, rec)
        out += struct.pack("<I", n) + buf + b"\x00"
    return bytes(out)


# ---------------------------------------------------------------- разбор

def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def inflate_key(raw, off, clen, key):
    blob = bytearray(raw[off:off + clen])
    kb = struct.pack("<I", key & 0xFFFFFFFF)
    for i in range(min(4, len(blob))):
        blob[i] ^= kb[i]
    return zlib.decompressobj(-15).decompress(bytes(blob))


def parse_names(buf):
    out, i = [], 0
    while i + 4 <= len(buf):
        ln = u32(buf, i)
        if not (0 < ln <= 1024 and i + 4 + ln <= len(buf)):
            break
        out.append(buf[i + 4:i + 4 + ln])
        i += 4 + ln + 1
    return out


def parse(raw):
    if len(raw) < 24 or u32(raw, 0) != MAGIC:
        raise ValueError("не TPAK (нет магии 'TPAK')")
    ver, zero, count = u32(raw, 4), u32(raw, 8), u32(raw, 12)
    p = 20
    ulen, clen = u32(raw, p), u32(raw, p + 4)
    p += 8
    names_raw = inflate_key(raw, p, clen, count)
    p = (p + clen + 3) & ~3
    index = list(struct.unpack_from("<%dI" % count, raw, p))
    p += count * 4
    csize = u32(raw, p)
    ftab = inflate_key(raw, p + 4, csize, count + csize)
    p = (p + 4 + csize + 3) & ~3
    files = []
    for i in range(count):
        size, f1, f2, nidx = struct.unpack_from("<4I", ftab, i * 16)
        files.append(dict(size=size, field1=f1, field2=f2, name_idx=nidx))
    num_chunks, n_entries = u32(raw, p), u32(raw, p + 4)
    p += 8
    ctab = inflate_key(raw, p, num_chunks, count + num_chunks + n_entries)
    # ВАЖНО: область данных чанков выровнена по 4 байта. Без этого выравнивания
    # чанки, хранящиеся БЕЗ сжатия (dlen == ul, например .tfh-хвосты текстур),
    # читались со сдвигом на 1..3 байта и файлы получались повреждёнными.
    # У сжатых чанков сдвиг подбирается перебором delta, поэтому там ошибка
    # была незаметна, а у несжатых проверки нет — отсюда «битые» текстуры.
    pdata = (p + num_chunks + 3) & ~3
    chunks = []
    for i in range(n_entries):
        coff, ul, doff, dlen = struct.unpack_from("<4I", ctab, i * 16)
        data, best, best_delta = b"", None, 0
        if ul and dlen == ul:                       # хранится без сжатия
            data = raw[pdata + doff:pdata + doff + dlen]
        else:
            for delta in (0, 2, 1, 3):              # поток со смещением 0 или +2
                seg = raw[pdata + doff + delta:pdata + doff + delta + dlen]
                try:
                    d = zlib.decompressobj(-15)
                    out = d.decompress(seg, (ul or 1 << 30) + 1)
                except Exception:
                    continue
                if not d.eof:
                    continue
                if ul == 0 or len(out) == ul:
                    data, best_delta = out, delta
                    break
                if best is None:
                    best, best_delta = out, delta
            if not data and best is not None:
                data = best
        chunks.append(dict(offset=coff, uncompressed=ul, doff=doff, dlen=dlen,
                           data=data, delta=best_delta))
    return dict(version=ver, count=count, names=parse_names(names_raw),
                names_raw=names_raw, files=files, chunks=chunks,
                n_entries=n_entries, index=index)


# ------------------------------------------------------- сборка файлов

def assemble(info):
    """[(index, payload_имени, данные)] в порядке записей архива."""
    count, chunks, names = info["count"], info["chunks"], info["names"]
    names = parse_names(info["names_raw"])
    # УНИВЕРСАЛЬНАЯ раскладка: у каждой файловой записи в таблице есть
    # суммарный размер. Набираем чанки (в порядке таблицы чанков), пока сумма
    # не достигнет этого размера. Так корректно читаются и «1 файл = 1 чанк»,
    # и текстурные архивы (.tfh-заголовок + мипы .tfd), и смешанные.
    out = []
    ci = 0
    nch = len(info["files"])
    for i, f in enumerate(info["files"]):
        if ci >= len(chunks):
            break
        want = f["size"] or 0
        group, got = [], 0
        while ci < len(chunks):
            group.append(chunks[ci])
            got += chunks[ci].get("uncompressed") or len(chunks[ci]["data"])
            ci += 1
            if want and got >= want:
                break
            # страховка: не забираем чанки у следующих записей
            left_recs = nch - i - 1
            if len(chunks) - ci <= left_recs:
                break
        n = f["name_idx"]
        nm = names[n].decode("latin1") if n < len(names) else ""
        blob = b"".join(c["data"] for c in group)
        out.append((i, (nm or ("file_%02d" % i)).encode("latin1"), blob))
    return out


def load_map(path):
    m = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            m[k.strip().lower()] = v.strip()
    return m


def resolve_paths(info, mapfile):
    """[(payload, путь, данные, name_idx)] — путь из карты либо заглушка."""
    m = load_map(mapfile)
    res = []
    for idx, payload, data in assemble(info):
        f = info["files"][idx] if idx < len(info["files"]) else {}
        nidx = f.get("name_idx", idx)
        key = payload.hex()
        rel = m.get(key) or m.get(str(idx)) or ("file_%03d" % idx)
        res.append((payload, rel.replace("\\", "/").lstrip("/"), data, nidx))
    return res


# ---------------------------------------------------------- распаковка

def cmd_unpack(a):
    pak, out = a.pak, a.out
    if not os.path.isfile(pak):
        print("ОШИБКА: архив не найден: %s" % pak)
        return 1
    info = parse(open(pak, "rb").read())
    items = resolve_paths(info, a.names or os.path.join(out, "_names.map"))
    # имена берём ИЗ АРХИВА (шифр снят), карта — только fallback
    try:
        dec = decode_names_raw(info["names_raw"])
    except Exception:
        dec = []
    os.makedirs(out, exist_ok=True)
    lines = ["# карта имён: hex(payload)=путь",
             "# layout: records=%d chunks=%d" % (len(info["files"]), len(info["chunks"]))]
    n = 0
    # Имя, которое лежит В АРХИВЕ (полный путь), и путь, по которому кладём
    # файл на диск. В карте храним архивное имя — его и зашифруем при упаковке.
    for k, (payload, rel, data, nidx) in enumerate(items):
        arc_name = dec[k] if (k < len(dec) and dec[k]) else rel.replace("/", "\\")
        disk_rel = rel.replace("/", "\\")
        if not disk_rel or disk_rel.startswith("file_"):
            disk_rel = arc_name
        path = os.path.join(out, *[p for p in disk_rel.replace("\\", "/").split("/") if p])
        os.makedirs(os.path.dirname(path) or out, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        lines.append("%s=%s" % (payload.hex(), arc_name))
        n += 1
        if a.verbose:
            print("  %-46s %d" % (disk_rel, len(data)))
    with open(os.path.join(out, "_names.map"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("распаковано %d файлов: %s\n  -> %s" % (n, os.path.basename(pak), out))
    return 0


# ------------------------------------------------------------ упаковка

def deflate(data, level=9):
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def xor4(b, key):
    b = bytearray(b)
    kb = struct.pack("<I", key & 0xFFFFFFFF)
    for i in range(min(4, len(b))):
        b[i] ^= kb[i]
    return bytes(b)


def split_tfd(data):
    base = 524288 if len(data) <= 700000 else 1048576
    sizes = [base, base // 4, base // 16, base // 64, base // 256]
    out, pos = [], 0
    for s in sizes:
        if pos + s <= len(data):
            out.append(data[pos:pos + s])
            pos += s
    if pos < len(data):
        out.append(data[pos:])
    return out


def cmd_pack(a):
    root = os.path.abspath(a.dir)
    if not os.path.isdir(root):
        print("ОШИБКА: папка не найдена: %s" % root)
        return 1
    mapfile = a.names or os.path.join(root, "_names.map")
    m = load_map(mapfile)

    entries = []
    for dp, dn, fn in os.walk(root):
        for f in sorted(fn):
            if f == "_names.map":
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace("\\", "/")
            entries.append((rel, open(full, "rb").read()))

    # Имя ресурса берём как ОТНОСИТЕЛЬНЫЙ ПУТЬ внутри архива: его и шифруем
    # при сборке таблицы имён (номер записи известен там же).
    named = []
    for rel, data in entries:
        named.append((rel.replace("/", "\\"), rel, data))
    named.sort(key=lambda e: e[1])

    # --- имя выходного архива: в папку packed рядом со скриптом ---
    base = os.path.basename(root) or "packed"
    os.makedirs(PACKED_DIR, exist_ok=True)
    out = a.out if a.out else os.path.join(PACKED_DIR, base + ".pak")

    # --- раскладка ---
    # 1) Если в _names.map есть метаданные раскладки (records=N chunks=M),
    #    воспроизводим её ТОЧНО: каждая строка карты = одна запись архива.
    # 2) Иначе: пара .tfh + мипы .tfd считается одной записью, остальное — 1:1.
    layout = None
    if os.path.exists(mapfile):
        for line in open(mapfile, encoding="utf-8"):
            if line.startswith("# layout:"):
                parts = dict(p.split("=") for p in line.split()[2:] if "=" in p)
                try:
                    layout = (int(parts.get("records", 0)), int(parts.get("chunks", 0)))
                except Exception:
                    layout = None
                break

    by_rel = {rel.replace("/", "\\"): (rel, data) for rel, data in entries}
    groups = []
    if layout and layout[0] == len(m):
        # Одна строка карты = одна запись архива. Имя берём ИЗ КАРТЫ
        # (архивное, полное), файл — с диска по тому же относительному пути.
        for hexkey, path in m.items():
            key = path.replace("/", "\\")
            hit = by_rel.get(key)
            if hit is None:
                # файл мог быть положен по другому пути — ищем по basename
                bn = os.path.basename(key)
                for r2, d2 in entries:
                    if os.path.basename(r2.replace("/", "\\")) == bn:
                        hit = (r2, d2)
                        break
            if hit is None:
                continue
            groups.append((key, [hit[1]]))     # key — ИМЯ для шифрования
    else:
        tex = {}
        for payload, rel, data in named:
            low = rel.lower()
            if low.endswith(".tfh"):
                tex.setdefault(rel[:-4], {})["tfh"] = (payload, rel, data)
            elif low.endswith(".tfd"):
                tex.setdefault(rel[:-4], {})["tfd"] = (payload, rel, data)
        paired = set(b for b, g in tex.items() if "tfh" in g and "tfd" in g)
        used = set()
        for payload, rel, data in named:
            low = rel.lower()
            base = rel[:-4] if low.endswith((".tfh", ".tfd")) else None
            if base in paired:
                if base in used:
                    continue
                used.add(base)
                g = tex[base]
                ch = [g["tfh"][2]] + split_tfd(g["tfd"][2])
                groups.append((g["tfh"], ch))
            else:
                groups.append(((payload, rel, data), [data]))

    count = len(groups)
    names_raw = bytearray()
    for rec, (payload, ch) in enumerate(groups):
        # payload — либо строка (путь), либо байты имени из карты
        if isinstance(payload, str):
            b = payload.encode("latin1")
        elif isinstance(payload, (bytes, bytearray)):
            b = bytes(payload)
        else:
            b = str(payload).encode("latin1")
        buf = bytearray(b)
        for j in range(len(buf)):
            buf[j] ^= key_byte(j, len(buf), rec)
        names_raw += struct.pack("<I", len(b)) + buf + b"\x00"

    # --- таблица чанков ---
    ctab, cdata = bytearray(), bytearray()
    all_off = 0
    for payload, ch in groups:
        for d in ch:
            c = deflate(d)
            ctab += struct.pack("<4I", all_off, len(d), len(cdata), len(c))
            cdata += c
            all_off += len(d)

    # --- таблица файлов ---
    ftab = bytearray()
    cum = 4
    for i, (payload, ch) in enumerate(groups):
        total = sum(len(d) for d in ch)
        ftab += struct.pack("<4I", total, cum, 1, i)
        cum += total
    findex = b"".join(struct.pack("<I", i) for i in
                     sorted(range(count), key=lambda k: groups[k][0]))

    ncomp = xor4(deflate(bytes(names_raw)), count)
    fcomp = deflate(bytes(ftab))
    ccomp = deflate(bytes(ctab))

    blob = bytearray()
    blob += struct.pack("<5I", MAGIC, 7, 0, count, 0xFFFFFFE3)
    blob += struct.pack("<2I", len(names_raw), len(ncomp)) + ncomp
    while len(blob) % 4:
        blob += b"\x00"
    blob += findex + struct.pack("<I", len(fcomp)) + xor4(fcomp, count + len(fcomp))
    while len(blob) % 4:
        blob += b"\x00"
    blob += struct.pack("<2I", len(ccomp), len(groups)) + \
        xor4(ccomp, count + len(ccomp) + len(groups))
    blob += cdata

    with open(out, "wb") as fh:
        fh.write(blob)
    print("упаковано: %s (%d байт, файлов %d)" % (out, len(blob), count))
    return 0


def cmd_info(a):
    for pak in a.paths:
        if not os.path.isfile(pak):
            print("%-46s ОШИБКА: не найден" % pak)
            continue
        info = parse(open(pak, "rb").read())
        layout = ("1 файл = 1 чанк" if len(info["chunks"]) <= info["count"] + 1
                  else "tfh+tfd")
        print("%-46s %9d б, файлов=%-4d чанков=%-4d имён=%-4d %s" %
              (os.path.basename(pak), os.path.getsize(pak), info["count"],
               len(info["chunks"]), len(info["names"]), layout))
    return 0


# ------------------------------------------------------------------ CLI

def main():
    ap = argparse.ArgumentParser(
        prog="pak.py", description="TPAK .pak: распаковка/упаковка (Star Conflict)")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("unpack", help="unpack <архив.pak> <папка выгрузки>")
    s.add_argument("pak", help="полный путь к .pak")
    s.add_argument("out", help="папка для распаковки")
    s.add_argument("--names", help="карта имён (по умолчанию <папка>/_names.map)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_unpack)

    s = sub.add_parser("pack", help="pack <папка>  (результат в ./packed)")
    s.add_argument("dir", help="папка для упаковки")
    s.add_argument("--names", help="карта имён (по умолчанию <папка>/_names.map)")
    s.add_argument("-o", "--out", help="явный путь к выходному .pak")
    s.set_defaults(func=cmd_pack)

    s = sub.add_parser("info", help="info <архив.pak>")
    s.add_argument("paths", nargs="+")
    s.set_defaults(func=cmd_info)

    a = ap.parse_args()
    if not getattr(a, "cmd", None):
        ap.print_help()
        return 0
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
