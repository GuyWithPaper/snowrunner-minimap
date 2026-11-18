"""Render a SnowRunner level map that looks like the in-game navigation map, from the game's own files.

The game draws its map by rendering the 3D level top-down with the `navigation_map` daytime preset
(sun from (1,-1,0), strong ambient) and the `env_lut_minimap` colour LUT. This module rebuilds that:

  terrain    *_height_map.pct            uint16, 0.5 m/texel, centred on the origin
  materials  *_blend_map__cmp.pct (BC7)  RGB = weights of terrain layers 0..2 over the base layer;
             layer names from [scene]/data.stg, albedo from editor.pak tiles_<name>__d_a.dds
  mud        *_extrude_map.pct           deformable-mud mask
  water      *_water_height_map.pct      low-res float16 water levels
  plants     [scene]/data.stg            32-byte instances: u16 1, u16 type, f32 x y z, f32 scale, ...
             [scene]/data_for_ssl.bin    big "landmark" trees and rocks (28-byte records)
  colours    editor.pak billboard_plants_<name>__d_a.dds, env_lut_minimap__vol_uncmp.dds

Output image: +x to the right, +z down (north up, matching the in-game map).
"""
import io, json, os, re, struct, zipfile, time
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import paths
PAKS = paths.paks_dir() or ""
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = paths.MAPS
M_PER_TEXEL = 0.5


# ---------------------------------------------------------------- low-level texture helpers
def pct_info(z, name):
    h = z.read(name + "_header")
    w, hgt = struct.unpack_from("<II", h, 0x10)
    return w, hgt, h[0x26]


def pct_payload(d):
    """Pixel data of a Swarm .pct: after the mip table, before the 6-byte footer (01 00 + u32 size).
    Layout per github.com/skibidius/SnowrunnerMapConverter (parse_pct)."""
    try:
        table_end = struct.unpack_from("<Q", d, 0x36)[0]
        marker = struct.unpack_from("<H", d, table_end)[0]
        term = struct.unpack_from("<Q", d, table_end + 2)[0] if marker == 0x0104 else table_end
        start = term + 6
        if 0 < start < len(d) - 6:
            return d[start:len(d) - 6]
    except struct.error:
        pass
    return d[88:len(d) - 6]


def tail(z, name, nbytes):
    """First nbytes of the top mip (name kept for history)."""
    return pct_payload(z.read(name))[:nbytes]


def dds_wrap(fourcc, data, w, h, dx10=None):
    hd = (b"DDS " + struct.pack("<7I", 124, 0x81007, w, h, len(data), 0, 1) + b"\0" * 44
          + struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0))
    if dx10:
        hd += struct.pack("<5I", dx10, 3, 0, 1, 0)
    return hd + data


def resize_f(a, size, mode=Image.BILINEAR):
    return np.asarray(Image.fromarray(np.ascontiguousarray(a, dtype=np.float32)).resize(size, mode))


# ---------------------------------------------------------------- editor.pak colours (cached)
_editor = None
_color_cache_path = paths.COLORS
_color_cache = None


def editor():
    global _editor
    if _editor is None:
        _editor = zipfile.ZipFile(os.path.join(PAKS, "editor.pak"))
    return _editor


def _colors():
    global _color_cache
    if _color_cache is None:
        try:
            _color_cache = json.load(open(_color_cache_path))
        except Exception:
            _color_cache = {}
    return _color_cache


def avg_color(texname, alpha=False):
    c = _colors()
    key = texname + ("|a" if alpha else "")
    if key in c:
        return c[key]
    rgb = None
    try:
        im = Image.open(io.BytesIO(editor().read(f"[textures]/dds/{texname}.dds")))
        im.draft("RGBA", (256, 256))
        a = np.asarray(im.convert("RGBA").resize((128, 128), Image.BOX), np.float32)
        if alpha:
            w = (a[..., 3] > 100).astype(np.float32)
            if w.sum() > 10:
                rgb = (a[..., :3] * w[..., None]).sum((0, 1)) / w.sum()
        else:
            rgb = a[..., :3].reshape(-1, 3).mean(0)
    except Exception:
        rgb = None
    c[key] = None if rgb is None else [round(float(v), 1) for v in rgb]
    try:
        json.dump(c, open(_color_cache_path, "w"))
    except Exception:
        pass
    return c[key]


def material_color(name):
    for n in (f"tiles_{name}__d_a", f"tiles_{name}__d"):
        col = avg_color(n)
        if col:
            return np.array(col, np.float32)
    # fallbacks by family
    if "concrete" in name or "asphalt" in name: return np.array([128, 126, 122], np.float32)
    if "gravel" in name or "stone" in name: return np.array([118, 108, 92], np.float32)
    if "sand" in name: return np.array([150, 135, 105], np.float32)
    if "grass" in name: return np.array([92, 98, 60], np.float32)
    return np.array([100, 86, 66], np.float32)


# ---------------------------------------------------------------- data.stg parsing
_name_pat = re.compile(rb"([\x03-\x60])\x00([a-z][a-z0-9_]{2,94})\x00\x00")


def stg_plant_names(d, search=4_000_000):
    """The plant type table: u32 count, then count x (u16 len, name\\0, \\0)."""
    best = []
    for m in _name_pat.finditer(d, 0, min(len(d), search)):
        st = m.start()
        if st < 4:
            continue
        n = struct.unpack_from("<I", d, st - 4)[0]
        if not (8 <= n <= 4000) or n <= len(best):
            continue
        p, names = st, []
        for _ in range(n):
            if p + 2 > len(d):
                break
            L = struct.unpack_from("<H", d, p)[0]
            s = d[p + 2:p + 1 + L]
            # entry: u16 len, name + NUL, u8 flag (names may contain spaces)
            if not (2 <= L <= 120) or d[p + 1 + L] != 0 or not re.fullmatch(rb"[ -~]+", s) or d[p + 2 + L] > 8:
                break
            names.append(s.decode()); p += 2 + L + 1
        if len(names) == n:
            best = names
    return best


def terrain_materials(d):
    """Material sets: list of [base, layer0, layer1, layer2] names."""
    pat = re.compile(rb"(.{4})(.{4})([\x00-\x03]\x00\x00\x00|\xff\xff\xff\xff)([\x03-\x40])\x00([a-z0-9_]{3,60})\x00", re.S)
    sets, cur = [], {}
    for m in pat.finditer(d):
        if m.group(4)[0] != len(m.group(5)) + 1:
            continue
        idx = struct.unpack("<i", m.group(3))[0]
        if idx == -1:
            if len(cur) == 4:
                sets.append([cur[-1], cur[0], cur[1], cur[2]])
            cur = {}
        cur[idx] = m.group(5).decode()
    if len(cur) == 4:
        sets.append([cur[-1], cur[0], cur[1], cur[2]])
    return sets


