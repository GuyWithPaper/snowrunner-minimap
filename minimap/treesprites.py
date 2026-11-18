"""Top-down sprites of SnowRunner's real plant meshes (shared.pak plants_*), textured with their leaf atlases.

Each species is rasterised once from above with per-pixel texture + alpha and a depth buffer, lit by the
navigation-map sun (from -x), then cached on disk. The map renderer stamps one per tree instance.
"""
import io, os, re, zipfile, hashlib
import numpy as np
from PIL import Image

import paths

RES = 0.125                 # metres per sprite pixel
CACHE = os.path.join(paths.DATA, "sprites")
SUN = np.array([-1.0, 1.0, 0.0]) / np.sqrt(2)     # direction towards the sun
_tex = {}
_mem = {}


def _texture(ref):
    if ref in _tex:
        return _tex[ref]
    import maprender
    arr = None
    stem = ref.replace("\\", "/").rsplit(".", 1)[0]
    names = set(maprender.editor().namelist())
    for c in (stem.replace("/", "_"), stem.rsplit("/", 1)[-1]):
        n = f"[textures]/dds/{c}.dds"
        if n in names:
            try:
                im = Image.open(io.BytesIO(maprender.editor().read(n)))
                im.draft("RGBA", (1024, 1024))
                arr = np.asarray(im.convert("RGBA").resize((512, 512), Image.BOX), np.float32)
            except Exception:
                arr = None
            break
    _tex[ref] = arr
    return arr


def mesh_name(plant):
    base = re.sub(r"_lmk$", "", plant.split("/")[-1].lower())
    return base


def render_sprite(base):
    """-> RGBA uint8 sprite (centre = trunk), or None."""
    import maprender, sr_mesh
    sh = maprender.shared()
    names = set(sh.namelist())
    cand = f"[meshes]/plants_{base}"
    if cand not in names:
        alt = re.sub(r"_(rus|us\d*|g|can|ru\d*)$", "", base)
        cand = f"[meshes]/plants_{alt}"
        if cand not in names:
            pref = sorted(n for n in names if n.startswith(f"[meshes]/plants_{alt}_") and "stump" not in n and "lying" not in n)
            if not pref:
                return None
            cand = pref[0]
    parsed = sr_mesh.parse_combinexmesh(sh.read(cand))
    mats = sr_mesh.parse_xml_materials(parsed["xml"])
    parts = sr_mesh.extract_visual_parts(parsed)
    if not parts:
        return None
    allp = np.concatenate([p["positions"] for p in parts])
    r = float(np.abs(allp[:, [0, 2]]).max()) + 0.3
    hmax = float(allp[:, 1].max())
    N = int(np.ceil(2 * r / RES)) | 1
    col = np.zeros((N, N, 3), np.float32); alpha = np.zeros((N, N), np.float32)
    zbuf = np.full((N, N), -1e9, np.float32)
    for part in parts:
        tex = _texture(mats.get(part["material"], {}).get("AlbedoMap", ""))
        P = part["positions"]; I = part["indices"].astype(np.int64)
        UV = part["uvs"] if part["uvs"] is not None else np.zeros((len(P), 2), np.float32)
        NRM = part["normals"]
        bark = "bark" in part["material"].lower() or "trunk" in part["material"].lower()
        px = (P[:, 0] + r) / RES; pz = (P[:, 2] + r) / RES
        for a, b, c in I:
            xs = np.array([px[a], px[b], px[c]]); zs = np.array([pz[a], pz[b], pz[c]])
            x0, x1 = int(max(0, np.floor(xs.min()))), int(min(N - 1, np.ceil(xs.max())))
            z0, z1 = int(max(0, np.floor(zs.min()))), int(min(N - 1, np.ceil(zs.max())))
            if x1 < x0 or z1 < z0:
                continue
            den = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2])
            if abs(den) < 1e-9:
                continue
            gx, gz = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(z0, z1 + 1) + 0.5)
            w0 = ((zs[1] - zs[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gz - zs[2])) / den
            w1 = ((zs[2] - zs[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gz - zs[2])) / den
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
            if not inside.any():
                continue
            y = w0 * P[a, 1] + w1 * P[b, 1] + w2 * P[c, 1]
            u = w0 * UV[a, 0] + w1 * UV[b, 0] + w2 * UV[c, 0]
            v = w0 * UV[a, 1] + w1 * UV[b, 1] + w2 * UV[c, 1]
            if tex is not None:
                tu = (np.mod(u, 1) * 511).astype(int); tv = (np.mod(v, 1) * 511).astype(int)
                rgba = tex[tv, tu]
            else:
                rgba = np.zeros(gx.shape + (4,), np.float32); rgba[...] = (90, 80, 60, 255)
            ok = inside & (rgba[..., 3] > 110) & (y > zbuf[z0:z1 + 1, x0:x1 + 1])
            if not ok.any():
                continue
            if NRM is not None:
                n = w0[..., None] * NRM[a] + w1[..., None] * NRM[b] + w2[..., None] * NRM[c]
                n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
                d = np.abs(n @ SUN) if not bark else np.clip(n @ SUN, 0, 1)
            else:
                d = np.full(gx.shape, 0.6)
            # canopy self-shading: higher = brighter, plus sun-facing side
            hf = np.clip(y / max(hmax, 0.1), 0, 1)
            light = 0.55 + 0.35 * d + 0.25 * hf
            zb = zbuf[z0:z1 + 1, x0:x1 + 1]; cb = col[z0:z1 + 1, x0:x1 + 1]; ab = alpha[z0:z1 + 1, x0:x1 + 1]
            zb[ok] = y[ok]; cb[ok] = rgba[..., :3][ok] * light[ok, None]; ab[ok] = 1.0
    if alpha.sum() < 4:
        return None
    # side of the canopy away from the sun is darker (top-down look of the game's map)
    gx, gz = np.meshgrid(np.arange(N) - N / 2, np.arange(N) - N / 2)
    side = 1.0 - 0.18 * np.clip(gx / (N / 2), -1, 1)
    col *= side[..., None]
    out = np.dstack([np.clip(col, 0, 255), alpha * 255]).astype(np.uint8)
    return out


def sprite(plant):
    """Cached sprite for a plant/landmark name: (RGBA array, metres per pixel) or None."""
    base = mesh_name(plant)
    if base in _mem:
        return _mem[base]
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, base + ".png")
    res = None
    if os.path.exists(p):
        res = np.asarray(Image.open(p).convert("RGBA"))
    else:
        try:
            res = render_sprite(base)
        except Exception:
            res = None
        if res is not None:
            Image.fromarray(res).save(p)
        else:
            open(p + ".none", "w").close() if not os.path.exists(p + ".none") else None
    if res is None and os.path.exists(p + ".none"):
        res = None
    _mem[base] = None if res is None else (res, RES)
    return _mem[base]
