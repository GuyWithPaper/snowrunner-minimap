"""Find each level's world extent/orientation by matching saved vehicle heights to the heightmap."""
import os, sys, zipfile, json, itertools
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "minimap"))
import savedata
PAKS = r"C:\Program Files (x86)\Steam\steamapps\common\SnowRunner\preload\paks\client"

def heightmap(level):
    z = zipfile.ZipFile(os.path.join(PAKS, level + ".pak"))
    n = f"[textures]/pct/{level}_height_map.pct"
    import struct
    w, h = struct.unpack_from("<II", z.read(n + "_header"), 0x10)
    return np.frombuffer(z.read(n)[-w*h*2:], "<u2").reshape(h, w).astype(np.float32)

def fit(level, pts):
    hm = heightmap(level); W = hm.shape[0]
    P = np.array([(p[2], p[3], p[4]) for p in pts])
    best = None
    for S in np.arange(250, 3001, 10):  # half-size metres
        for flipx, flipz, swap in itertools.product([1, -1], [1, -1], [0, 1]):
            a, b = P[:, 0] * flipx, P[:, 2] * flipz
            if swap: a, b = b, a
            u = (a / S + 1) / 2 * (W - 1); v = (b / S + 1) / 2 * (W - 1)
            ok = (u >= 0) & (u < W) & (v >= 0) & (v < W)
            if ok.sum() < len(P) * 0.95 or ok.sum() < 4: continue
            hv = hm[v[ok].astype(int), u[ok].astype(int)]
            y = P[ok, 1]
            A = np.vstack([hv, np.ones_like(hv)]).T
            coef, res, *_ = np.linalg.lstsq(A, y, rcond=None)
            err = np.sqrt(np.mean((A @ coef - y) ** 2))
            if best is None or err < best[0]:
                best = (err, S, flipx, flipz, swap, coef.tolist(), int(ok.sum()))
    return best

if __name__ == "__main__":
    d = savedata.save_dir()
    for f in sorted(os.listdir(d)):
        if f.startswith("sts_level_"):
            lv = f[4:-4]
            pts = savedata.vehicles(lv, d)
            if len(pts) >= 4:
                print(lv, len(pts), fit(lv, pts))