def stg_plants(d, ntypes, half_x, half_z):
    """Vectorised scan for 32-byte plant instances. Returns array (type, x, y, z, scale)."""
    b = np.frombuffer(d, np.uint8)
    out = []
    win = 1 << 24
    for start in range(0, len(d) - 64, win):
        for al in range(4):
            s0 = start + al
            n = (min(len(d), start + win + 64) - s0) // 4
            if n < 16:
                continue
            f = np.frombuffer(d, "<f4", n, s0)
            x, y, z, sc = f[:-3], f[1:-2], f[2:-1], f[3:]
            with np.errstate(invalid="ignore"):
                m = (np.abs(x) < half_x + 20) & (np.abs(z) < half_z + 20) & (y > -300) & (y < 3000) & (sc > 0.05) & (sc < 30)
            idx = np.nonzero(m)[0]
            if idx.size == 0:
                continue
            off = s0 + idx * 4
            ok = (off >= 4)
            off, idx = off[ok], idx[ok]
            one = b[off - 4].astype(np.int32) | (b[off - 3].astype(np.int32) << 8)
            typ = b[off - 2].astype(np.int32) | (b[off - 1].astype(np.int32) << 8)
            k = (one == 1) & (typ < ntypes)
            if k.any():
                out.append(np.c_[typ[k], x[idx[k]], y[idx[k]], z[idx[k]], sc[idx[k]]])
    if not out:
        return np.zeros((0, 5), np.float32)
    a = np.concatenate(out).astype(np.float32)
    # windows overlap by 64 bytes; drop exact duplicates
    _, u = np.unique(a, axis=0, return_index=True)
    return a[np.sort(u)]


# ---------------------------------------------------------------- the game's baked map tiles
def baked_tiles(stg, W, H):
    """The game's own baked top-down terrain colour: one 64x64 DXT5 tile per 2x2 terrain blocks (48 m),
    row-major from the top-left, stored as length-prefixed records from data.stg section offset [2].
    RGB = colour (roads, painted ground, water, relief), A = occlusion of trees and buildings.
    Returns uint8 (h, w, 4) at 0.75 m per pixel, or None."""
    try:
        nx, nz = (W - 1) // 96, (H - 1) // 96
        if nx < 1 or nz < 1:
            return None
        p = struct.unpack_from("<4I", stg, 0)[2]
        img = np.zeros((nz * 64, nx * 64, 4), np.uint8)
        n = 0
        while p + 4 < len(stg) and n < nx * nz:
            L = struct.unpack_from("<I", stg, p)[0]
            if L <= 0 or p + 4 + L > len(stg):
                break
            b0 = p + 4
            p += 4 + L
            if stg[b0 + 1:b0 + 5] != b"\x80\x10\x00\x00" or stg[b0 + 5:b0 + 9] != b"DDS ":
                continue
            body = stg[b0:b0 + L]
            q, last = body.find(b"DDS |"), None
            while q >= 0:
                if body[q + 84:q + 88] == b"DXT5" and struct.unpack_from("<II", body, q + 12) == (64, 64):
                    last = q
                q = body.find(b"DDS |", q + 4)
            if last is None:
                continue
            t = Image.open(io.BytesIO(body[last:last + 128 + 5504])).convert("RGBA")
            r, c = divmod(n, nx)
            img[r * 64:(r + 1) * 64, c * 64:(c + 1) * 64] = np.asarray(t)
            n += 1
        if n != nx * nz:
            return None
        return level_tile_seams(img, nx, nz)
    except Exception:
        return None


def level_tile_seams(img, nx, nz, T=64):
    """Remove brightness/colour steps between baked tiles without blurring their content: solve a per-tile
    colour offset that best matches neighbours across every shared edge, then apply it as a smooth field."""
    f = img[..., :3].astype(np.float32)
    o = np.zeros((nz, nx, 3), np.float32)
    # edge differences (median over the edge of the 1-pixel step), right and down neighbours
    dr = np.zeros((nz, nx - 1, 3), np.float32); dd = np.zeros((nz - 1, nx, 3), np.float32)
    for r in range(nz):
        for c in range(nx - 1):
            a = f[r * T:(r + 1) * T, (c + 1) * T - 1]; b = f[r * T:(r + 1) * T, (c + 1) * T]
            dr[r, c] = np.median(b - a, axis=0)
    for r in range(nz - 1):
        for c in range(nx):
            a = f[(r + 1) * T - 1, c * T:(c + 1) * T]; b = f[(r + 1) * T, c * T:(c + 1) * T]
            dd[r, c] = np.median(b - a, axis=0)
    # ignore large legitimate steps (shorelines crossing an edge)
    dr = np.where(np.abs(dr) < 40, dr, 0); dd = np.where(np.abs(dd) < 40, dd, 0)
    for _ in range(400):                         # Jacobi iterations of the least-squares system
        acc = np.zeros_like(o); cnt = np.zeros((nz, nx, 1), np.float32)
        acc[:, :-1] += o[:, 1:] + dr; cnt[:, :-1] += 1        # o_left = o_right + d  (cancels the step)
        acc[:, 1:] += o[:, :-1] - dr; cnt[:, 1:] += 1
        acc[:-1] += o[1:] + dd; cnt[:-1] += 1
        acc[1:] += o[:-1] - dd; cnt[1:] += 1
        o = acc / np.maximum(cnt, 1)
        o -= o.mean(axis=(0, 1), keepdims=True)
    # offsets are piecewise constant per tile; a hard per-tile offset exactly cancels the edge steps
    field = np.repeat(np.repeat(o, T, 0), T, 1)
    out = img.copy()
    out[..., :3] = np.clip(f + field, 0, 255).astype(np.uint8)
    return out


BUILDINGS = os.environ.get("SR_BUILDINGS", "1") == "1"
ROT_MODE = os.environ.get("SR_ROT", "yaw")
ROT_SIGN = float(os.environ.get("SR_ROTSIGN", "1"))
ROOF = np.array([176, 172, 164], np.float32)


def draw_footprints(baked, items, canvas, treemask, shadow, half_x, half_z, px_per_m, size, log=print):
    a = baked[..., 3].astype(np.float32) / 255
    bh, bw = a.shape
    tm = Image.new("L", (bw, bh), 0); dr = ImageDraw.Draw(tm)
    for x, zz, r, col, kind in items:
        cx = (x + half_x) / 0.75; cy = (zz + half_z) / 0.75; R = max(r / 0.75 * 1.4, 2.5)
        dr.ellipse((cx - R, cy - R, cx + R, cy + R), fill=255)
    trees = np.asarray(tm) > 0
    m = Image.fromarray(((a < 0.42) & ~trees).astype(np.uint8) * 255)
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    # drop specks smaller than ~6 m2
    arr = np.asarray(m) > 0
    small = np.asarray(Image.fromarray(arr.astype(np.uint8) * 255).filter(ImageFilter.BoxBlur(2)), np.float32) / 255
    arr &= small > 0.22
    big = Image.fromarray(arr.astype(np.uint8) * 255).crop((0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75))))
    big = big.resize(size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(px_per_m * 0.25))
    fp = np.asarray(big, np.float32) / 255
    fp = np.clip((fp - 0.35) * 4, 0, 1)                  # crisp edge
    if fp.max() == 0:
        return
    # shadow: footprint shifted along the sun (from -x) by ~5 m
    off = int(5 * px_per_m)
    sh = np.zeros_like(fp); sh[:, off:] = fp[:, :-off] if off else fp
    shadow.paste(Image.fromarray(np.maximum(np.asarray(shadow), (sh * 170).astype(np.uint8))))
    base = np.asarray(canvas, np.float32)
    edge = np.clip(fp - np.asarray(Image.fromarray((fp * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3)), np.float32) / 255, 0, 1)
    roof = ROOF * (1 - 0.35 * edge[..., None])
    out = base * (1 - fp[..., None]) + roof * fp[..., None]
    canvas.paste(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)))
    treemask.paste(Image.fromarray(np.maximum(np.asarray(treemask), (fp * 255).astype(np.uint8))))
    log(f"  drew building footprints ({int((fp > 0.5).sum() / px_per_m ** 2)} m2)")


