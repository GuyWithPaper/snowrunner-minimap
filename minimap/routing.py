"""Route planning over a region, using only ground the player has uncovered (fog of war).

Per region a grid (CELL metres) is built once from the game's own data and cached:
  road   0..1  roads as the game's own map draws them (grey-blue lines on the baked map); terrain blend
               map road materials only for regions without a baked map
  mud    0..1  deformable-mud depth (extrude map): the level's off-road trails and mud fields
  slope        rise/run from the heightmap
  water        water depth in metres (water level map minus terrain), 0 where dry
  block        trees, buildings, cliffs; bridges clear water and count as road
A route is an A* search on a cost per metre derived from those layers; cells still under fog (grey or
black on the map) are impassable.
If the waypoint itself is unexplored (or unreachable), the route goes to the closest explored point.
"""
import heapq, io, json, math, os, re, struct, time, zipfile
from collections import Counter
import numpy as np
from PIL import Image, ImageDraw

import maprender as M
import paths

CELL = 3.0
VERSION = 3
ROAD_RE = re.compile(r"(road|asphalt|concrete|tile|pavement|path)")


def grid_path(level):
    return os.path.join(paths.MAPS, f"{level}.route.npz")


# ---------------------------------------------------------------- build
def build_grid(level, log=print):
    t0 = time.time()
    z = zipfile.ZipFile(os.path.join(M.PAKS, level + ".pak"))
    names = set(z.namelist())
    base = f"[textures]/pct/{level}_"
    W, H, _ = M.pct_info(z, base + "height_map.pct")
    half_x, half_z = (W - 1) * M.M_PER_TEXEL / 2, (H - 1) * M.M_PER_TEXEL / 2
    gw, gh = int(math.ceil(2 * half_x / CELL)), int(math.ceil(2 * half_z / CELL))
    size = (gw, gh)
    hm = np.frombuffer(M.tail(z, base + "height_map.pct", W * H * 2), "<u2").reshape(H, W).astype(np.float32)
    stg = z.read("[scene]/data.stg") if "[scene]/data.stg" in names else b""
    ssl = z.read("[scene]/data_for_ssl.bin") if "[scene]/data_for_ssl.bin" in names else b""
    lmk = M.ssl_landmarks(ssl, max(half_x, half_z) + 50) if ssl else {}
    gp = [v[:, :3] for k, v in lmk.items() if M.plant_style(k)]
    k_h, b_h = M.height_scale(hm, np.concatenate(gp) if gp else np.zeros((0, 3)))
    hgt = M.resize_f(hm * k_h + b_h, size, Image.BILINEAR)

    # slope: steepest of the two axes, metres per metre
    gz_, gx_ = np.gradient(hgt, CELL)
    slope = np.hypot(gx_, gz_).astype(np.float32)

    baked = M.baked_tiles(stg, W, H) if stg else None
    road = baked_roads(baked, half_x, half_z, size) if baked is not None else None
    if road is not None:
        log(f"  route: roads from the game's map ({(road > 0.5).mean() * 100:.1f}% of cells)")
    else:
        road = blend_roads(z, names, stg, base, W, H, size, log)

    # mud
    mud = np.zeros((gh, gw), np.float32)
    en = base + "extrude_map.pct"
    if en in names:
        ew, eh, _ = M.pct_info(z, en)
        e = np.frombuffer(M.tail(z, en, ew * eh), np.uint8).reshape(eh, ew).astype(np.float32) / 255
        mud = M.resize_f(e, size, Image.BOX)

    # water depth (same logic as the map renderer)
    water = np.zeros((gh, gw), np.float32)
    wn = base + "water_height_map.pct"
    if wn in names:
        ww, wh, _ = M.pct_info(z, wn)
        wl = np.frombuffer(M.tail(z, wn, ww * wh * 4), "<f4").reshape(wh, ww).astype(np.float32)
        ok = np.isfinite(wl) & (np.abs(wl) < 1e5)
        if ok.any():
            lv = np.where(ok, wl, -1e9).astype(np.float32)
            lvl = np.asarray(Image.fromarray(lv).transform(size, Image.EXTENT, (0, 0, ww - 1, wh - 1), Image.NEAREST), np.float32)
            water = np.clip(lvl - hgt, 0, 50).astype(np.float32)
            if baked is not None:
                b = baked[..., :3].astype(np.float32)
                wet = ((b[..., 2] > b[..., 0] + 18) & (b[..., 2] > b[..., 1] + 4)).astype(np.uint8) * 255
                wm = Image.fromarray(wet).crop((0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75))))
                water = np.where(np.asarray(wm.resize(size, Image.BILINEAR)) > 100, water, 0).astype(np.float32)

    # obstacles: trees (landmark trees are the solid ones) and buildings (game footprints)
    block = Image.new("L", size, 0); bd = ImageDraw.Draw(block)
    bridge = Image.new("L", size, 0); brd = ImageDraw.Draw(bridge)
    tree_pts = []
    for nm, arr in lmk.items():
        st = M.plant_style(nm)
        mt = M.mesh_triangles(nm)
        if st:
            for p in arr:
                tree_pts.append((p[0], p[2]))
                cx, cz = (p[0] + half_x) / CELL, (p[2] + half_z) / CELL
                if 0 <= int(cz) < gh and 0 <= int(cx) < gw and road[int(cz), int(cx)] > 0.5:
                    continue                      # a plant on the road (grass, saplings): drivable
                r = 0.45
                bd.ellipse((cx - r, cz - r, cx + r, cz + r), fill=255)
            continue
        if mt is None:
            continue
        tris = mt[0]
        ext = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
        is_bridge = "bridge" in nm.lower()
        if not is_bridge and (ext[1] < 1.2 or ext[0] * ext[2] < 4 or M.map_clutter(nm, ext)):
            continue
        if not is_bridge:
            # only what stands on the ground blocks a truck (walls, trunks, rocks), not roofs or canopies
            # seen from above: keep triangles reaching down to within 1.5 m of the model's base
            y0 = tris[..., 1].min()
            tris = tris[tris[..., 1].min(axis=1) < y0 + 1.5]
            if not len(tris):
                continue
        for p in arr:
            R = M.quat_matrix(p[4:8]) if len(p) >= 8 else np.eye(3, dtype=np.float32)
            w = (tris.reshape(-1, 3) @ R.T * p[3] + p[:3]).reshape(-1, 3, 3)
            for tri in w:
                pts = [((x + half_x) / CELL, (zz + half_z) / CELL) for x, _, zz in tri]
                (brd if is_bridge else bd).polygon(pts, fill=255)
    log(f"  route: trees and meshes block {(np.asarray(block) > 0).mean() * 100:.1f}%")
    # prefab buildings without landmark meshes: the game's own baked footprints (dark occlusion, not trees)
    try:
        if baked is not None:
            a = baked[..., 3].astype(np.float32) / 255
            tm = Image.new("L", (a.shape[1], a.shape[0]), 0); td = ImageDraw.Draw(tm)
            for x, zz in tree_pts:
                cx, cz = (x + half_x) / 0.75, (zz + half_z) / 0.75
                td.ellipse((cx - 5, cz - 5, cx + 5, cz + 5), fill=255)
            fp = (a < 0.42) & ~(np.asarray(tm) > 0)
            fpi = Image.fromarray(fp.astype(np.uint8) * 255).crop((0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75))))
            fpg = np.asarray(fpi.resize(size, Image.BOX)) > 140
            log(f"  route: footprints block {fpg.mean() * 100:.1f}%")
            block = Image.fromarray(np.maximum(np.asarray(block), fpg.astype(np.uint8) * 255))
    except Exception as e:
        log(f"  route: footprints unavailable ({e})")
    blk = np.asarray(block) > 0
    brg = np.asarray(bridge) > 0
    # bridges: drivable, no water, count as road
    blk &= ~brg
    water[brg] = 0
    road[brg] = 1.0

    np.savez_compressed(grid_path(level), road=road.astype(np.float16), mud=mud.astype(np.float16),
                        slope=slope.astype(np.float16), water=water.astype(np.float16), block=blk,
                        meta=np.array([CELL, half_x, half_z, VERSION], np.float64))
    log(f"  route grid {gw}x{gh} built in {time.time() - t0:.1f}s (roads {road.mean() * 100:.1f}%, blocked {blk.mean() * 100:.1f}%)")


