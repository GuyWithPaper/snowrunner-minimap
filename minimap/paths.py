"""Where things live: the game install (any Steam library), and per-user app data."""
import os, re, sys, winreg

APP_NAME = "SnowRunnerMinimap"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), APP_NAME)
MAPS = os.path.join(DATA, "maps")
os.makedirs(MAPS, exist_ok=True)
SETTINGS = os.path.join(DATA, "settings.json")
LOG = os.path.join(DATA, "minimap.log")
CACHE = os.path.join(DATA, "reader_cache.json")
COLORS = os.path.join(DATA, "colors.json")


def steam_root():
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        v = winreg.QueryValueEx(k, name)[0]
                        if v and os.path.isdir(v):
                            return os.path.normpath(v)
                    except OSError:
                        pass
        except OSError:
            pass
    d = r"C:\Program Files (x86)\Steam"
    return d if os.path.isdir(d) else None


def game_dir():
    env = os.environ.get("SNOWRUNNER_DIR")
    if env and os.path.isdir(env):
        return env
    root = steam_root()
    libs = [root] if root else []
    if root:
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            libs += [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', open(vdf, encoding="utf-8", errors="ignore").read())]
        except OSError:
            pass
    for lib in libs:
        g = os.path.join(lib, "steamapps", "common", "SnowRunner")
        if os.path.isdir(os.path.join(g, "preload", "paks", "client")):
            return g
    # Epic / other stores: common default locations
    for g in (r"C:\Program Files\Epic Games\SnowRunner", r"D:\Epic Games\SnowRunner", r"C:\Program Files\Epic Games\SnowRunnerEpic"):
        if os.path.isdir(os.path.join(g, "preload", "paks", "client")):
            return g
    return None


def paks_dir():
    g = game_dir()
    return os.path.join(g, "preload", "paks", "client") if g else None


def userdata_root():
    root = steam_root()
    return os.path.join(root, "userdata") if root else None