# ---------------------------------------------------------------- data_for_ssl.bin landmarks
_ssl_pat = re.compile(rb"[\x20-\x7e]{3,240}")


def ssl_landmarks(data, limit):
    groups = {}
    for m in _ssl_pat.finditer(data):
        s, st, en = m.group(), m.start(), m.end()
        if st < 2 or en + 5 > len(data) or data[en] != 0:
            continue
        if struct.unpack_from("<H", data, st - 2)[0] != len(s) + 1:
            continue
        cnt = struct.unpack_from("<I", data, en + 1)[0]
        start = en + 5
        if not (1 <= cnt <= 300000) or start + cnt * 28 > len(data):
            continue
        text = s.decode("latin-1")
        if "/" not in text:
            continue
        rec = np.frombuffer(data, np.uint8, cnt * 28, start).reshape(cnt, 28)
        f = rec[:, :16].copy().view("<f4").reshape(cnt, 4)
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(f).all(1) & (np.abs(f[:, 0]) <= limit) & (np.abs(f[:, 2]) <= limit) & (f[:, 1] > -250) & (f[:, 1] < 3000)
        if ok[: min(cnt, 24)].mean() < 0.8:
            continue
        sc = np.where((f[:, 3] > 0.01) & (f[:, 3] < 100), f[:, 3], 1.0)
        rot = (rec[:, 16:19].astype(np.float32) - 128.0) * (180.0 / 127.0)   # euler XYZ degrees
        groups.setdefault(text, []).append(np.c_[f[ok, 0], f[ok, 1], f[ok, 2], sc[ok], rot[ok]])
    return {k: np.concatenate(v) for k, v in groups.items()}


# ---------------------------------------------------------------- plant styling
SKIP = re.compile(r"(twig|herb|mushroom|cane|grass|flower|fern|small_rock|mud_chunk|coal|leaves|chunks|garbage|trash|stub|stump|branch|lying|log)")


def plant_style(name):
    """-> (kind, radius_m) or None. kind: conifer, broadleaf, bush, rock, burnt"""
    n = name.lower()
    if SKIP.search(n):
        return None
    TREE = ("spruce", "tsuga", "pine", "fir", "larch", "cedar", "thuja", "juniper", "sequoia", "maple", "birch",
            "aspen", "oak", "alder", "poplar", "willow", "elm", "linden", "palm", "acacia", "beech", "tree", "bush")
    if not any(re.search(r"(^|[/_])" + k, n) for k in TREE) or re.search(r"(^|[/_])fire", n):
        return None                       # rocks, sheds, caravans, fire stations... are drawn as meshes
    if "burnt" in n or "dry" in n or "dead" in n:
        return ("burnt", 1.6)
    if "bush" in n or "young" in n or "small_tree" in n:
        return ("bush", 1.3)
    if any(k in n for k in ("spruce", "tsuga", "pine", "fir", "larch", "cedar", "thuja", "juniper", "sequoia")):
        return ("conifer", 2.6)
    if any(k in n for k in ("maple", "birch", "aspen", "oak", "alder", "poplar", "willow", "elm", "linden", "ash", "tree", "palm", "acacia", "beech")):
        return ("broadleaf", 3.0)
    return None


FALLBACK = {"conifer": (52, 66, 44), "broadleaf": (110, 104, 52), "bush": (80, 90, 50), "rock": (130, 128, 124), "burnt": (50, 46, 42)}


def plant_color(name, kind):
    base = re.sub(r"_lmk$", "", name.split("/")[-1].lower())
    for cand in (base, re.sub(r"_(g|rus|us\d*)$", "", base)):
        col = avg_color(f"billboard_plants_{cand}__d_a", alpha=True)
        if col:
            return np.array(col, np.float32)
    return np.array(FALLBACK[kind], np.float32)


# ---------------------------------------------------------------- landmark meshes (buildings, bridges, props)
_shared = None
_mesh_cache = {}
_tex_cache = {}


def shared():
    global _shared
    if _shared is None:
        _shared = zipfile.ZipFile(os.path.join(PAKS, "shared.pak"))
    return _shared


def _texture(ref):
    """Albedo texture (small numpy RGB) for a material AlbedoMap reference."""
    if ref in _tex_cache:
        return _tex_cache[ref]
    stem = ref.replace("\\", "/").rsplit(".", 1)[0]
    cands = [stem.replace("/", "_"), stem.rsplit("/", 1)[-1]]
    arr = None
    names = set(editor().namelist())
    for c in cands:
        for suf in ("", "_a"):
            n = f"[textures]/dds/{c}{suf}.dds"
            if n in names:
                try:
                    im = Image.open(io.BytesIO(editor().read(n)))
                    im.draft("RGB", (512, 512))
                    arr = np.asarray(im.convert("RGB").resize((256, 256), Image.BOX), np.float32)
                except Exception:
                    arr = None
                break
        if arr is not None:
            break
    _tex_cache[ref] = arr
    return arr


def mesh_triangles(lmk_name):
    """-> (verts (T,3,3) local metres, colours (T,3) sRGB) for a landmark, cached."""
    if lmk_name in _mesh_cache:
        return _mesh_cache[lmk_name]
    import sr_mesh
    base = lmk_name.split("/")[-1]
    names = set(shared().namelist())
    res = None
    for cand in (f"[meshes]/landmarks_{base}", f"[meshes]/{base}"):
        if cand in names:
            try:
                parsed = sr_mesh.parse_combinexmesh(shared().read(cand))
                mats = sr_mesh.parse_xml_materials(parsed["xml"])
                tris, cols = [], []
                for part in sr_mesh.extract_visual_parts(parsed):
                    P = part["positions"]; I = part["indices"].astype(np.int64)
                    tri = P[I]
                    tex = _texture(mats.get(part["material"], {}).get("AlbedoMap", ""))
                    if tex is not None and part["uvs"] is not None:
                        uv = part["uvs"][I].mean(1)
                        u = (np.mod(uv[:, 0], 1) * 255).astype(int); v = (np.mod(uv[:, 1], 1) * 255).astype(int)
                        col = tex[v, u]
                    else:
                        col = np.tile(np.array([150, 146, 138], np.float32), (len(tri), 1))
                    tris.append(tri); cols.append(col)
                if tris:
                    res = (np.concatenate(tris).astype(np.float32), np.concatenate(cols).astype(np.float32))
            except Exception:
                res = None
            break
    _mesh_cache[lmk_name] = res
    return res


