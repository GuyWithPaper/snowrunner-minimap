"""Read SnowRunner save data: current level and vehicle transforms stored in sts_level_*.cfg."""
import glob, json, os, re, struct, zlib
import numpy as np

import paths
STEAM_USERDATA = paths.userdata_root() or r"C:\Program Files (x86)\Steam\userdata"
APPID = "1465360"


def save_dir():
    c = glob.glob(os.path.join(STEAM_USERDATA, "*", APPID, "remote"))
    # Epic / Microsoft Store builds keep saves under Documents
    docs = os.path.join(os.path.expanduser("~"), "Documents", "My Games", "SnowRunner", "base", "storage")
    c += [os.path.dirname(p) for p in glob.glob(os.path.join(docs, "*", "CompleteSave.cfg"))]
    c.sort(key=lambda p: os.path.getmtime(os.path.join(p, "CompleteSave.cfg")) if os.path.exists(os.path.join(p, "CompleteSave.cfg")) else 0)
    return c[-1] if c else None


def complete_save(d=None):
    d = d or save_dir()
    p = os.path.join(d, "CompleteSave.cfg")
    s = open(p, encoding="utf-8", errors="replace").read().rstrip("\x00")
    return json.loads(s)["CompleteSave"]["SslValue"]


def last_level(d=None):
    try:
        return complete_save(d).get("lastLoadedLevel")
    except Exception:
        return None


_NAME = re.compile(rb"([\x02-\x60])\x00([a-zA-Z0-9_]{2,96})\x00")


def vehicles(level, d=None):
    """Return list of (name, tag, x, y, z) for vehicles/trailers saved on a level."""
    d = d or save_dir()
    p = os.path.join(d, f"sts_{level}.cfg")
    if not os.path.exists(p):
        return []
    raw = open(p, "rb").read()
    data = zlib.decompress(raw[4:])
    out = []
    for m in _NAME.finditer(data):
        n = m.group(1)[0]
        if n != len(m.group(2)) + 1:
            continue
        name = m.group(2).decode()
        j = m.end()
        tag = ""
        m2 = _NAME.match(data, j)
        if m2 and m2.group(1)[0] == len(m2.group(2)) + 1:
            tag = m2.group(2).decode(); j = m2.end()
        if j + 64 > len(data):
            continue
        f = struct.unpack_from("<16f", data, j)
        if abs(f[15] - 1) > 1e-6 or any(abs(f[k]) > 1e-6 for k in (3, 7, 11)):
            continue
        rows = np.array(f[:12]).reshape(3, 4)[:, :3]
        if not np.allclose(np.linalg.norm(rows, axis=1), 1, atol=1e-3):
            continue
        x, y, z = f[12], f[13], f[14]
        if max(abs(x), abs(z)) > 1e5:
            continue
        if name.startswith("Bone") or (x == 0 and y == 0 and z == 0):
            continue
        out.append((name, tag, x, y, z))
    # the save repeats a vehicle under its type and its tag; keep one row per position
    seen, res = set(), []
    for v in out:
        k = (round(v[2], 2), round(v[4], 2))
        if k in seen:
            continue
        seen.add(k); res.append(v)
    return res


if __name__ == "__main__":
    lv = last_level(); print("last level:", lv)
    for v in vehicles(lv): print(v)
