"""Full-resolution terrain colour from the game's terrain materials.

Sources (all from the level / editor paks):
  * blend map (4096^2 BC7): per-texel weights of material layers 0..2 over the base layer (RGB)
  * material sets (data.stg section 1): 4 layers each (base + 3) with tiling size in metres
  * each 24 m terrain block uses one material set; we pick the set whose blended colour best matches the
    game's baked map colour for that block (the baked tiles are low-res but colour-correct)
  * layer textures: editor.pak tiles_<name>__d_a.dds, repeated at their tiling size -> real ground detail
  * final colour is matched to the baked colour at a ~6 m scale, so detail is sharp and colours are right
"""
import io, os, struct
import numpy as np
from PIL import Image, ImageFilter

import maprender as M

TEX_DETAIL = float(os.environ.get("SR_TEXDETAIL", "0.25"))   # how much of the tiled texture's grain to keep
MATCH_M = float(os.environ.get("SR_MATCH_M", "48"))
GROUND_SAT = float(os.environ.get("SR_GROUND_SAT", "1.0"))          # colour-match scale (metres)


def material_sets(stg):
    s = struct.unpack_from("<4I", stg, 0)[1]
    n = struct.unpack_from("<I", stg, s)[0]
    p = s + 4
    out = []
    for _ in range(n):
        a, b, idx, cnt = struct.unpack_from("<ffII", stg, p); p += 16
        layers = {}
        for _ in range(cnt):
            tile, contrast, li = struct.unpack_from("<ffi", stg, p); p += 12
            L = struct.unpack_from("<H", stg, p)[0]
            layers[li] = (stg[p + 2:p + 1 + L].decode(errors="ignore"), max(0.5, tile))
            p += 2 + L
        if all(k in layers for k in (-1, 0, 1, 2)):
            out.append([layers[-1], layers[0], layers[1], layers[2]])
    return out


_texcache = {}


def layer_texture(name, tile_m, px_per_m):
    """Texture resampled so one repeat = tile_m metres at the output resolution (float RGB)."""
    n = max(4, int(round(tile_m * px_per_m)))
    key = (name, n)
    if key in _texcache:
        return _texcache[key]
    arr = None
    ed = M.editor()
    names = set(ed.namelist())
    for cand in (f"[textures]/dds/tiles_{name}__d_a.dds", f"[textures]/dds/tiles_{name}__d.dds"):
        if cand in names:
            try:
                im = Image.open(io.BytesIO(ed.read(cand)))
                im.draft("RGB", (512, 512))
                arr = np.asarray(im.convert("RGB").resize((n, n), Image.LANCZOS), np.float32)
            except Exception:
                arr = None
            break
    if arr is None:
        c = M.material_color(name)
        arr = np.tile(c, (n, n, 1)).astype(np.float32)
    else:
        mean = arr.reshape(-1, 3).mean(0)
        arr = mean + (arr - mean) * TEX_DETAIL        # top-down from height the grain averages out
    _texcache[key] = arr
    return arr


def terrain_crop(tex_w, tex_h, W, H):
    """Texel box of a terrain-wide texture that covers the terrain: textures sized near the heightmap are one
    texel per height sample padded to a multiple of 4 (crop the padding); power-of-two ones span it fully."""
    if abs(tex_w - W) <= 4 and abs(tex_h - H) <= 4:
        return (0, 0, W - 1, H - 1)
    return (0, 0, tex_w, tex_h)