def euler_matrix(rx, ry, rz):
    """Same convention as the SnowrunnerMapConverter (XYZ euler -> quaternion -> matrix)."""
    hx, hy, hz = np.radians([rx, ry, rz]) * 0.5
    cx, sx, cy, sy, cz, sz = np.cos(hx), np.sin(hx), np.cos(hy), np.sin(hy), np.cos(hz), np.sin(hz)
    x = sx * cy * cz - cx * sy * sz; y = cx * sy * cz + sx * cy * sz
    z = cx * cy * sz - sx * sy * cz; w = cx * cy * cz + sx * sy * sz
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float32)


def mesh_coverage(w, occ, half_x, half_z, R=0.75):
    xs = (w[..., 0] + half_x + 0.375) / R; zs = (w[..., 2] + half_z + 0.375) / R
    u0, v0 = int(xs.min()) - 1, int(zs.min()) - 1; u1, v1 = int(xs.max()) + 2, int(zs.max()) + 2
    if u0 < 0 or v0 < 0 or u1 >= occ.shape[1] or v1 >= occ.shape[0]:
        return None
    im = Image.new("L", (u1 - u0, v1 - v0), 0); d = ImageDraw.Draw(im)
    for tx, tz in zip(xs, zs):
        d.polygon([(tx[k] - u0, tz[k] - v0) for k in range(3)], fill=255)
    msk = np.asarray(im) > 0
    if msk.sum() < 8:
        return None
    return float(occ[v0:v1, u0:u1][msk].mean())


def fit_yaw(tris_xz, x, z, sc, occ, half_x, half_z, guess):
    """Heading (deg) that best matches the game's own baked occlusion around (x, z); None if inconclusive."""
    R = 0.75
    best = None
    pts = tris_xz.reshape(-1, 2) * sc
    rad = float(np.abs(pts).max()) + 6
    u0, v0 = int((x - rad + half_x + 0.375) / R), int((z - rad + half_z + 0.375) / R)
    u1, v1 = int((x + rad + half_x + 0.375) / R) + 1, int((z + rad + half_z + 0.375) / R) + 1
    if u0 < 0 or v0 < 0 or u1 >= occ.shape[1] or v1 >= occ.shape[0]:
        return None
    o = occ[v0:v1, u0:u1]
    scores = []
    for ang in range(0, 360, 5):
        a = np.radians(ang); c, s = np.cos(a), np.sin(a)
        q = tris_xz.reshape(-1, 3, 2) * sc
        rx = q[..., 0] * c + q[..., 1] * s + x; rz = -q[..., 0] * s + q[..., 1] * c + z
        im = Image.new("L", (u1 - u0, v1 - v0), 0); d = ImageDraw.Draw(im)
        for tx, tz in zip(rx, rz):
            d.polygon([((tx[k] + half_x + 0.375) / R - u0, (tz[k] + half_z + 0.375) / R - v0) for k in range(3)], fill=255)
        msk = np.asarray(im) > 0
        if msk.sum() < 12 or (~msk).sum() < 12:
            return None
        scores.append((o[msk].mean() - o[~msk].mean(), ang))
    scores.sort(reverse=True)
    top, ang = scores[0]
    med = np.median([s for s, _ in scores])
    if top < 0.25 or top - med < 0.08:
        return None
    return float(ang)


def draw_landmark_meshes(lmk, canvas, flatmask, shadow, half_x, half_z, px_per_m, log=print, skip_flat=False, occ=None):
    """Rasterise every non-plant landmark top-down. Lighting is baked relative to the flat-ground light."""
    cd = ImageDraw.Draw(canvas); md = ImageDraw.Draw(flatmask); sd = ImageDraw.Draw(shadow)
    sun = -np.array([1.0, -1.0, 0.0]); sun /= np.linalg.norm(sun)
    flat_light = SUN_COL.mean() * 0.7 + AMB_UP.mean()
    all_polys = []
    n_inst = 0
    fitted = [0]
    skipped = [0]
    fitted_flag = lambda p: True
    for name, arr in lmk.items():
        is_plant = plant_style(name) is not None
        if is_plant and not GAME_TREES:
            continue
        m = mesh_triangles(name)
        if m is None:
            continue
        tris, cols = m
        if skip_flat and float(tris[..., 1].max() - tris[..., 1].min()) < 0.35:
            continue                      # paving / ground decals: already part of the baked terrain
        ext = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
        fittable = occ is not None and not is_plant and ext[1] > 2.5 and ext[0] * ext[2] > 25
        for p in arr:
            if fittable:
                yaw = fit_yaw(tris[..., [0, 2]], p[0], p[2], p[3], occ, half_x, half_z, None)
                if yaw is not None:
                    p = p.copy(); p[4:7] = (0, 0, 0); p = np.r_[p[:4], 0, 0, 0]
                    p[5] = yaw; fitted[0] += 1
            x, y, z, sc = p[:4]
            if len(p) >= 7 and fittable and p[4] == 0 and p[6] == 0 and fitted_flag(p):
                a = np.radians(p[5]); c, s = np.cos(a), np.sin(a)
                R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], np.float32)
            elif len(p) >= 7:
                R = euler_matrix(*p[4:7]) if ROT_MODE == "full" else euler_matrix(0, p[5] * ROT_SIGN, 0)
            else:
                R = np.eye(3, dtype=np.float32)
            w = tris.reshape(-1, 3) @ R.T * sc + np.array([x, y, z], np.float32)
            w = w.reshape(-1, 3, 3)
            if fittable and occ is not None:
                # the game's footprint is ground truth: if this mesh doesn't sit on it, the footprint layer
                # (drawn from the game's own occlusion) shows the building instead
                cov = mesh_coverage(w, occ, half_x, half_z)
                if cov is not None and cov < 0.55:
                    skipped[0] += 1
                    continue
            nrm = np.cross(w[:, 1] - w[:, 0], w[:, 2] - w[:, 0])
            ln = np.linalg.norm(nrm, axis=1, keepdims=True); ln[ln == 0] = 1; nrm /= ln
            nrm[nrm[:, 1] < 0] *= -1           # top-down view sees the upward side
            if GAME_TREES:
                # same lighting the game uses for its map: sun (1,-1,0) + sky ambient, filmic tonemap
                d_ = np.clip(nrm @ sun, 0, 1).astype(np.float32)
                shade = tonemap_lmk(cols, d_, np.clip(nrm[:, 1], 0, 1).astype(np.float32))
            else:
                light = (SUN_COL.mean() * np.clip(nrm @ sun, 0, 1) + AMB_MID.mean() + (AMB_UP.mean() - AMB_MID.mean()) * np.clip(nrm[:, 1], 0, 1)) / flat_light
                shade = np.clip(cols * light[:, None] ** (1 / 2.2), 0, 255)
            top = w[:, :, 1].max(1)
            px = (w[:, :, 0] + half_x) * px_per_m; pz = (w[:, :, 2] + half_z) * px_per_m
            hgt = np.clip(top - y, 0, 30)
            for k in range(len(w)):
                all_polys.append((top[k], px[k], pz[k], shade[k], hgt[k]))
            n_inst += 1
    all_polys.sort(key=lambda t: t[0])
    for top, px, pz, col, hgt in all_polys:
        pts = list(zip(px.tolist(), pz.tolist()))
        off = hgt * px_per_m          # sun at 45 degrees from -x: shadow length = height
        sd.polygon([(a + off, b) for a, b in pts], fill=170)
        cd.polygon(pts, fill=tuple(int(c) for c in col))
        md.polygon(pts, fill=255)
    log(f"  drew {n_inst} landmark meshes ({len(all_polys)} triangles); {fitted[0]} large ones aligned to the game's footprints, {skipped[0]} replaced by footprints")


