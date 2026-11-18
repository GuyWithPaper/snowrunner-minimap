"""Level names, map detection from terrain height, and the player's fog of war from the save."""
import glob, json, os, re, struct, zipfile, zlib
import numpy as np

import maprender

HERE = os.path.dirname(os.path.abspath(__file__))
import paths
MAPS = paths.MAPS
_names = None


def display_name(level):
    global _names
    if _names is None:
        _names = {}
        try:
            cache = os.path.join(MAPS, "names.json")
            if os.path.exists(cache):
                _names = json.load(open(cache))
            else:
                z = zipfile.ZipFile(os.path.join(maprender.PAKS, "initial.pak"))
                t = z.read("[strings]/strings_english.str").decode("utf-16-le", "ignore")
                for m in re.finditer(r'^(level_[a-z0-9_]+)_NAME\t+"([^"]*)"', t, re.M):
                    _names[m.group(1)] = m.group(2)
                os.makedirs(MAPS, exist_ok=True)
                json.dump(_names, open(cache, "w"), indent=1)
        except Exception:
            pass
    n = _names.get(level)
    if not n:
        # e.g. level_us_01_04_new -> level_us_01_04
        n = _names.get(re.sub(r"_new$|_crop$", "", level))
    return n or level.replace("level_", "").upper()


class Detector:
    """Pick the level whose terrain matches the truck's height at several positions."""

    def __init__(self):
        p = os.path.join(MAPS, "levels.json")
        if not os.path.exists(p):
            maprender.build_index(log=lambda *a: None)
        self.info = json.load(open(p))
        self.grids = {}
        self.samples = []
        self.level = None

    def grid(self, lv):
        g = self.grids.get(lv)
        if g is None:
            g = np.load(os.path.join(maprender.HEIGHTS, lv + ".npy")).astype(np.float32)
            self.grids[lv] = g
        return g

    def terrain(self, lv, x, z):
        i = self.info[lv]; g = self.grid(lv); gh, gw = g.shape
        u = (x / i["half_x"] + 1) / 2 * (gw - 1); v = (z / i["half_z"] + 1) / 2 * (gh - 1)
        if not (0 <= u <= gw - 1 and 0 <= v <= gh - 1):
            return None
        u0, v0 = int(u), int(v); u1, v1 = min(u0 + 1, gw - 1), min(v0 + 1, gh - 1); fu, fv = u - u0, v - v0
        return (g[v0, u0] * (1 - fu) + g[v0, u1] * fu) * (1 - fv) + (g[v1, u0] * (1 - fu) + g[v1, u1] * fu) * fv

    def score(self, lv, samples):
        r = []
        for x, y, z in samples:
            t = self.terrain(lv, x, z)
            if t is None:
                return 1e9
            r.append(y - t)
        r = np.array(r)
        # chassis sits ~0.5..3 m above the ground; penalise spread
        return float(np.median(np.abs(r - 1.3)) + 0.5 * np.std(r))

    def add(self, x, y, z):
        if not self.samples or np.hypot(x - self.samples[-1][0], z - self.samples[-1][2]) > 15:
            self.samples.append((x, y, z)); self.samples = self.samples[-12:]

    def reset(self):
        self.samples = []; self.level = None

    def detect(self, x, y, z, hint=None):
        """Return the best level for the current truck position (keeps current one while it fits)."""
        self.add(x, y, z)
        smp = self.samples[-8:]
        if self.level and self.score(self.level, smp) < 2.5:
            return self.level
        order = sorted(self.info, key=lambda lv: (lv != hint, lv))
        scores = sorted((self.score(lv, smp), lv) for lv in order)
        best, lv = scores[0]
        second = scores[1][0] if len(scores) > 1 else 1e9
        if best < 3.0 and (second - best > 1.0 or len(smp) >= 4):
            self.level = lv
        return self.level


def fog(level, save_dir):
    """-> (grid uint8 HxW: 0 hidden, 128 edge, 255 uncovered, (ox, oz, sx, sz)) or None."""
    if not save_dir:
        return None
    p = os.path.join(save_dir, f"fog_{level}.cfg")
    if not os.path.exists(p):
        return None
    try:
        d = zlib.decompress(open(p, "rb").read()[4:])
        w, h = struct.unpack_from("<II", d, 0)
        g = np.frombuffer(d, np.uint8, w * h, 8).reshape(h, w)
        ox, oz, sx, sz = struct.unpack_from("<4f", d, 8 + w * h)
        return g, (ox, oz, sx, sz)
    except Exception:
        return None


# ---------------------------------------------------------------- map markers (garages, fuel, watchtowers, tasks...)
_mk_pat = re.compile(rb"([\x03-\x80])\x00([A-Za-z0-9_]{3,120})\x00([\x03-\x40])\x00([A-Za-z0-9_]{2,60}?)30\x00")
_strings = None


def ui_string(key):
    global _strings
    if _strings is None:
        _strings = {}
        try:
            z = zipfile.ZipFile(os.path.join(maprender.PAKS, "initial.pak"))
            t = z.read("[strings]/strings_english.str").decode("utf-16-le", "ignore")
            for m in re.finditer(r'^([A-Za-z0-9_]+)\t+"([^"]*)"', t, re.M):
                _strings[m.group(1)] = m.group(2)
        except Exception:
            pass
    return _strings.get(key) or _strings.get(key.upper()) or ""


def markers(level):
    """[{id, icon, name, x, z}] from [scene]/data_for_ssl.bin (cached as JSON)."""
    cache = os.path.join(MAPS, level + ".markers.json")
    if os.path.exists(cache):
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    out = []
    try:
        z = zipfile.ZipFile(os.path.join(maprender.PAKS, level + ".pak"))
        b = z.read("[scene]/data_for_ssl.bin")
        seen = set()
        for m in _mk_pat.finditer(b):
            if m.group(1)[0] != len(m.group(2)) + 1 or m.group(3)[0] != len(m.group(4)) + 3:
                continue
            st = m.start()
            x, zz = struct.unpack_from("<2f", b, st - 20)
            p = m.end()
            L = struct.unpack_from("<H", b, p)[0]; icon = b[p + 2:p + 1 + L].decode(errors="ignore"); p += 2 + L
            L = struct.unpack_from("<H", b, p)[0]; key = b[p + 2:p + 1 + L].decode(errors="ignore")
            ident = m.group(2).decode()
            if ident in seen or not (abs(x) < 3000 and abs(zz) < 3000):
                continue
            seen.add(ident)
            out.append({"id": ident, "icon": icon, "name": ui_string(key) or ui_string(ident + "_NAME") or "",
                        "x": float(x), "z": float(zz)})
        json.dump(out, open(cache, "w", encoding="utf-8"), indent=0)
    except Exception:
        pass
    return out
