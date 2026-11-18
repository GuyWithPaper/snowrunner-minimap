"""Dev tool: find addresses holding the active truck position by driving and diffing memory."""
import sys, time, pickle
import numpy as np
sys.path.insert(0, "minimap"); sys.path.insert(0, "tools")
import procmem, gamectl

P = procmem.Proc(procmem.find_pid())
LIM = 1100


def snapshot():
    regs = {}
    for base, a in P.scan_chunks():
        n = a.size // 4 * 4
        f = a[:n].view("<f4")
        if f.size < 3:
            continue
        x, y, z = f[:-2], f[1:-1], f[2:]
        with np.errstate(invalid="ignore"):
            m = (np.abs(x) < LIM) & (np.abs(z) < LIM) & (np.abs(x) > 1) & (np.abs(z) > 1) & (y > -60) & (y < 700)
        idx = np.nonzero(m)[0]
        if idx.size:
            regs[base] = (idx.astype(np.int64), np.stack([x[idx], y[idx], z[idx]], 1))
    return regs


def reread(regs):
    out = {}
    for base, (idx, _) in regs.items():
        hi = int(idx.max()) * 4 + 12
        a = np.empty(hi, np.uint8)
        if not P.read_into(base, a):
            continue
        f = a[: hi // 4 * 4].view("<f4")
        out[base] = np.stack([f[idx], f[idx + 1], f[idx + 2]], 1)
    return out


def drive(sec, k="w"):
    gamectl.focus(); time.sleep(0.3)
    gamectl.key(k, True); time.sleep(sec); gamectl.key(k, False)


t = time.time()
s0 = snapshot()
print("candidates", sum(len(v[0]) for v in s0.values()), "%.1fs" % (time.time() - t))
drive(5)
time.sleep(1.5)
v1 = reread(s0)
keep = {}
for base, (idx, a0) in s0.items():
    if base not in v1:
        continue
    d = v1[base] - a0
    hd = np.hypot(d[:, 0], d[:, 2])
    m = (hd > 2) & (hd < 80) & (np.abs(d[:, 1]) < 10)
    if m.any():
        keep[base] = (idx[m], v1[base][m])
print("moved", sum(len(v[0]) for v in keep.values()))
time.sleep(2)
v2 = reread(keep)
keep2 = {}
for base, (idx, a1) in keep.items():
    if base not in v2:
        continue
    d = np.abs(v2[base] - a1).max(1)
    m = d < 0.3  # parked: should stay still
    if m.any():
        keep2[base] = (idx[m], v2[base][m])
print("still", sum(len(v[0]) for v in keep2.values()))
drive(4, "s")
time.sleep(1.5)
v3 = reread(keep2)
final = []
for base, (idx, a2) in keep2.items():
    if base not in v3:
        continue
    d = v3[base] - a2
    hd = np.hypot(d[:, 0], d[:, 2])
    for i in np.nonzero((hd > 1.5) & (hd < 80))[0]:
        final.append((base + int(idx[i]) * 4, a2[i].tolist(), v3[base][i].tolist()))
print("final", len(final))
for ad, a, b in final[:80]:
    print(hex(ad), [round(v, 2) for v in a], "->", [round(v, 2) for v in b])
pickle.dump(final, open("motion_final.pkl", "wb"))
