import sys
import numpy as np
sys.path.insert(0, "minimap")
import procmem
P = procmem.Proc(procmem.find_pid())
base, _ = P.module_base()
ref = np.array([float(v) for v in sys.argv[1:4]]) if len(sys.argv) > 3 else P.f32(0x2577afa0340 + 0, 3).astype(float)
print("truck ~", ref)
tc = P.u64(base + 0x2A93BF8)
SPAN = 0x600
def is_ptr(v): return 0x10000000000 <= v < 0x7FF000000000
found = []
def visit(addr, path, depth, seen):
    b = P.read(addr, SPAN)
    if not b or len(b) < SPAN: return
    f = np.frombuffer(b, "<f4")
    # matrix: translation at f[i:i+3], f[i+3]==1, rows above
    for i in range(12, len(f) - 3):
        if abs(f[i] - ref[0]) < 3 and abs(f[i + 2] - ref[2]) < 3 and abs(f[i + 1] - ref[1]) < 5:
            kind = "vec"
            if abs(f[i + 3] - 1) < 1e-4 and all(abs(f[i - 12 + k * 4 + 3]) < 1e-4 for k in range(3)):
                kind = "mat44"
            found.append((path + [i * 4], kind, f[i:i + 3].round(2).tolist()))
    if depth == 0: return
    q = np.frombuffer(b, "<u8")
    for j, v in enumerate(q):
        v = int(v)
        if is_ptr(v) and v not in seen:
            seen.add(v)
            visit(v, path + [j * 8], depth - 1, seen)
visit(tc, [], 3, {tc})
for p, k, v in found[:60]:
    print("tc" + "".join("->+%X" % o for o in p[:-1]), "+%X" % p[-1], k, v)
print(len(found))
