"""Static object placements (plants, models, landmarks) from a level's [scene]/data_for_ssl.bin.

Record layout follows the community SnowrunnerMapConverter (github.com/skibidius/SnowrunnerMapConverter):
  <u16 len><name\\0><u32 count> then count x 28 bytes:
  f32 x, y, z, f32 scale, u8 rx, ry, rz (euler = (b-128)*180/127), 4 bytes flags, 4 bytes colour
"""
import math, re, struct, sys, zipfile, os, collections
import numpy as np

PAKS = r"C:\Program Files (x86)\Steam\steamapps\common\SnowRunner\preload\paks\client"
_pat = re.compile(rb"[\x20-\x7e]{3,240}")


def parse(data, limit=1500.0):
    groups = collections.defaultdict(list)
    for m in _pat.finditer(data):
        s, st, en = m.group(), m.start(), m.end()
        if st < 2 or en + 5 > len(data) or data[en] != 0:
            continue
        if struct.unpack_from("<H", data, st - 2)[0] != len(s) + 1:
            continue
        count = struct.unpack_from("<I", data, en + 1)[0]
        start = en + 5
        if not (1 <= count <= 300000) or start + count * 28 > len(data):
            continue
        text = s.decode("latin-1")
        if "/" not in text:
            continue
        rec = np.frombuffer(data, np.uint8, count * 28, start).reshape(count, 28)
        f = rec[:, :16].copy().view("<f4").reshape(count, 4)
        ok = np.isfinite(f).all(1) & (np.abs(f[:, 0]) <= limit) & (np.abs(f[:, 2]) <= limit) & (f[:, 1] > -250) & (f[:, 1] < 3000)
        if ok[: min(count, 24)].mean() < 0.8:
            continue
        ry = (rec[:, 17].astype(np.float32) - 128) * (180 / 127)
        sc = np.where((f[:, 3] > 0.01) & (f[:, 3] < 100), f[:, 3], 1.0)
        groups[text].append(np.c_[f[ok, 0], f[ok, 1], f[ok, 2], sc[ok], ry[ok]])
    return {k: np.concatenate(v) for k, v in groups.items()}


def load(level):
    with zipfile.ZipFile(os.path.join(PAKS, level + ".pak")) as z:
        return parse(z.read("[scene]/data_for_ssl.bin"))


if __name__ == "__main__":
    g = load(sys.argv[1] if len(sys.argv) > 1 else "level_us_09_02")
    cats = collections.Counter()
    for k, v in g.items():
        cats[k.split("/")[0]] += len(v)
    print(cats)
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))[:60]:
        print(len(v), k)
