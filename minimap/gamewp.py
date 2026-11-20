"""Read the waypoints you place on SnowRunner's own map, live from the running game (read-only).

Every map icon the game can show is a small record in its own heap block:

    +0   x, y, z        map units (world metres = 150 x map units)
    +12  flags word
    +16  u32            0x00010000 on the waypoints you placed (the route points), else 0
    +20  15.0, -1, -1, -1, -1
    +96  u32 state      2 = shown on the map screen, 4 = hidden

With the map open nearly every icon is shown, and the game may rebuild its records, so the list
only changes once the map is closed and it has stayed the same for a moment.

Finding the records needs one pass over the game's small heap blocks (a few seconds, background
thread). After that only the known records and newly allocated blocks are read, twice a second.
"""
import ctypes, math, struct, threading, time

import numpy as np

import procmem

SCALE = 150.0
TAIL = struct.pack("<5f", 15.0, -1.0, -1.0, -1.0, -1.0)
SMALL = 0x40000                 # records live in ~0.1 MB blocks
SHOWN = 2
WAYPOINT = 0x00010000
MAX_HUD = 8                     # more icons shown than this = the map screen is open
SETTLE = 1.0                    # seconds a new list must hold before it's used


def _valid(b):
    if len(b) < 100 or b[20:40] != TAIL:
        return None
    x, y, z = struct.unpack_from("<3f", b, 0)
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        return None
    if abs(x) > 20 or abs(z) > 20 or not -1 <= y < 5:
        return None
    return x, y, z


def scan_block(P, base, size, out):
    a = np.empty(size, np.uint8)
    if not P.read_into(base, a):
        return
    b = a.tobytes()
    i = b.find(TAIL)
    while i >= 0:
        if i >= 20 and i - 20 + 100 <= len(b) and _valid(b[i - 20:i + 80]) is not None:
            out.add(base + i - 20)
        i = b.find(TAIL, i + 4)


class Watcher:
    """Background reader. `points` = [(x, z)] world metres of the game's waypoints, `ready` once scanned."""

    def __init__(self, pid, log=print):
        self.pid, self.log = pid, log
        self.points, self.ready, self.map_open = [], False, False
        self.version = 0                  # bumps whenever the waypoint list changes
        self._stop = False
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def stop(self):
        self._stop = True

    def _run(self):
        try:
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -1)  # below normal
        except Exception:
            pass
        try:
            P = procmem.Proc(self.pid)
        except Exception as e:
            self.log("game waypoints: can't open process", e); return
        recs, known, last_enum = set(), set(), 0.0
        cand, cand_t = None, 0.0
        try:
            while not self._stop:
                now = time.time()
                if now - last_enum > 2.0:
                    t0 = now
                    regs = [(b, s) for b, s in P.regions(private_only=True) if s <= SMALL]
                    cur = {b for b, _ in regs}
                    for b, s in regs:
                        if b not in known:
                            scan_block(P, b, s, recs)
                        if self._stop:
                            return
                    known = cur
                    if not self.ready:
                        self.log(f"game waypoints: {len(recs)} map records found in {time.time() - t0:.1f}s")
                        self.ready = True
                    last_enum = time.time()
                n_shown, wps, dead = 0, [], []
                for a in recs:
                    b = P.read(a, 100)
                    v = _valid(b) if b else None
                    if v is None:
                        dead.append(a); continue
                    if not (v[0] or v[2]):                      # (0, 0) = unused record
                        continue
                    n_shown += struct.unpack_from("<I", b, 96)[0] == SHOWN
                    if struct.unpack_from("<I", b, 16)[0] == WAYPOINT:
                        wps.append((round(v[0] * SCALE, 2), round(v[2] * SCALE, 2)))
                for a in dead:
                    recs.discard(a)
                now = time.time()
                self.map_open = n_shown > MAX_HUD
                # the same waypoint can be drawn by two records: merge points within 1 m
                pts = []
                for p in sorted(wps):
                    if all(math.hypot(p[0] - q[0], p[1] - q[1]) > 1.0 for q in pts):
                        pts.append(p)
                if self.map_open or pts != cand:
                    cand, cand_t = (None if self.map_open else pts), now
                elif now - cand_t >= SETTLE and pts != self.points:
                    self.points = pts; self.version += 1
                time.sleep(0.5)
        except Exception as e:
            if not self._stop:
                self.log("game waypoints stopped:", e)
        finally:
            try:
                P.close()
            except Exception:
                pass


def order_from(start, pts):
    """Visit order for several waypoints: nearest first, then nearest to that, and so on."""
    left, out, cur = list(pts), [], start
    while left:
        k = min(range(len(left)), key=lambda i: (left[i][0] - cur[0]) ** 2 + (left[i][1] - cur[1]) ** 2)
        cur = left.pop(k); out.append(cur)
    return out
