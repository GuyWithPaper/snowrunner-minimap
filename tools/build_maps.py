"""Render a top-down topo map PNG for every SnowRunner level, straight from the game's .pak files.

Layers used (all inside preload/paks/client/level_<id>.pak):
  height_map   uint16 heightfield (W x W)
  tint_map     BC5 two-channel terrain colour tint
  extrude_map  uint8 mud/deformable-ground mask (shows tracks and dirt roads)
  water_height float16 low-res water surface levels
Output: maps/<level>.png (+x = right/east, +z = down/south), maps/<level>.h.npy, maps/index.json
"""
import io, json, os, struct, sys, zipfile
import numpy as np
from PIL import Image, ImageFilter

GAME = r"C:\Program Files (x86)\Steam\steamapps\common\SnowRunner"
PAKS = os.path.join(GAME, "preload", "paks", "client")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps")
OUT_SIZE = 2048
WATER_MARGIN = float(os.environ.get("WM", "-0.004"))


def pct_info(z, name):
    h = z.read(name + "_header")
    w, hgt = struct.unpack_from("<II", h, 0x10)
    fmt = h[0x26]
    return w, hgt, fmt


def raw(z, name, nbytes):
    d = z.read(name)
    return d[-nbytes:]


def dds(fourcc, data, w, h):
    hd = (b"DDS " + struct.pack("<7I", 124, 0x81007, w, h, len(data), 0, 1) + b"\0" * 44
          + struct.pack("<II4s5I", 32, 4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0))
    return hd + data


def render(level, z):
    base = f"[textures]/pct/{level}_"
    names = set(z.namelist())
    hn = base + "height_map.pct"
    if hn not in names:
        return None
    W, H, _ = pct_info(z, hn)
    hm_full = np.frombuffer(raw(z, hn, W * H * 2), dtype="<u2").reshape(H, W).astype(np.float32)
    sc = OUT_SIZE / max(W, H)
    OW, OH = int(round(W * sc)), int(round(H * sc))
    size = (OW, OH)
    hm = np.array(Image.fromarray(hm_full).resize(size, Image.BILINEAR))

    # hillshade (light from north-west)
    gy, gx = np.gradient(hm)
    k = 0.012 * (W / OW)  # vertical exaggeration tuned by eye
    nx, ny, nz = -gx * k, -gy * k, np.ones_like(hm)
    n = np.sqrt(nx * nx + ny * ny + nz * nz)
    L = np.array([-0.6, -0.6, 0.55]); L /= np.linalg.norm(L)
    shade = np.clip((nx * L[0] + ny * L[1] + nz * L[2]) / n, 0, 1)

    # base colour from the tint map (BC5 RG)
    col = np.full((OH, OW, 3), (96, 112, 70), np.float32)
    tn = base + "tint_map__cmp.pct"
    if tn in names:
        tw, th, _ = pct_info(z, tn)
        pw, ph = (tw + 3) // 4 * 4, (th + 3) // 4 * 4
        im = Image.open(io.BytesIO(dds(b"ATI2", raw(z, tn, pw * ph), pw, ph))).convert("RGB").crop((0, 0, tw, th))
        t = np.asarray(im.resize(size, Image.BOX), np.float32)
        r, g = t[..., 0] / 255, t[..., 1] / 255
        grass = np.array([88, 118, 62], np.float32)
        dirt = np.array([150, 126, 88], np.float32)
        w = np.clip(r - g * 0.6 + 0.35, 0, 1)[..., None]
        col = grass * (1 - w) + dirt * w
        col *= (0.75 + 0.5 * ((r + g) / 2))[..., None]

    # mud / tracks from the extrude map
    en = base + "extrude_map.pct"
    if en in names:
        ew, eh, _ = pct_info(z, en)
        e = np.frombuffer(raw(z, en, ew * eh), np.uint8).reshape(eh, ew)
        e = np.asarray(Image.fromarray(e).resize(size, Image.BOX), np.float32) / 255
        mud = np.array([112, 84, 58], np.float32)
        a = (np.clip(e, 0, 1) * 0.55)[..., None]
        col = col * (1 - a) + mud * a

    img = col * (0.35 + 0.8 * shade)[..., None]

    # water: low-res float16 water levels; fit their units to the heightmap per level
    wn = base + "water_height_map.pct"
    if wn in names:
        ww, wh, _ = pct_info(z, wn)
        wr = np.frombuffer(raw(z, wn, ww * wh * 4), "<u2").reshape(wh, ww, 2)[..., 0]
        wf = wr.view("<f2").astype(np.float32)
        mask = wr < 0x7000
        if mask.sum() > 20:
            cy, cx = H // wh, W // ww
            cells = hm_full[: wh * cy, : ww * cx].reshape(wh, cy, ww, cx)
            cmin = cells.min(axis=(1, 3)); cmax = cells.max(axis=(1, 3))
            A, B = np.polyfit(wf[mask], cmin[mask], 1)
            lvl = A * wf + B + WATER_MARGIN * 65535
            shore = mask & (lvl > cmin) & (lvl < cmax)
            flooded = mask & (lvl >= cmax)
            # a fully flooded cell only counts if it connects to a real shoreline
            keep = shore.copy()
            for _ in range(max(wh, ww)):
                g = keep.copy()
                g[1:] |= keep[:-1]; g[:-1] |= keep[1:]; g[:, 1:] |= keep[:, :-1]; g[:, :-1] |= keep[:, 1:]
                g &= (shore | flooded)
                if (g == keep).all():
                    break
                keep = g
            lvl = np.where(keep, lvl, -1e9).astype(np.float32)
            lvl_up = np.asarray(Image.fromarray(lvl).resize(size, Image.NEAREST))
            m_up = np.asarray(Image.fromarray(keep.astype(np.float32)).resize(size, Image.BILINEAR))
            water = ((lvl_up - hm) > 0) & (m_up > 0.3)
            wmask = blur_f(water.astype(np.float32), 3)
            wc = np.array([62, 108, 146], np.float32)
            wa = (np.clip(wmask * 1.4, 0, 1) * 0.78)[..., None]
            img = img * (1 - wa) + wc * wa

    # contour lines
    levels = hm / (hm.max() / 40 + 1e-6)
    edge = (np.floor(levels) != np.floor(np.roll(levels, 1, 0))) | (np.floor(levels) != np.floor(np.roll(levels, 1, 1)))
    img[edge] *= 0.82

    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).filter(ImageFilter.SMOOTH)
    # world extent: heightmap is 0.5 m per texel, centred on the origin (verified against saved truck heights)
    meta = {"w": W, "h": H, "half_x": (W - 1) / 4.0, "half_z": (H - 1) / 4.0, "img_w": OW, "img_h": OH}
    small = np.asarray(Image.fromarray(hm_full).resize((128, max(1, int(128 * H / W))), Image.BOX), np.float32)
    np.save(os.path.join(OUT, level + ".h.npy"), small.astype(np.uint16))
    return out, meta


