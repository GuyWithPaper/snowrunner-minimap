import sys, time
import numpy as np
sys.path.insert(0, "minimap")
import procmem, rtti
P = procmem.Proc(procmem.find_pid())
base, size = P.module_base()
print("module", hex(base), hex(size))
t = time.time(); img = rtti.read_module(P, base, size); print("read %.1fs" % (time.time() - t))
for cls in ["TRUCK_CONTROL@combine", "DRIVE_CAMERA@combine"]:
    vts = rtti.vtables_for(img, base, cls)
    print(cls, [(hex(v), o) for v, o in vts])
    for vt, o in vts:
        if o: continue
        inst = rtti.find_instances(P, vt)
        print("  instances", [hex(i) for i in inst])
        for ins in inst:
            refs = rtti.find_static_refs(P, img, base, ins)
            print("   ", hex(ins), "static refs (rva)", [hex(r) for r in refs])
            q = np.frombuffer(P.read(ins, 0x80), "<u8")
            print("    qwords", [hex(int(x)) for x in q])