GAME_TREES = os.environ.get("SR_GAME_TREES", "1") == "1"
LMK_EXPOSURE = float(os.environ.get("SR_LMK_EXPOSURE", "1.5"))


def tonemap_lmk(cols, d, up):
    lin = (np.clip(cols, 0, 255) / 255.0) ** 2.2
    amb = AMB_MID + (AMB_UP - AMB_MID) * up[:, None]
    rad = lin * (SUN_COL * d[:, None] + amb) * LMK_EXPOSURE
    return np.clip(_filmic(rad) / _filmic(TW), 0, 1) ** (1 / 2.2) * 255


# ---------------------------------------------------------------- water
WATER_SHALLOW = np.array([112, 153, 196], np.float32)   # sampled from the in-game map
WATER_DEEP = np.array([100, 143, 188], np.float32)
SHORE = np.array([70, 78, 80], np.float32)


def draw_water(z, base, names, img, hs, W, H, size, px_per_m, log=print, baked=None):
    wn = base + "water_height_map.pct"
    if wn not in names:
        return img
    ww, wh, _ = pct_info(z, wn)
    wl = np.frombuffer(tail(z, wn, ww * wh * 4), "<f4").reshape(wh, ww).astype(np.float32)
    ok = np.isfinite(wl) & (np.abs(wl) < 1e5)
    if ok.sum() < 4:
        return img
    # fill invalid vertices from valid neighbours so the level interpolates smoothly up to the shore
    lv = np.where(ok, wl, 0).astype(np.float32); cnt = ok.astype(np.float32)
    for _ in range(4):
        pad_l = np.pad(lv, 1, mode="edge"); pad_c = np.pad(cnt, 1, mode="edge")
        sl = sum(pad_l[1 + dy:1 + dy + wh, 1 + dx:1 + dx + ww] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        sc = sum(pad_c[1 + dy:1 + dy + wh, 1 + dx:1 + dx + ww] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
        fill = (cnt == 0) & (sc > 0)
        lv = np.where(fill, sl / np.maximum(sc, 1e-6), lv); cnt = np.where(fill, 1.0, cnt)
    near = cnt > 0
    # vertex grid -> output pixels: vertex i sits at texel i*12 of the heightmap, i.e. world spacing 6 m
    span_x = (ww - 1) * (W - 1) / (ww - 1); span_z = (wh - 1) * (H - 1) / (wh - 1)
    lvl = Image.fromarray(lv).transform(size, Image.EXTENT, (0, 0, ww - 1, wh - 1), Image.BILINEAR)
    msk = Image.fromarray(near.astype(np.float32)).transform(size, Image.EXTENT, (0, 0, ww - 1, wh - 1), Image.BILINEAR)
    lvl = np.asarray(lvl, np.float32); msk = np.asarray(msk, np.float32)
    depth = np.where(msk > 0.99, lvl - hs, -1.0)
    if baked is not None:
        # only where the game itself draws water (its baked map); the level data then gives a sharp shore
        b = baked[..., :3].astype(np.float32)
        wet = ((b[..., 2] > b[..., 0] + 18) & (b[..., 2] > b[..., 1] + 4)).astype(np.uint8) * 255
        hx_, hz_ = (W - 1) / 4, (H - 1) / 4
        wm = Image.fromarray(wet).crop((0, 0, int(round(2 * hx_ / 0.75)), int(round(2 * hz_ / 0.75))))
        wm = wm.filter(ImageFilter.MaxFilter(5)).resize(size, Image.BILINEAR)
        depth = np.where(np.asarray(wm) > 100, depth, -1.0)
    a = (depth > 0).astype(np.uint8) * 255
    a = np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(px_per_m * 0.3)), np.float32) / 255
    a = np.clip((a - 0.5) * 3 + 0.5, 0, 1)                              # crisp, anti-aliased shoreline
    ring = np.clip(1 - np.abs(a - 0.5) * 2.2, 0, 1)                     # thin dark line on the shore
    deep = np.clip(depth / 3.0, 0, 1)[..., None]
    col = WATER_SHALLOW * (1 - deep) + WATER_DEEP * deep
    out = img * (1 - a[..., None]) + col * a[..., None]
    out = out * (1 - 0.55 * ring[..., None]) + SHORE * (0.55 * ring[..., None])
    log(f"  water: {int((a > 0.5).sum() / px_per_m ** 2)} m2")
    return out


# ---------------------------------------------------------------- vegetation sprites
SPRITES = os.environ.get("SR_SPRITES", "1") == "1"


def stamp_vegetation(stamps, items, canvas, shadow, half_x, half_z, px_per_m, size, log=print):
    """Stamp real top-down tree sprites. Returns (canvas, shadow, treemask, items still needing discs)."""
    treemask = Image.new("L", size, 0)
    if not SPRITES or not stamps:
        return canvas, shadow, treemask, items
    try:
        import treesprites
    except Exception:
        return canvas, shadow, treemask, items
    rgba = canvas.convert("RGBA")
    cache = {}
    have = {}
    leftover_names = set()
    order = sorted(range(len(stamps)), key=lambda i: stamps[i][1])
    n = 0
    for i in order:
        x, zz, sc, nm, kind = stamps[i]
        if kind == "rock":
            continue
        if nm not in have:
            have[nm] = treesprites.sprite(nm)
        sp = have[nm]
        if sp is None:
            leftover_names.add(nm); continue
        arr, res = sp
        px = max(3, int(round(arr.shape[0] * res * sc * px_per_m)))
        rot = int((x * 7.13 + zz * 3.71)) % 8
        key = (nm, px, rot)
        im = cache.get(key)
        if im is None:
            base = Image.fromarray(arr).resize((px, px), Image.LANCZOS if px < arr.shape[0] else Image.BILINEAR)
            im = base.rotate(rot * 45, resample=Image.BILINEAR, expand=False)
            cache[key] = im
        cx = (x + half_x) * px_per_m; cy = (zz + half_z) * px_per_m
        ox, oy = int(cx - px / 2), int(cy - px / 2)
        a = im.getchannel("A")
        # shadow east of the tree (sun from -x, 45 degrees): offset ~ half the crown width
        shadow.paste(140, (ox + int(px * 0.45), oy + int(px * 0.08)), a)
        rgba.alpha_composite(im, (max(0, ox), max(0, oy)), (max(0, -ox), max(0, -oy))) if ox < size[0] and oy < size[1] else None
        treemask.paste(255, (ox, oy), a)
        n += 1
    log(f"  stamped {n} tree sprites ({len(have) - len(leftover_names)} species from game meshes); no mesh: {sorted(leftover_names)[:12]}")
    rest = [it for it, st in zip(items, stamps) if st[3] in leftover_names or st[4] == "rock"]
    rest = [it for it in rest if it[4] != "rock" or it[2] > 1.2]          # drop pebbles
    return rgba.convert("RGB"), shadow, treemask, rest


