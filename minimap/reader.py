"""Live truck position reader for SnowRunner (read-only memory access).

Path, found by RTTI so it survives game updates:
  SnowRunner.exe static slot -> TRUCK_CONTROL (class .?AVTRUCK_CONTROL@combine@@)
  TRUCK_CONTROL + 0x8        -> the truck you are driving (null in garage/menus)
  truck + N                  -> hkpRigidBody (chassis); N found by RTTI scan of the truck object
  rigid body + 0x170         -> hkTransform: 3 rotation columns (x = truck forward) + translation
"""
import json, os, struct, time
import numpy as np
import procmem, rtti

import paths
CACHE = paths.CACHE
HEAP_LO, HEAP_HI = 0x10000000000, 0x7FF000000000


def _load_cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _save_cache(c):
    try:
        json.dump(c, open(CACHE, "w"), indent=1)
    except Exception:
        pass


class TruckState:
    __slots__ = ("x", "y", "z", "fx", "fz", "heading", "t")

    def __init__(self, pos, fwd, t):
        self.x, self.y, self.z = map(float, pos)
        self.fx, self.fz = float(fwd[0]), float(fwd[2])
        # heading: angle of forward vector on the map, 0 = north (-z), clockwise positive
        self.heading = float(np.degrees(np.arctan2(self.fx, -self.fz)))
        self.t = t


class Reader:
    def __init__(self, log=print):
        self.log = log
        self.P = None
        self.base = None
        self.slot = None       # absolute address of the static TRUCK_CONTROL pointer
        self.tc_vt = None
        self._rb_off = {}      # truck object -> rigid body offset
        self.status = "waiting for SnowRunner"

    # ---------- attach ----------
    def attach(self):
        pid = procmem.find_pid()
        if not pid:
            self.status = "waiting for SnowRunner"
            return False
        try:
            P = procmem.Proc(pid)
        except OSError as e:
            self.status = f"cannot open game process ({e})"
            return False
        base, size = P.module_base()
        if not base:
            self.status = "game still starting"
            P.close()
            return False
        exe_key = f"{size:x}"
        cache = _load_cache()
        img = rtti.read_module(P, base, size)
        vts = [v for v, off in rtti.vtables_for(img, base, "TRUCK_CONTROL@combine") if off == 0]
        if not vts:
            self.status = "TRUCK_CONTROL class not found (game changed?)"
            P.close()
            return False
        vt = vts[0]
        slot = None
        c = cache.get(exe_key)
        if c and P.u64(P.u64(base + c["slot_rva"]) or 0) == vt:
            slot = base + c["slot_rva"]
        if slot is None:
            slot = self._find_slot(P, img, base, vt)
            if slot is None:
                self.status = "loading level..."  # singleton not created yet
                P.close()
                return False
            cache[exe_key] = {"slot_rva": slot - base}
            _save_cache(cache)
        self.P, self.base, self.slot, self.tc_vt = P, base, slot, vt
        self.log(f"attached pid {pid}, TRUCK_CONTROL slot SnowRunner+0x{slot - base:X}")
        self.status = "attached"
        return True

    def _find_slot(self, P, img, base, vt):
        q = np.frombuffer(img[: len(img) // 8 * 8], "<u8")
        idx = np.nonzero((q >= HEAP_LO) & (q < HEAP_HI))[0]
        vals = q[idx]
        # an object pointer is 8/16-aligned; check each candidate's first qword against the vtable
        m = (vals & 0x7) == 0
        idx, vals = idx[m], vals[m]
        t = time.time()
        for i, v in zip(idx.tolist(), vals.tolist()):
            if P.u64(v) == vt:
                self.log(f"found TRUCK_CONTROL slot in {time.time() - t:.1f}s")
                return base + i * 8
        return None

    def detach(self):
        if self.P:
            self.P.close()
        self.P = None
        self._rb_off.clear()

    # ---------- per-frame ----------
    def _rigid_body(self, truck):
        off = self._rb_off.get(truck)
        if off is None:
            b = self.P.read(truck, 0x1000)
            if not b:
                return None
            q = np.frombuffer(b, "<u8")
            for j, v in enumerate(q.tolist()):
                if HEAP_LO <= v < HEAP_HI and rtti.class_name(self.P, v) == ".?AVhkpRigidBody@@":
                    off = j * 8
                    break
            if off is None:
                return None
            self._rb_off = {truck: off}  # forget old trucks
        return self.P.u64(truck + off)

    def read(self):
        """Return TruckState, or None when not driving. Raises ConnectionError if the game closed."""
        if not self.P:
            raise ConnectionError
        tc = self.P.u64(self.slot)
        if tc is None:
            if not self.P.alive():
                self.detach(); raise ConnectionError
            return None
        if not tc:
            return None
        truck = self.P.u64(tc + 8)
        if not truck or not (HEAP_LO <= truck < HEAP_HI):
            return None
        rb = self._rigid_body(truck)
        if not rb:
            return None
        b = self.P.read(rb + 0x170, 0x40)
        if not b or len(b) < 0x40:
            return None
        f = np.frombuffer(b, "<f4").reshape(4, 4)
        pos, fwd = f[3, :3], f[0, :3]
        if not np.all(np.isfinite(pos)) or np.abs(pos).max() > 1e5 or abs(np.linalg.norm(fwd) - 1) > 0.05:
            self._rb_off.clear()
            return None
        return TruckState(pos, fwd, time.time())


if __name__ == "__main__":
    r = Reader()
    while not r.attach():
        print(r.status); time.sleep(2)
    for _ in range(20):
        s = r.read()
        print(None if s is None else (round(s.x, 2), round(s.y, 2), round(s.z, 2), round(s.heading, 1)))
        time.sleep(0.25)