def baked_roads(baked, half_x, half_z, size):
    """Roads, streets and yards as the game's own map draws them: grey-blue on the sandy terrain colour.
    Shorelines look similar, so anything right next to water is left out."""
    from PIL import ImageFilter
    crop = (0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75)))
    c = np.asarray(Image.fromarray(baked[..., :3]).crop(crop), np.float32)
    R, G, B = c[..., 0], c[..., 1], c[..., 2]
    lum = 0.3 * R + 0.59 * G + 0.11 * B
    wet = (B > R + 18) & (B > G + 4) & (lum < 150)
    near_wet = np.asarray(Image.fromarray(wet.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(9))) > 0
    road = (R - B < 40) & (B - R < 60) & (lum > 120) & ~near_wet
    # close small gaps (bridges of shade, faded stretches), then sample at the grid size keeping thin roads
    im = Image.fromarray(road.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    gw, gh = size
    big = np.asarray(im.resize((gw * 2, gh * 2), Image.BILINEAR), np.float32) / 255
    return big.reshape(gh, 2, gw, 2).max(axis=(1, 3)).astype(np.float32)


def blend_roads(z, names, stg, base, W, H, size, log=print):
    """Fallback: road materials painted into the terrain blend map."""
    gw, gh = size
    road = np.zeros((gh, gw), np.float32)
    bn = base + "blend_map__cmp.pct"
    try:
        import terrain
        sets = terrain.material_sets(stg)
        if sets and bn in names:
            common = Counter(tuple(s) for s in sets).most_common(1)[0][0]
            bw, bh, _ = M.pct_info(z, bn)
            pw, ph = (bw + 3) // 4 * 4, (bh + 3) // 4 * 4
            bl = Image.open(io.BytesIO(M.dds_wrap(b"DX10", M.tail(z, bn, pw * ph), pw, ph, 98))).convert("RGB")
            bl = bl.crop(terrain.terrain_crop(bw, bh, W, H))
            big = np.asarray(bl.resize((gw * 3, gh * 3), Image.BILINEAR), np.float32) / 255
            big = big.reshape(gh, 3, gw, 3, 3).max(axis=(1, 3))
            for li, (nm, _) in enumerate(common[1:]):
                if ROAD_RE.search(nm):
                    road = np.maximum(road, big[..., li])
    except Exception as e:
        log(f"  route: road layer unavailable ({e})")
    return road


class Grid:
    def __init__(self, level):
        d = np.load(grid_path(level))
        self.cell, self.half_x, self.half_z, ver = d["meta"]
        if int(ver) != VERSION:
            raise ValueError("old route grid")
        self.road = d["road"].astype(np.float32); self.mud = d["mud"].astype(np.float32)
        self.slope = d["slope"].astype(np.float32); self.water = d["water"].astype(np.float32)
        self.block = d["block"]
        self.h, self.w = self.road.shape

    def cell_of(self, x, z):
        j = int((x + self.half_x) / self.cell); i = int((z + self.half_z) / self.cell)
        return min(max(i, 0), self.h - 1), min(max(j, 0), self.w - 1)

    def world(self, i, j):
        return (-self.half_x + (j + 0.5) * self.cell, -self.half_z + (i + 0.5) * self.cell)


STYLES = {
    # Roads are cheapest, then the game's off-road trails (its mud tracks); open terrain costs a lot more and
    # woods more again, so the route only leaves roads and trails for a short, clear shortcut.
    #            trail, open terrain, slope, water, forest
    "balanced": (1.2, 8.0, 1.0, 6.0, 6.0),
    "roads":    (5.0, 16.0, 1.2, 10.0, 10.0),
    "shortest": (0.5, 1.5, 0.5, 2.5, 2.0),
}


def box_mean(a, r):
    """Mean over a (2r+1)^2 window (summed-area table, edges clamped)."""
    p = np.pad(a.astype(np.float64), r + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return (s / (k * k))[: a.shape[0], : a.shape[1]].astype(np.float32)


def cost_grid(g, style="balanced"):
    k_trail, k_off, k_slope, k_w, k_for = STYLES.get(style, STYLES["balanced"])
    if getattr(g, "forest", None) is None:
        g.forest = box_mean(g.block, 3)            # share of blocked cells within ~10 m: 0 open field .. 1 woods
    trail = np.clip((g.mud - 0.12) / 0.25, 0, 1) * (1.0 - g.road)     # trails are the level's mud tracks
    off = 1.0 - np.maximum(g.road, trail)
    c = 1.0 + trail * k_trail + off * (k_off + k_for * np.clip(g.forest * 1.6, 0, 1) ** 1.5)
    c *= 1.0 + k_slope * (g.slope / 0.3) ** 2
    c += np.where(g.water > 0.05, k_w * np.minimum(g.water, 1.5), 0)
    blocked = g.block | (g.slope > 0.75) | (g.water > 1.4)
    return np.where(blocked, np.inf, c).astype(np.float32)


def fog_mask(g, fog, driven=None, radius=40.0):
    """True where the player has uncovered the map. The fog file holds 255 for uncovered ground, 128 for the grey
    still-fogged band and 0 for black; only 255 counts."""
    if fog is None:
        return np.ones((g.h, g.w), bool)
    m = _fog_cells(g, fog)
    if driven:
        # the game writes its fog file only when it saves: ground you've driven since then is uncovered too
        im = Image.new("L", (g.w, g.h), 0); d = ImageDraw.Draw(im)
        r = radius / g.cell
        for line in driven:
            pts = [((x + g.half_x) / g.cell, (z + g.half_z) / g.cell) for x, z in line]
            if len(pts) > 1:
                d.line(pts, fill=255, width=int(2 * r))
            for cx, cz in pts[:: max(1, len(pts) // 400)] + pts[-1:]:
                d.ellipse((cx - r, cz - r, cx + r, cz + r), fill=255)
        m |= np.asarray(im) > 0
    return m


def _fog_cells(g, fog):
    fg, (ox, oz, sx, sz) = fog
    fh, fw = fg.shape
    js = np.arange(g.w); is_ = np.arange(g.h)
    xs = -g.half_x + (js + 0.5) * g.cell; zs = -g.half_z + (is_ + 0.5) * g.cell
    u = np.clip(((xs - ox) / sx * fw).astype(int), 0, fw - 1); v = np.clip(((zs - oz) / sz * fh).astype(int), 0, fh - 1)
    return fg[v[:, None], u[None, :]] >= 192


def find_route(g, cost, open_mask, start, goal, max_expand=900_000):
    """A* on 8-connected cells. Returns dict(points, length_m, reached, goal_used) or None."""
    h, w = g.h, g.w
    cst = np.where(open_mask, cost, np.inf)
    si, sj = g.cell_of(*start)
    gi, gj = g.cell_of(*goal)
    # the truck can always drive out of where it stands (parked between trees, at a fog edge...)
    r = 6
    win = cst[max(si - r, 0):si + r + 1, max(sj - r, 0):sj + r + 1]
    win[~np.isfinite(win)] = 6.0
    reached_goal_cell = bool(np.isfinite(cst[gi, gj]))
    flat = cst.ravel().tolist()
    INF = float("inf")
    cellm = float(g.cell)
    s = si * w + sj; t = gi * w + gj
    minc = float(np.nanmin(cost[np.isfinite(cost)])) if np.isfinite(cost).any() else 1.0
    hw = 1.25 * minc * cellm                       # weighted heuristic: fast, near-optimal
    def hfun(n):
        i, j = divmod(n, w)
        di, dj = abs(i - gi), abs(j - gj)
        return hw * (max(di, dj) + 0.4142 * min(di, dj))
    dist = {s: 0.0}; prev = {}
    pq = [(hfun(s), s)]
    best = (hfun(s), s)
    nbr = ((-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0), (-1, -1, 1.4142), (-1, 1, 1.4142), (1, -1, 1.4142), (1, 1, 1.4142))
    closed = set(); n_exp = 0
    while pq:
        f, n = heapq.heappop(pq)
        if n in closed:
            continue
        closed.add(n); n_exp += 1
        if n == t:
            best = (0.0, n); break
        hn = f - dist[n]
        if hn < best[0]:
            best = (hn, n)
        if n_exp > max_expand:
            break
        i, j = divmod(n, w); dn = dist[n]; cn = flat[n]
        for di, dj, step in nbr:
            ii, jj = i + di, j + dj
            if ii < 0 or jj < 0 or ii >= h or jj >= w:
                continue
            m = ii * w + jj
            cm = flat[m]
            if cm == INF:
                continue
            if di and dj and (flat[i * w + jj] == INF or flat[ii * w + j] == INF):
                continue                              # no corner cutting past obstacles
            nd = dn + 0.5 * (cn + cm) * step * cellm
            if nd < dist.get(m, INF):
                dist[m] = nd; prev[m] = n
                heapq.heappush(pq, (nd + hfun(m), m))
    end = best[1]
    path = [end]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    if len(path) < 2:
        return None
    pts = [g.world(*divmod(n, w)) for n in path]
    pts = smooth(simplify(pts, cst, g))
    length = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return {"points": pts, "length": length, "reached": end == t and reached_goal_cell,
            "goal_used": g.world(*divmod(end, w)), "expanded": n_exp}


def simplify(pts, cst, g, tol=1.2):
    """Drop points while the straight segment stays on cells of similar cost (keeps roads, avoids obstacles)."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]; k = 0
    def ok(a, b):
        n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / (g.cell * 0.5)) + 1
        base = None
        for s in range(n + 1):
            x = a[0] + (b[0] - a[0]) * s / n; z = a[1] + (b[1] - a[1]) * s / n
            i, j = g.cell_of(x, z); c = cst[i, j]
            if not np.isfinite(c):
                return False
            base = c if base is None else base
            if c > base * tol + 0.3:
                return False
        return True
    while k < len(pts) - 1:
        j = min(len(pts) - 1, k + 40)
        while j > k + 1 and not ok(pts[k], pts[j]):
            j -= 1
        out.append(pts[j]); k = j
    return out


def smooth(pts, it=2):
    """Chaikin corner cutting for a natural line."""
    for _ in range(it):
        if len(pts) < 3:
            return pts
        q = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            q.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            q.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        q.append(pts[-1]); pts = q
    return pts


if __name__ == "__main__":
    import sys
    build_grid(sys.argv[1] if len(sys.argv) > 1 else "level_us_09_01")