# ---------------------------------------------------------------- LUT
_lut = None


def apply_lut(img):
    """img float32 0..255 (H,W,3) -> graded with env_lut_minimap (16^3, trilinear)."""
    global _lut
    if _lut is None:
        d = editor().read("[textures]/dds/env_lut_minimap__vol_uncmp.dds")
        raw = np.frombuffer(d[-16384:], np.uint8).reshape(16, 16, 16, 4)
        # bytes are B,G,R,A; index order [b][g][r]
        _lut = raw[..., [2, 1, 0]].astype(np.float32)
    c = np.clip(img / 255.0, 0, 1) * 15
    i0 = np.floor(c).astype(np.int32); i0 = np.minimum(i0, 14); t = c - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    tr, tg, tb = t[..., 0:1], t[..., 1:2], t[..., 2:3]
    L = _lut
    def at(db, dg, dr):
        return L[b0 + db, g0 + dg, r0 + dr]
    c00 = at(0, 0, 0) * (1 - tr) + at(0, 0, 1) * tr
    c01 = at(0, 1, 0) * (1 - tr) + at(0, 1, 1) * tr
    c10 = at(1, 0, 0) * (1 - tr) + at(1, 0, 1) * tr
    c11 = at(1, 1, 0) * (1 - tr) + at(1, 1, 1) * tr
    c0 = c00 * (1 - tg) + c01 * tg
    c1 = c10 * (1 - tg) + c11 * tg
    return c0 * (1 - tb) + c1 * tb


# ---------------------------------------------------------------- navigation_map lighting + tonemap
SUN_COL = np.array([255, 230, 200], np.float32) / 255 * 1.8          # SunColor x 1.8
AMB_UP = np.array([200, 210, 216], np.float32) / 255 * 2.6           # Ambient PosY
AMB_MID = np.array([200, 210, 216], np.float32) / 255 * 2.0          # Ambient MidY
EXPOSURE = float(os.environ.get("SR_EXPOSURE", "2.5"))                # HdrExposureScale
HAZE = float(os.environ.get("SR_HAZE", "0.22"))                      # fog seen from the high map camera
FOG_COL = np.array([184, 218, 243], np.float32)
TA, TB, TC, TD, TE, TF, TW = 0.1, 0.5, 0.1, 0.3, 0.02, 0.3, 2.2       # Tonemap A..F, ToneMapWhite
STYLE = os.environ.get("SR_STYLE", "game")       # game (baked tiles) | rebuild (own terrain)
RELIEF = float(os.environ.get("SR_RELIEF", "0.35"))
AO_MIN = float(os.environ.get("SR_AO", "1.0"))      # baked occlusion off: it smudges at high zoom
HAZE_B = float(os.environ.get("SR_HAZE_B", "0.06"))
SAT = float(os.environ.get("SR_SAT", "0.82"))
WARM = float(os.environ.get("SR_WARM", "0.0"))