def blur_f(a, r):
    """cheap float blur: shrink then bilinear upsample"""
    n = max(4, int(a.shape[0] / max(r, 1)))
    im = Image.fromarray(a.astype(np.float32))
    return np.asarray(im.resize((n, n), Image.BOX).resize(a.shape[::-1], Image.BILINEAR))


def _unused(hm, wv, valid):
    # choose scale s so that the water surface is just above terrain along most water cells:
    # minimise how many 'valid' water cells end up deep underground, pick median ratio near lakes
    samp = valid & (hm > 0)
    if samp.sum() < 100:
        return 1.0
    ratios = wv[samp] / hm[samp]
    # water sits at or slightly above the lowest terrain; low quantile of ratio is a stable fit
    return float(np.quantile(ratios, 0.05))


def main(only=None):
    os.makedirs(OUT, exist_ok=True)
    index = {}
    for fn in sorted(os.listdir(PAKS)):
        if not (fn.startswith("level_") and fn.endswith(".pak")):
            continue
        level = fn[:-4]
        if only and only not in level:
            continue
        if "main_menu" in level or "test_polygon" in level:
            continue
        try:
            with zipfile.ZipFile(os.path.join(PAKS, fn)) as z:
                r = render(level, z)
        except Exception as e:
            print("skip", level, e)
            continue
        if not r:
            continue
        img, meta = r
        img.save(os.path.join(OUT, level + ".png"), optimize=True)
        index[level] = meta
        print("ok", level)
    if not only:
        json.dump(index, open(os.path.join(OUT, "index.json"), "w"), indent=1)
    else:
        p = os.path.join(OUT, "index.json")
        old = json.load(open(p)) if os.path.exists(p) else {}
        old.update(index); json.dump(old, open(p, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
