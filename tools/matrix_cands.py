import sys, pickle
import numpy as np
sys.path.insert(0, "minimap")
import procmem
P = procmem.Proc(procmem.find_pid())
F = pickle.load(open("motion_final.pkl", "rb"))
F = [f for f in F if abs(f[1][0]) > 100 and abs(f[1][2]) > 100 and 0 < f[1][1] < 200]
for ad, a, b in F:
    m = P.f32(ad - 48, 16)
    if m is None: continue
    rows = m[:12].reshape(3, 4)
    if abs(m[15] - 1) < 1e-4 and np.all(np.abs(rows[:, 3]) < 1e-4) and np.allclose(np.linalg.norm(rows[:, :3], axis=1), 1, atol=1e-2):
        print("4x4", hex(ad), m[12:15].round(2), "fwd", rows[2, :3].round(2))
        continue
    m = P.f32(ad - 36, 12)  # 3x3 rot + pos (Mat34 style)
    if m is not None:
        r = m[:9].reshape(3, 3)
        if np.allclose(np.linalg.norm(r, axis=1), 1, atol=1e-2) and abs(np.linalg.det(r) - 1) < 0.02:
            print("3x4", hex(ad), m[9:12].round(2))