def render_ground(z, level, stg, baked, half_x, half_z, px_per_m, size, log=print):
    """-> float32 (H, W, 3) sRGB ground colour at the output size, or None."""
    base = f"[textures]/pct/{level}_"
    bn = base + "blend_map__cmp.pct"
    if bn not in z.namelist():
        return None
    sets = material_sets(stg)
    if not sets:
        return None
    bw, bh, _ = M.pct_info(z, bn)
    pw, ph = (bw + 3) // 4 * 4, (bh + 3) // 4 * 4
    blend = Image.open(io.BytesIO(M.dds_wrap(b"DX10", M.tail(z, bn, pw * ph), pw, ph, 98))).convert("RGBA")
    TW_, TH_, _ = M.pct_info(z, base + "height_map.pct")
    blend = blend.crop(terrain_crop(bw, bh, TW_, TH_))
    # the blend map spans the terrain extent (texels beyond the terrain are padding)
    W, H = size
    wts = np.asarray(blend.convert("RGB").resize((W, H), Image.BILINEAR), np.float32) / 255.0
    wsum = np.clip(wts.sum(-1, keepdims=True), 0, 1)
    wts_n = wts / np.maximum(wts.sum(-1, keepdims=True), 1)        # layers never exceed full coverage

    # Layer colours solved from the game's baked map: baked ~= c0*(1-sum w) + c1*w1 + c2*w2 + c3*w3 (least squares)
    from collections import Counter
    common = Counter(tuple(x) for x in sets).most_common(1)[0][0]
    texs = [layer_texture(nm, tile, px_per_m) for nm, tile in common]
    tmeans = [t.reshape(-1, 3).mean(0) for t in texs]
    cols = None
    if baked is not None:
        bk = Image.fromarray(baked[..., :3]).crop((0, 0, int(round(2 * half_x / 0.75)), int(round(2 * half_z / 0.75))))
        sw, sh = bk.size
        wsm = np.asarray(blend.convert("RGB").resize((sw, sh), Image.BOX), np.float32) / 255.0
        wsm = wsm / np.maximum(wsm.sum(-1, keepdims=True), 1)
        A = np.concatenate([1 - wsm.sum(-1, keepdims=True), wsm], -1).reshape(-1, 4)
        Y = np.asarray(bk, np.float32).reshape(-1, 3)
        sel = np.random.default_rng(0).choice(len(A), min(len(A), 400000), replace=False)
        cols, *_ = np.linalg.lstsq(A[sel], Y[sel], rcond=None)
        cols = np.clip(cols, 10, 245)
        # the baked map carries a warm cast the live map doesn't have: neutralise part of it
        lum = cols @ np.array([0.299, 0.587, 0.114])
        cols = lum[:, None] + (cols - lum[:, None]) * GROUND_SAT
        pass
        log("  ground layer colours from baked map: " + ", ".join(f"{nm}={tuple(int(v) for v in c)}" for (nm, _), c in zip(common, cols)))
    out = np.zeros((H, W, 3), np.float32)
    ys, xs = np.mgrid[0:H, 0:W]
    s1 = 1 - np.clip(wts_n.sum(-1, keepdims=True), 0, 1)
    for li, t in enumerate(texs):
        n = t.shape[0]
        grain = t[ys % n, xs % n] - tmeans[li]
        base = cols[li] if cols is not None else tmeans[li]
        w = s1 if li == 0 else wts_n[..., li - 1:li]
        out += (base + grain) * w
    del ys, xs

    # match colours to the game's baked map at a ~6 m scale (keeps detail sharp, colours correct)
    if baked is not None:
        bk_up = np.asarray(bk.resize((W, H), Image.BICUBIC), np.float32)
        r = max(2, int(px_per_m * MATCH_M))
        small = (max(1, W // r), max(1, H // r))
        lo_b = np.asarray(Image.fromarray(bk_up.astype(np.uint8)).resize(small, Image.BOX).resize((W, H), Image.BICUBIC), np.float32)
        lo_d = np.asarray(Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).resize(small, Image.BOX).resize((W, H), Image.BICUBIC), np.float32)
        # match only brightness locally (keeps the neutralised hue)
        lb = lo_b.mean(-1, keepdims=True); ld = lo_d.mean(-1, keepdims=True)
        ratio = np.clip((lb + 4) / (ld + 4), 0.75, 1.3)
        out = out * ratio
    return np.clip(out, 0, 255)
