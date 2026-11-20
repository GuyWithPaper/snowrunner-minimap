"""Settings model: defaults, versioned JSON on disk (atomic writes), hotkey parsing, start-with-Windows."""
import copy, json, os, sys, tempfile, winreg

import paths

SCHEMA = 3

MARKER_CATEGORIES = [
    ("garage", "Garages"), ("fuel", "Fuel stations"), ("repair", "Repair / resupply"), ("tower", "Watchtowers"),
    ("trailer", "Trailer stores"), ("upgrade", "Upgrades & add-ons"), ("gate", "Gateways"),
    ("storage", "Warehouses & storage"), ("task", "Tasks"), ("subtask", "Task steps"), ("contest", "Contests"),
    ("delivery", "Delivery points"), ("vehicles", "Trucks & trailers found in the region"), ("pins", "My pins"),
]

ACTIONS = [
    ("toggle", "Show / hide minimap", "Ctrl+Shift+M"),
    ("big", "Big map", "Ctrl+Shift+F"),
    ("zoom_in", "Zoom in", "Ctrl+Shift+="),
    ("zoom_out", "Zoom out", "Ctrl+Shift+-"),
    ("rotate", "Rotate with truck / north up", "Ctrl+Shift+R"),
    ("fog", "Fog of war on / off", "Ctrl+Shift+G"),
    ("markers", "Map markers on / off", "Ctrl+Shift+K"),
    ("unlock", "Move / resize mode", "Ctrl+Shift+U"),
    ("settings", "Open settings", "Ctrl+Shift+O"),
    ("clear_trail", "Clear driven trail", "Ctrl+Shift+T"),
]

DEFAULTS = {
    "schema": SCHEMA,
    # general
    "start_with_windows": False,
    "show_mode": "game",            # game: only while SnowRunner runs | always
    "hide_when_not_driving": False,
    "notifications": True,
    "units": "auto",                # auto (from game save) | metric | imperial
    # overlay
    "shape": "circle",              # circle | square | rounded
    "size": 320,
    "opacity": 0.95,
    "position": "right_middle",     # top_left | top_right | right_middle | bottom_left | bottom_right | custom
    "x": None, "y": None,
    "monitor": 0,
    "rotate": True,
    "zoom": 1.1,
    "show_compass": True,
    "show_scale": True,
    "show_info": True,
    "show_speed": True,
    "show_coords": False,
    "accent": "#E6DCBE",
    "arrow_color": "#FFC400",
    "arrow_size": 1.0,
    # map
    "fog": True,
    "markers": True,
    "marker_size": 1.0,
    "marker_filter": {k: True for k, _ in MARKER_CATEGORIES},
    "labels": "big",                # never | big | always
    "completed": "dim",             # dim | hide | show  (finished tasks, found watchtowers, collected upgrades)
    "route": True,
    "route_style": "balanced",      # balanced | roads | shortest
    "route_color": "#4FC3FF",
    "game_waypoints": True,          # follow the waypoints set on the game's own map
    "trail": True,
    "trail_color": "#FFD23C",
    "trail_length": 4000,
    "map_quality": 3.0,             # px per metre when drawing maps
    "prerender_visited": True,
    # big map
    "big_size": 0.85,               # fraction of screen height
    "big_opacity": 0.97,
    "big_view": "follow",           # follow | whole
    "big_zoom": 0.55,
    # hotkeys
    "hotkeys": {k: v for k, _, v in ACTIONS},
    # updates
    "auto_update": True,
    "update_hours": 4,
    "skip_version": "",
    # advanced
    "game_dir": "",
    "save_dir": "",
    "visible": True,
    "first_run_done": False,
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def migrate(raw):
    """Bring older settings files forward."""
    if raw.get("schema", 1) < 2:
        if "big_size" in raw and raw["big_size"] > 2:      # v1 stored pixels
            raw["big_size"] = DEFAULTS["big_size"]
        if raw.get("x") is not None:
            raw["position"] = "custom"
        raw.pop("big_zoom", None)
        raw["schema"] = 2
    if raw.get("schema", 2) < 3:
        if raw.get("map_quality") in (1.0, 2.0):     # v1.1 defaults; maps are now drawn at 3 px per metre
            raw["map_quality"] = 3.0
        raw["schema"] = 3
    return raw


def load():
    try:
        with open(paths.SETTINGS, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    return _merge(DEFAULTS, migrate(raw))


def save(s):
    """Atomic write so a crash never leaves a half-written settings file."""
    d = os.path.dirname(paths.SETTINGS)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="settings", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, paths.SETTINGS)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def reset():
    s = copy.deepcopy(DEFAULTS)
    s["first_run_done"] = True
    save(s)
    return s


# ---------------------------------------------------------------- hotkeys
MODS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}
KEYS = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
KEYS.update({str(d): 0x30 + d for d in range(10)})
KEYS.update({f"F{i}": 0x6F + i for i in range(1, 13)})
KEYS.update({"=": 0xBB, "-": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD,
             "`": 0xC0, "\\": 0xDC, "Space": 0x20, "Tab": 0x09, "Home": 0x24, "End": 0x23, "PgUp": 0x21,
             "PgDown": 0x22, "Insert": 0x2D, "Delete": 0x2E, "Up": 0x26, "Down": 0x28, "Left": 0x25,
             "Right": 0x27, "Num+": 0x6B, "Num-": 0x6D, "Num*": 0x6A, "Num/": 0x6F,
             **{f"Num{i}": 0x60 + i for i in range(10)}})
VK_NAME = {v: k for k, v in KEYS.items()}


def parse_hotkey(text):
    """'Ctrl+Shift+M' -> (frozenset of modifier VKs, key VK) or None."""
    if not text:
        return None
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    # allow '+' / '=' as the key itself: "Ctrl+Shift++" splits into an empty last part
    if text.endswith("++"):
        parts.append("=")
    mods, key = set(), None
    for p in parts:
        lp = p.lower()
        if lp in ("ctrl", "control"):
            mods.add(MODS["ctrl"])
        elif lp == "shift":
            mods.add(MODS["shift"])
        elif lp == "alt":
            mods.add(MODS["alt"])
        elif lp in ("win", "meta"):
            mods.add(MODS["win"])
        else:
            k = KEYS.get(p) or KEYS.get(p.upper()) or KEYS.get(p.capitalize())
            if k is None:
                return None
            key = k
    if key is None:
        return None
    return frozenset(mods), key


def hotkey_text(mods, vk):
    names = []
    for n, v in (("Ctrl", 0x11), ("Shift", 0x10), ("Alt", 0x12), ("Win", 0x5B)):
        if v in mods:
            names.append(n)
    names.append(VK_NAME.get(vk, f"0x{vk:02X}"))
    return "+".join(names)


# ---------------------------------------------------------------- start with Windows
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "MapRunner"


def set_autostart(enabled):
    exe = sys.executable if getattr(sys, "frozen", False) else None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled and exe:
                winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{exe}" --tray')
            else:
                try:
                    winreg.DeleteValue(k, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def get_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        pass
    # the installer's "start with Windows" option puts a shortcut in the Startup folder instead
    return os.path.exists(os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup\Map Runner.lnk"))