def grade(img):
    """Gentle grade toward the in-game map look: slightly desaturated, neutral."""
    lum = (img @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
    out = lum + (img - lum) * SAT
    if WARM:
        out = out * np.array([1 + WARM, 1, 1 - WARM], np.float32)
    return out


def _filmic(x):
    return (x * (TA * x + TC * TB) + TD * TE) / (x * (TA * x + TB) + TD * TF) - TE / TF


def tonemap(albedo_srgb, d_sun, a_up):
    lin = (np.clip(albedo_srgb, 0, 255) / 255.0) ** 2.2
    amb = AMB_MID + (AMB_UP - AMB_MID) * a_up[..., None]
    rad = lin * (SUN_COL * d_sun[..., None] + amb) * EXPOSURE
    out = _filmic(rad) / _filmic(TW)
    return np.clip(out, 0, 1) ** (1 / 2.2) * 255


# ---------------------------------------------------------------- main render
def height_scale(hm, pts):
    """Fit y = k*h + b from objects that stand on the ground (robust, trimmed least squares)."""
    H, W = hm.shape
    half_x, half_z = (W - 1) * M_PER_TEXEL / 2, (H - 1) * M_PER_TEXEL / 2
    u = ((pts[:, 0] / half_x + 1) / 2 * (W - 1)).round().astype(int)
    v = ((pts[:, 2] / half_z + 1) / 2 * (H - 1)).round().astype(int)
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    h = hm[v[ok], u[ok]]; y = pts[ok, 1]
    if len(h) < 50:
        return 0.003, 0.0
    sel = np.ones(len(h), bool)
    for _ in range(6):
        A = np.c_[h[sel], np.ones(sel.sum())]
        (k, b), *_ = np.linalg.lstsq(A, y[sel], rcond=None)
        r = np.abs(y - (k * h + b))
        sel = r < max(0.5, np.quantile(r, 0.6))
    return float(k), float(b)


def render(level, px_per_m=2.0, log=print):
    t0 = time.time()
    z = zipfile.ZipFile(os.path.join(PAKS, level + ".pak"))
    names = set(z.namelist())
    base = f"[textures]/pct/{level}_"
    hn = base + "height_map.pct"
    W, H, _ = pct_info(z, hn)
    hm = np.frombuffer(tail(z, hn, W * H * 2), "<u2").reshape(H, W).astype(np.float32)
    half_x, half_z = (W - 1) * M_PER_TEXEL / 2, (H - 1) * M_PER_TEXEL / 2
    OW, OH = int(round(2 * half_x * px_per_m)), int(round(2 * half_z * px_per_m))
    size = (OW, OH)

    stg = z.read("[scene]/data.stg") if "[scene]/data.stg" in names else b""
    ssl = z.read("[scene]/data_for_ssl.bin") if "[scene]/data_for_ssl.bin" in names else b""
    plant_names = stg_plant_names(stg) if stg else []
    plants = stg_plants(stg, len(plant_names), half_x, half_z) if plant_names else np.zeros((0, 5), np.float32)
    lmk = ssl_landmarks(ssl, max(half_x, half_z) + 50) if ssl else {}
    log(f"{level}: {len(plants)} plant instances, {sum(len(v) for v in lmk.values())} landmarks ({time.time() - t0:.1f}s)")

    # vertical scale from ground objects (trees/rocks sit on the terrain)
    ground = [v[:, :3] for k, v in lmk.items() if plant_style(k)] + ([plants[:, 1:4]] if len(plants) else [])
    gp = np.concatenate(ground) if ground else np.zeros((0, 3))
    k_h, b_h = height_scale(hm, gp)
    # keep only stg plants that actually sit on the terrain (rejects false matches)
    if len(plants):
        u = ((plants[:, 1] / half_x + 1) / 2 * (W - 1)).round().clip(0, W - 1).astype(int)
        v = ((plants[:, 3] / half_z + 1) / 2 * (H - 1)).round().clip(0, H - 1).astype(int)
        plants = plants[np.abs(plants[:, 2] - (k_h * hm[v, u] + b_h)) < 2.5]

    # ---- terrain geometry & lighting (navigation_map: SunDir (1,-1,0), bright ambient)
    hm_m = hm * k_h + b_h
    hs = resize_f(hm_m, size)
    mpp = 1.0 / px_per_m
    gz, gx = np.gradient(hs, mpp)
    n = np.stack([-gx, np.ones_like(hs), -gz], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    sun = -np.array([1.0, -1.0, 0.0]); sun /= np.linalg.norm(sun)
    diffuse = np.clip(n @ sun, 0, 1).astype(np.float32)
    up = np.clip(n[..., 1], 0, 1).astype(np.float32)

    # ---- terrain colour: the game's own baked map tiles when present (exact roads, ground, water)
    baked = baked_tiles(stg, W, H) if stg and STYLE != "rebuild" else None
    if baked is not None:
        bk = Image.fromarray(baked).crop((0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75))))
        base_rgb = None
        try:
            import terrain
            base_rgb = terrain.render_ground(z, level, stg, baked, half_x, half_z, px_per_m, size, log)
        except Exception as e:
            import traceback; log("  full-res ground failed: " + traceback.format_exc())
        if base_rgb is None:
            base_rgb = np.asarray(bk.convert("RGB").resize(size, Image.LANCZOS), np.float32)
        ao = np.asarray(bk.getchannel("A").resize(size, Image.BILINEAR), np.float32) / 255
        log(f"  using the game's baked map tiles ({baked.shape[1]}x{baked.shape[0]})")
    # ---- material albedo (fallback when a level has no baked tiles)
    sets = terrain_materials(stg) if stg and baked is None else []
    albedo = np.full((OH, OW, 3), (104, 100, 78), np.float32)
    bn = base + "blend_map__cmp.pct"
    if bn in names and sets:
        bw, bh, fmt = pct_info(z, bn)
        pw, ph = (bw + 3) // 4 * 4, (bh + 3) // 4 * 4
        try:
            im = Image.open(io.BytesIO(dds_wrap(b"DX10", tail(z, bn, pw * ph), pw, ph, 98))).convert("RGBA").crop((0, 0, bw, bh))
            wts = np.asarray(im.resize(size, Image.BOX), np.float32)[..., :3] / 255.0
            wts = np.asarray(Image.fromarray((wts * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255.0
            # the most common set describes the level's palette
            from collections import Counter
            common = Counter(tuple(s) for s in sets).most_common(1)[0][0]
            cols = [material_color(m) for m in common]
            wsum = np.clip(wts.sum(-1, keepdims=True), 0, 1)
            albedo = cols[0] * (1 - wsum) + sum(cols[i + 1] * wts[..., i:i + 1] for i in range(3))
        except Exception as e:
            log(f"  blend map skipped: {e}")

    # tint map modulates albedo brightness a little (BC5 RG)
    tn = base + "tint_map__cmp.pct"
    if tn in names:
        try:
            tw, th, _ = pct_info(z, tn)
            pw, ph = (tw + 3) // 4 * 4, (th + 3) // 4 * 4
            t = np.asarray(Image.open(io.BytesIO(dds_wrap(b"ATI2", tail(z, tn, pw * ph), pw, ph))).convert("RGB").crop((0, 0, tw, th)).resize(size, Image.BOX), np.float32) / 255
            albedo *= (0.8 + 0.4 * t[..., :1])
        except Exception:
            pass

    # mud
    en = base + "extrude_map.pct"
    if en in names and baked is None:
        ew, eh, _ = pct_info(z, en)
        e = np.frombuffer(tail(z, en, ew * eh), np.uint8).reshape(eh, ew)
        e = resize_f(e.astype(np.float32) / 255, size, Image.BOX)
        mud = np.array([74, 58, 44], np.float32)
        a = np.clip(e * 0.75, 0, 0.75)[..., None]
        albedo = albedo * (1 - a) + mud * a

    img = albedo if baked is None else base_rgb

    # ---- water from the game's water levels (metres, one vertex every 12 heightmap texels)
    try:
        img = draw_water(z, base, names, img, hs, W, H, size, px_per_m, log, baked)
    except Exception as e:
        log(f"  water skipped: {e}")

    # ---- vegetation: shadows then canopies
    canvas = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    shadow = Image.new("L", size, 0)
    sd = ImageDraw.Draw(shadow)
    items = []
    styles = {}
    for i, nm in enumerate(plant_names):
        st = plant_style(nm)
        if st:
            styles[i] = (st, plant_color(nm, st[0]))
    stamps = []          # (x, z, scale, name) for sprite stamping
    for t_id, (st, col) in styles.items():
        sel = plants[plants[:, 0] == t_id] if len(plants) else []
        nm = plant_names[t_id]
        for p in sel:
            sc = min(max(float(p[4]), 0.4), 3.0)
            items.append((p[1], p[3], st[1] * min(sc, 2.5), col, st[0]))
            stamps.append((p[1], p[3], sc, nm, st[0]))
    for nm, arr in lmk.items():
        st = plant_style(nm)
        if not st:
            continue
        col = plant_color(nm, st[0])
        r = st[1] * 1.25
        for p in arr:
            sc = min(max(float(p[3]), 0.4), 3.0)
            items.append((p[0], p[2], r * min(sc, 2.5), col, st[0]))
            stamps.append((p[0], p[2], sc, nm, st[0]))
    log(f"  drawing {len(items)} plants")
    all_items = list(items)
    if GAME_TREES and lmk:
        treemask_pre = Image.new("L", size, 0); items = []
        log("  vegetation: the game's landmark tree models")
    else:
        canvas, shadow, treemask_pre, items = stamp_vegetation(stamps, items, canvas, shadow, half_x, half_z, px_per_m, size, log)
    sd = ImageDraw.Draw(shadow)
    # sun from -x: shadows fall to +x  (fallback discs for plants without a mesh sprite)
    for x, zz, r, col, kind in items:
        cx = (x + half_x) * px_per_m; cy = (zz + half_z) * px_per_m
        R = r * px_per_m
        off = R * (0.9 if kind != "rock" else 0.4)
        sd.ellipse((cx - R + off, cy - R * 0.85, cx + R + off, cy + R * 0.85), fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, px_per_m * 0.8)))
    sh = np.asarray(shadow, np.float32) / 255
    treemask = treemask_pre
    tmd = ImageDraw.Draw(treemask)
    cd = ImageDraw.Draw(canvas)
    lit = 1.0
    for x, zz, r, col, kind in sorted(items, key=lambda it: it[1]):
        cx = (x + half_x) * px_per_m; cy = (zz + half_z) * px_per_m
        R = max(r * px_per_m, 1.0)
        dark = tuple(int(c * 0.62) for c in col)
        mid = tuple(int(min(255, c * 0.95)) for c in col)
        hi = tuple(int(min(255, c * 1.35)) for c in col)
        cd.ellipse((cx - R, cy - R, cx + R, cy + R), fill=dark)
        tmd.ellipse((cx - R, cy - R, cx + R, cy + R), fill=255)
        cd.ellipse((cx - R * 0.85, cy - R * 0.85, cx + R * 0.55, cy + R * 0.55), fill=mid)
        if R > 2.5:
            cd.ellipse((cx - R * 0.7, cy - R * 0.7, cx - R * 0.05, cy - R * 0.05), fill=hi)
    # buildings that are level prefabs (garages, houses...) have no landmark mesh; the baked occlusion
    # records their footprints as sharp dark shapes, so draw those that are not trees as roofs
    mshadow = Image.new("L", size, 0)
    if baked is not None and BUILDINGS:
        try:
            draw_footprints(baked, all_items, canvas, treemask, mshadow, half_x, half_z, px_per_m, size, log)
        except Exception as e:
            log(f"  footprints skipped: {e}")
    try:
        occ_g = (1 - baked[..., 3].astype(np.float32) / 255) if baked is not None else None
        draw_landmark_meshes(lmk, canvas, treemask, mshadow, half_x, half_z, px_per_m, log, skip_flat=baked is not None, occ=occ_g)
    except Exception as e:
        log(f"  landmark meshes skipped: {e}")
    mshadow = mshadow.filter(ImageFilter.GaussianBlur(max(1, px_per_m * 0.6)))
    sh = np.maximum(sh, np.asarray(mshadow, np.float32) / 255 * 0.9)
    alb = np.asarray(canvas, np.float32)
    tm = np.asarray(treemask, np.float32) / 255
    if baked is not None:
        # baked terrain is already lit; add the map sun's relief, tree/building shadows and baked occlusion
        g = 1.0 + RELIEF * (diffuse - 0.707)
        g = g * (1 - 0.42 * sh) * (AO_MIN + (1 - AO_MIN) * ao)
        g = g * (1 - tm) + tm
        img = alb * g[..., None]
        img = img * (1 - HAZE_B) + FOG_COL * 0.8 * HAZE_B
        img = grade(img)
    else:
        d_sun = diffuse * (1 - tm) + 0.7 * tm
        d_sun = d_sun * (1 - 0.8 * sh * (1 - tm))
        a_up = up * (1 - tm) + tm
        img = tonemap(alb, d_sun, a_up)
        img = img * (1 - HAZE) + FOG_COL * 0.8 * HAZE
        img = apply_lut(img)
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    meta = {"w": W, "h": H, "half_x": half_x, "half_z": half_z, "img_w": OW, "img_h": OH,
            "px_per_m": px_per_m, "k_h": k_h, "b_h": b_h, "plants": len(all_items), "version": 5, "baked": baked is not None}
    log(f"  done in {time.time() - t0:.1f}s")
    return out, meta, hm


def build(level, px_per_m=3.0, log=print):
    os.makedirs(OUT, exist_ok=True)
    img, meta, hm = render(level, px_per_m, log)
    img.save(os.path.join(OUT, level + ".jpg"), quality=90, optimize=True)
    small = np.asarray(Image.fromarray(hm).resize((128, max(1, int(128 * hm.shape[0] / hm.shape[1]))), Image.BOX), np.float32)
    np.save(os.path.join(OUT, level + ".h.npy"), small.astype(np.uint16))
    idx_p = os.path.join(OUT, "index.json")
    idx = json.load(open(idx_p)) if os.path.exists(idx_p) else {}
    idx[level] = meta
    json.dump(idx, open(idx_p, "w"), indent=1)
    return meta


def all_levels():
    out = []
    for fn in sorted(os.listdir(PAKS)):
        if fn.startswith("level_") and fn.endswith(".pak") and "main_menu" not in fn and "test_polygon" not in fn:
            out.append(fn[:-4])
    return out


if __name__ == "__main__":
    import sys
    lv = sys.argv[1:] or all_levels()
    for l in lv:
        try:
            build(l)
        except Exception as e:
            print("FAILED", l, e)


# ---------------------------------------------------------------- fast per-level index (for map detection)
HEIGHTS = os.path.join(OUT, "heights")


def level_info(level):
    """Extent, vertical scale and a 512px height grid in metres; no rendering. ~1-2 s per level."""
    z = zipfile.ZipFile(os.path.join(PAKS, level + ".pak"))
    names = set(z.namelist())
    hn = f"[textures]/pct/{level}_height_map.pct"
    if hn not in names:
        return None
    W, H, _ = pct_info(z, hn)
    hm = np.frombuffer(tail(z, hn, W * H * 2), "<u2").reshape(H, W).astype(np.float32)
    half_x, half_z = (W - 1) * M_PER_TEXEL / 2, (H - 1) * M_PER_TEXEL / 2
    pts = []
    if "[scene]/data_for_ssl.bin" in names:
        lmk = ssl_landmarks(z.read("[scene]/data_for_ssl.bin"), max(half_x, half_z) + 50)
        pts = [v[:, :3] for k, v in lmk.items() if plant_style(k)]
    gp = np.concatenate(pts) if pts else np.zeros((0, 3))
    k_h, b_h = height_scale(hm, gp)
    gw = 512; gh = max(1, int(round(512 * H / W)))
    grid = resize_f(hm * k_h + b_h, (gw, gh), Image.BOX).astype(np.float16)
    os.makedirs(HEIGHTS, exist_ok=True)
    np.save(os.path.join(HEIGHTS, level + ".npy"), grid)
    return {"w": W, "h": H, "half_x": half_x, "half_z": half_z, "k_h": k_h, "b_h": b_h, "n_ground": int(len(gp))}


def build_index(log=print):
    p = os.path.join(OUT, "levels.json")
    idx = {}
    for lv in all_levels():
        try:
            info = level_info(lv)
            if info:
                idx[lv] = info; log(f"{lv}: k={info['k_h']:.5f} ground={info['n_ground']}")
        except Exception as e:
            log(f"{lv}: FAILED {e}")
    json.dump(idx, open(p, "w"), indent=1)
    return idx
