import sys, time, os
import numpy as np
sys.path.insert(0, "minimap")
import procmem, savedata
pid = procmem.find_pid(); P = procmem.Proc(pid)
lv = savedata.last_level(); V = savedata.vehicles(lv)
print(lv, len(V))
t = time.time(); hits = []; total = 0
V = [v for v in V if abs(v[2]) > 30 or abs(v[4]) > 30]; print([v[:2] for v in V])
targets = np.array([(v[2], v[3], v[4]) for v in V], np.float32)
for base, a in P.scan_chunks():
    total += a.size
    n = a.size // 4 * 4
    f = a[:n].view("<f4")
    for ti, (x, y, z) in enumerate(targets):
        idx = np.nonzero(np.abs(f[:-2] - x) < 0.5)[0]
        if idx.size == 0: continue
        ok = idx[(np.abs(f[idx + 1] - y) < 1.5) & (np.abs(f[idx + 2] - z) < 0.5)]
        for i in ok:
            hits.append((ti, base + int(i) * 4, f[i:i+3].tolist()))
print("scanned %.1f GB in %.1fs" % (total / 1e9, time.time() - t))
for ti, ad, v in hits: print(V[ti][0], V[ti][1], hex(ad), v)
