import sys, time, pickle, threading
import numpy as np
sys.path.insert(0, "minimap"); sys.path.insert(0, "tools")
import procmem, gamectl
P = procmem.Proc(procmem.find_pid())
F = pickle.load(open("motion_final.pkl", "rb"))
F = [f for f in F if abs(f[1][0]) > 100 and abs(f[1][2]) > 100 and 0 < f[1][1] < 200]
addrs = [f[0] for f in F]
def sample():
    out = np.full((len(addrs), 3), np.nan, np.float32)
    for i, a in enumerate(addrs):
        v = P.f32(a, 3)
        if v is not None: out[i] = v
    return out
key = sys.argv[1] if len(sys.argv) > 1 else "w"
gamectl.focus(); time.sleep(0.3)
S = []; T = []
gamectl.key(key, True); t0 = time.time()
while time.time() - t0 < 6:
    S.append(sample()); T.append(time.time() - t0)
gamectl.key(key, False)
t1 = time.time()
while time.time() - t1 < 2:
    S.append(sample()); T.append(time.time() - t0)
S = np.array(S)  # (t, n, 3)
print("samples", len(S), "dt %.3f" % (T[-1] / len(T)))
# consensus = median over candidates of (pos - pos0)
d = S - S[0]
cons = np.nanmedian(d, axis=1)
err = np.sqrt(np.nanmean(np.sum((d - cons[:, None, :]) ** 2, axis=2)[:, :], axis=0))
travel = np.hypot(cons[-1, 0], cons[-1, 2]); print("consensus travel %.1f m" % travel)
order = np.argsort(err)
res = []
for i in order[:40]:
    print(hex(addrs[i]), "err %.3f" % err[i], S[0, i].round(2), "->", S[-1, i].round(2))
    res.append((addrs[i], float(err[i])))
pickle.dump(res, open("track_best.pkl", "wb"))
