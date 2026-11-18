"""The game's own map icon glyphs, cut at runtime from SnowRunner's UI atlas (gfx.pak icons_lib_i7a).

Nothing is shipped with the app: each user's copy reads its own game files. Rectangles are in the 512x512 atlas.
"""
import io, os, struct, zipfile
import numpy as np

ATLAS = "[textures]/ui/flash_auto/icons_lib_i7a"
RECTS = {
    "tower": (1, 150, 75, 224),
    "garage": (148, 158, 227, 216),
    "fuel": (308, 270, 347, 309),
    "repair": (133, 229, 171, 268),
    "trailer": (393, 280, 439, 303),
    "storage": (376, 148, 425, 195),
    "contest": (349, 359, 395, 403),
    "task": (490, 490, 512, 512),
    "delivery": (299, 168, 379, 206),
    "fire": (132, 360, 172, 402),
    "lumber": (303, 75, 375, 145),
    "factory": (0, 84, 70, 137),
    "water": (490, 196, 512, 224),
}


def glyph_key(icon, cat):
    """Which game glyph to use for a marker's icon name / category."""
    i = icon.lower()
    if "firedept" in i: return "fire"
    if "waterdrop" in i: return "water"
    if "lumber" in i: return "lumber"
    if "townstorage" in i or "factory" in i: return "factory"
    if cat == "subtask": return "task"
    if cat in RECTS: return cat
    if cat == "delivery": return "delivery"
    return None
_cache = None


def load(paks_dir):
    """-> {category: QImage white glyph with alpha} (empty dict on failure)."""
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        from PIL import Image
        from PySide6.QtGui import QImage
        import maprender
        g = zipfile.ZipFile(os.path.join(paks_dir, "gfx.pak"))
        d = g.read(ATLAS + ".pct")
        w, h = struct.unpack_from("<II", d, 0x10)
        pw, ph = (w + 3) // 4 * 4, (h + 3) // 4 * 4
        atlas = Image.open(io.BytesIO(maprender.dds_wrap(b"DX10", maprender.pct_payload(d)[:pw * ph], pw, ph, 98))).convert("RGBA")
        for cat, box in RECTS.items():
            im = atlas.crop(box)
            a = np.asarray(im)
            ys, xs = np.nonzero(a[..., 3] > 30)
            if len(xs) < 10:
                continue
            im = im.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
            # glyphs are white with dark outline baked in; keep as-is
            arr = np.ascontiguousarray(np.asarray(im.convert("RGBA")))
            q = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QImage.Format_RGBA8888).copy()
            _cache[cat] = q
    except Exception:
        _cache = {}
    return _cache
