"""Map Runner: an always-on-top overlay that follows your truck on a map rendered from the game files."""
import ctypes, json, math, os, sys, threading, time, traceback, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths, settings as S

# folder overrides must be in place before the game-file modules resolve paths
_boot = S.load()
if _boot.get("game_dir"):
    os.environ["SNOWRUNNER_DIR"] = _boot["game_dir"]

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QObject, QPoint
from PySide6.QtGui import (QImage, QPainter, QPainterPath, QColor, QPen, QFont, QPolygonF, QTransform,
                           QGuiApplication, QIcon, QAction, QCursor, QBrush, QRadialGradient, QLinearGradient)
from PySide6.QtWidgets import (QApplication, QWidget, QSystemTrayIcon, QMenu, QMessageBox, QDialog, QVBoxLayout, QLabel,
                               QPushButton, QHBoxLayout, QInputDialog, QColorDialog, QLineEdit, QListWidget,
                               QListWidgetItem)

import reader as rd, levels, maprender, savedata, updater, icons
from settingswin import SettingsWindow, STYLE, dark_title_bar
from version import VERSION

u32 = ctypes.windll.user32
MAP_VERSION = 5


def log(*a):
    msg = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        if os.path.exists(paths.LOG) and os.path.getsize(paths.LOG) > 2_000_000:
            os.replace(paths.LOG, paths.LOG + ".old")
        with open(paths.LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def _excepthook(t, v, tb):
    log("UNHANDLED", "".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _excepthook
threading.excepthook = lambda a: log("THREAD", "".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))


# ---------------------------------------------------------------- map store (render + fog compositing)
class MapStore(QObject):
    ready = Signal(str)
    status = Signal(str)

    def __init__(self, quality):
        super().__init__()
        self.quality = quality
        self.color, self.meta, self.images, self.fog_sig = {}, {}, {}, {}
        self.busy = set()
        self.lock = threading.Lock()

    def path(self, lv):
        return os.path.join(maprender.OUT, lv + ".jpg")

    def _index(self):
        p = os.path.join(maprender.OUT, "index.json")
        try:
            return json.load(open(p))
        except Exception:
            return {}

    def up_to_date(self, lv):
        m = self._index().get(lv, {})
        return os.path.exists(self.path(lv)) and m.get("version") == MAP_VERSION and abs(m.get("px_per_m", 0) - self.quality) < 1e-6

    def ensure(self, lv, force=False):
        if (lv in self.color and not force) or lv in self.busy:
            return
        self.busy.add(lv)
        threading.Thread(target=self._load, args=(lv, force), daemon=True).start()

    def _load(self, lv, force):
        try:
            if force or not self.up_to_date(lv):
                self.status.emit(f"Drawing the map of {levels.display_name(lv)} from your game files (about a minute)…")
                maprender.build(lv, px_per_m=self.quality, log=log)
            from PIL import Image
            img = np.asarray(Image.open(self.path(lv)).convert("RGB")).copy()
            with self.lock:
                self.meta[lv] = self._index()[lv]
                self.color[lv] = img
                for k in [k for k in self.images if k[0] == lv]:
                    self.images.pop(k, None); self.fog_sig.pop(k, None)
            self.status.emit("")
            self.ready.emit(lv)
        except Exception:
            log("map load failed", lv, traceback.format_exc())
            self.status.emit(f"Could not draw the map for {levels.display_name(lv)}. See the log (Settings > Advanced).")
        finally:
            self.busy.discard(lv)

    def clear(self):
        with self.lock:
            self.color.clear(); self.meta.clear(); self.images.clear(); self.fog_sig.clear()

    def composite(self, lv, fog_on, save_dir):
        key = (lv, fog_on)
        sig, f = None, None
        if fog_on:
            p = os.path.join(save_dir or "", f"fog_{lv}.cfg")
            sig = os.path.getmtime(p) if os.path.exists(p) else None
            if sig is not None and self.fog_sig.get(key) != sig:
                f = levels.fog(lv, save_dir)
        if key in self.images and self.fog_sig.get(key) == sig:
            return self.images[key]
        img = self.color[lv]
        if fog_on and f is not None:
            g, (ox, oz, sx, sz) = f
            m = self.meta[lv]
            H, W = img.shape[:2]
            from PIL import Image
            ppm = m["px_per_m"]
            fx0 = (ox + m["half_x"]) * ppm; fz0 = (oz + m["half_z"]) * ppm
            fim = Image.fromarray(g).resize((max(1, int(sx * ppm)), max(1, int(sz * ppm))), Image.BILINEAR)
            canvas = Image.new("L", (W, H), 0)
            canvas.paste(fim, (int(round(fx0)), int(round(fz0))))
            fv = np.asarray(canvas, np.float32) / 255.0
            col = img.astype(np.float32)
            lum = col @ np.array([0.30, 0.59, 0.11], np.float32)
            grey = (lum * 0.55 + 70)[..., None] * np.array([1.0, 1.0, 1.04], np.float32)
            dark = grey * 0.42
            t = np.clip((fv - 0.5) * 2.0, 0, 1)[..., None]
            s = np.clip(fv * 2.0, 0, 1)[..., None]
            arr = np.clip((dark * (1 - s) + grey * s) * (1 - t) + col * t, 0, 255).astype(np.uint8)
        else:
            arr = img
        arr = np.ascontiguousarray(arr)
        q = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 3, QImage.Format_RGB888).copy()
        # pre-shrunk copies so zoomed-out views are smooth (no shimmer / jagged edges)
        levels_ = [q]
        while levels_[-1].width() > 512:
            levels_.append(levels_[-1].scaled(levels_[-1].width() // 2, levels_[-1].height() // 2,
                                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        self.images[key] = levels_
        self.fog_sig[key] = sig
        return levels_


class Welcome(QDialog):
    def __init__(self, hk, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Map Runner")
        self.setStyleSheet(STYLE)
        ip = os.path.join(paths.APP_DIR, "assets", "icon.ico")
        if os.path.exists(ip):
            self.setWindowIcon(QIcon(ip))
        v = QVBoxLayout(self); v.setContentsMargins(26, 22, 26, 20)
        h = QLabel("Map Runner is running"); h.setObjectName("h1"); v.addWidget(h)
        t = QLabel(
            "<p>Start SnowRunner and get in a truck; the minimap appears and follows you. The first time you visit a "
            "region its map is drawn from your game files (about a minute).</p>"
            "<p><b>Play in Borderless or Windowed mode</b> (SnowRunner: Options &gt; Graphics &gt; Display mode) so the "
            "overlay can draw on top of the game.</p>"
            f"<table cellspacing=6><tr><td><b>{hk['toggle']}</b></td><td>show / hide</td></tr>"
            f"<tr><td><b>{hk['big']}</b></td><td>big map (drag to pan, wheel to zoom, right-click for a waypoint)</td></tr>"
            f"<tr><td><b>{hk['zoom_in']} / {hk['zoom_out']}</b></td><td>zoom</td></tr>"
            f"<tr><td><b>{hk['unlock']}</b></td><td>move and resize</td></tr>"
            f"<tr><td><b>{hk['settings']}</b></td><td>settings</td></tr></table>"
            "<p>Everything else is in the tray icon menu.</p>")
        t.setWordWrap(True); t.setTextFormat(Qt.RichText); v.addWidget(t)
        row = QHBoxLayout(); row.addStretch(1)
        b1 = QPushButton("Open settings"); b1.clicked.connect(lambda: self.done(2))
        b2 = QPushButton("Got it"); b2.setObjectName("primary"); b2.clicked.connect(self.accept)
        row.addWidget(b1); row.addWidget(b2); v.addLayout(row)
        self.resize(520, 380)
        dark_title_bar(self)


# ---------------------------------------------------------------- overlay window
class Overlay(QWidget):
    sig_update = Signal(str, str, bool)
    sig_notify = Signal(str)

    def __init__(self):
        super().__init__()
        self.pending_update = None
        self.sig_update.connect(self._on_update_ready)
        self.sig_notify.connect(lambda m: self.notify(m, force=True))
        self.s = S.load()
        self.setWindowTitle("Map Runner")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.big = False
        self.locked = True
        self.drag = None
        self.pan = None            # big map: world point at view centre when the user pans
        self.big_zoom = self.s["big_zoom"]
        self.waypoint = {}         # level -> (x, z)
        self.pins = self._load_pins()   # level -> [{x, z, label, color}]
        self._progress = (None, set())  # (save mtime, completed ids)
        self._vehicles = {}             # level -> (sts mtime, [(name, tag, x, z)])
        self.reader = rd.Reader(log=log)
        self.detector = None
        self.store = MapStore(self.s["map_quality"])
        self.store.ready.connect(lambda lv: self.update())
        self.store.status.connect(self.set_status)
        self.save_dir = self.s.get("save_dir") or savedata.save_dir()
        self.use_metric = self._game_metric()
        self.state = None
        self.level = None
        self.trails = {}           # level -> [(x, z)]
        self.markers = {}
        self._fog_cache = {}
        self.speed = 0.0
        self._last = None
        self.game_running = False
        self.status = "Waiting for SnowRunner…"
        self.keys_down = set()
        self.settings_win = None
        self._binds = {}
        self.load_hotkeys()
        threading.Thread(target=self._init_detector, daemon=True).start()

        self.t_read = QTimer(self); self.t_read.timeout.connect(self.tick); self.t_read.start(33)
        self.t_keys = QTimer(self); self.t_keys.timeout.connect(self.poll_keys); self.t_keys.start(35)
        self.t_slow = QTimer(self); self.t_slow.timeout.connect(self.slow); self.t_slow.start(2000)
        self.t_upd = QTimer(self); self.t_upd.timeout.connect(self.check_updates)
        self.t_upd.start(int(self.s["update_hours"]) * 3600 * 1000)
        self.make_tray()
        self._build_search()
        try:
            import gameicons
            icons.set_glyphs(gameicons.load(maprender.PAKS))
        except Exception:
            log("game icons unavailable", traceback.format_exc())
        self.apply_geometry()
        self.slow()
        self.refresh_visibility()
        QTimer.singleShot(200, self.set_click_through)
        QTimer.singleShot(10000, self.check_updates)
        if not self.s.get("first_run_done"):
            QTimer.singleShot(600, self.first_run)

    # ---------- setup
    def _game_metric(self):
        if self.s["units"] != "auto":
            return self.s["units"] == "metric"
        try:
            return bool(savedata.complete_save(self.save_dir).get("metricSystem", 1))   # 0 = imperial in the save
        except Exception:
            return True

    def first_run(self):
        self.s["first_run_done"] = True; S.save(self.s)
        r = Welcome(self.s["hotkeys"]).exec()
        if r == 2:
            self.open_settings()

    def _init_detector(self):
        if not maprender.PAKS:
            self.status = "SnowRunner wasn't found. Set its folder in Settings > Advanced."; return
        if not os.path.exists(os.path.join(paths.MAPS, "levels.json")):
            self.status = "First start: indexing SnowRunner's regions (about 2 minutes)…"
        try:
            self.detector = levels.Detector()
        except Exception:
            log("index failed", traceback.format_exc()); self.status = "Could not read SnowRunner's regions. See the log."; return
        self.status = ""
        if not self.s["prerender_visited"]:
            return
        try:
            visited = savedata.complete_save(self.save_dir).get("visitedLevels", [])
            for lv in [(v if v.startswith("level_") else "level_" + v).lower() for v in visited]:
                if lv in self.detector.info and not self.store.up_to_date(lv):
                    while self.store.busy:
                        time.sleep(1)
                    if not self.s["prerender_visited"]:
                        return
                    log("pre-drawing", lv)
                    maprender.build(lv, px_per_m=self.store.quality, log=log)
        except Exception:
            log("prerender", traceback.format_exc())

    # ---------- save-derived layers
    PINS = os.path.join(paths.DATA, "pins.json")

    def _load_pins(self):
        try:
            return json.load(open(self.PINS, encoding="utf-8"))
        except Exception:
            return {}

    def _save_pins(self):
        try:
            json.dump(self.pins, open(self.PINS, "w", encoding="utf-8"), indent=1)
        except Exception:
            pass

    def completed(self):
        p = os.path.join(self.save_dir or "", "CompleteSave.cfg")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return self._progress[1]
        if self._progress[0] != mt:
            try:
                self._progress = (mt, levels.progress(savedata.complete_save(self.save_dir)))
            except Exception:
                self._progress = (mt, self._progress[1])
        return self._progress[1]

    def vehicles(self, lv):
        p = os.path.join(self.save_dir or "", f"sts_{lv}.cfg")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return []
        c = self._vehicles.get(lv)
        if c is None or c[0] != mt:
            try:
                v = [(n, t, x, z) for n, t, x, y, z in savedata.vehicles(lv, self.save_dir)
                     if t and not n.startswith(("frame_addon", "addon")) and (abs(x) > 5 or abs(z) > 5)]
            except Exception:
                v = []
            c = self._vehicles[lv] = (mt, v)
        return c[1]

    # ---------- settings
    def open_settings(self):
        if self.settings_win is None:
            self.settings_win = SettingsWindow(self.s)
            self.settings_win.changed.connect(self.on_setting)
            self.settings_win.action.connect(self.on_action)
            self.settings_win.finished.connect(lambda _: setattr(self, "settings_win", None))
        self.settings_win.show(); self.settings_win.raise_(); self.settings_win.activateWindow()
        self.refresh_visibility()

    def on_setting(self, key):
        if key in ("size", "position", "monitor", "shape", "big_size"):
            if key == "position" and self.s["position"] != "custom":
                self.s["x"] = self.s["y"] = None
            self.apply_geometry()
        elif key == "hotkeys":
            self.load_hotkeys(); self.update_tray()
        elif key == "units":
            self.use_metric = self._game_metric()
        elif key == "map_quality":
            self.store.quality = self.s["map_quality"]
            if self.level:
                self.store.ensure(self.level, force=True)
        elif key == "update_hours":
            self.t_upd.start(int(self.s["update_hours"]) * 3600 * 1000)
        elif key in ("show_mode", "hide_when_not_driving"):
            self.refresh_visibility()
        elif key == "trail_length":
            for lv in self.trails:
                self.trails[lv] = self.trails[lv][-int(self.s["trail_length"]):]
        self.apply_opacity()
        self.update()

    def on_action(self, a):
        if a == "unlock":
            if self.locked:
                self.k_unlock()
        elif a == "reset_position":
            self.s["position"] = "right_middle"; self.s["x"] = self.s["y"] = None; self.s["monitor"] = 0
            S.save(self.s); self.apply_geometry(); self.refresh_visibility()
        elif a == "clear_trail":
            self.trails.pop(self.level, None); self.update()
        elif a == "redraw" and self.level:
            self.store.ensure(self.level, force=True)
        elif a == "clear_maps":
            self.store.clear()
            for fn in os.listdir(paths.MAPS):
                if fn.endswith(".jpg"):
                    try: os.remove(os.path.join(paths.MAPS, fn))
                    except OSError: pass
            if self.level:
                self.store.ensure(self.level)
        elif a == "check_updates":
            self.check_updates(manual=True)
        elif a == "open_data":
            os.startfile(paths.DATA)
        elif a == "open_log":
            if os.path.exists(paths.LOG): os.startfile(paths.LOG)
        elif a == "diagnostics":
            QApplication.clipboard().setText(self.diagnostics()); self.notify("Diagnostics copied to the clipboard.", force=True)
        elif a == "reset":
            self.s.clear(); self.s.update(S.reset()); self.load_hotkeys(); self.apply_geometry(); self.apply_opacity(); self.update_tray(); self.update()

    def diagnostics(self):
        st = self.state
        lines = [f"Map Runner {VERSION}", f"Windows {sys.getwindowsversion().major}.{sys.getwindowsversion().build}",
                 f"Game dir: {paths.game_dir()}", f"Save dir: {self.save_dir}", f"Reader: {self.reader.status}",
                 f"Region: {self.level}", f"Truck: {None if st is None else (round(st.x, 1), round(st.y, 1), round(st.z, 1))}",
                 f"Screens: {[s.name() for s in QApplication.screens()]}"]
        try:
            lines += ["--- log tail ---"] + open(paths.LOG, encoding="utf-8").read().splitlines()[-40:]
        except Exception:
            pass
        return "\n".join(lines)

    # ---------- updates
    def check_updates(self, manual=False):
        """Check and download in the background; install only when SnowRunner isn't running (or on request)."""
        if not manual and not self.s["auto_update"]:
            return
        if getattr(self, "_upd_busy", False):
            return
        self._upd_busy = True
        def run():
            try:
                if not updater.can_self_update():
                    if manual: self.sig_notify.emit("Running from source; updates come from git.")
                    return
                r = updater.check(log=log)
                if not r:
                    if manual: self.sig_notify.emit(f"You're on the latest version ({VERSION}).")
                    return
                tag, url = r
                path = updater.download(url, tag, log=log)
                self.sig_update.emit(tag, path, bool(manual))
            except Exception as e:
                log("update failed", e)
                if manual: self.sig_notify.emit("The update couldn't be downloaded. It will retry later.")
            finally:
                self._upd_busy = False
        threading.Thread(target=run, daemon=True).start()

    def _on_update_ready(self, tag, path, manual):
        self.pending_update = (tag, path)
        if manual or not self.game_running:
            self.apply_update()
        else:
            self.notify(f"Map Runner {tag} is ready. It installs when you close SnowRunner.")

    def apply_update(self):
        if not getattr(self, "pending_update", None):
            return
        tag, path = self.pending_update
        self.pending_update = None
        log("installing update", tag)
        self.notify(f"Updating Map Runner to {tag}…", force=True)
        try:
            updater.run_setup(path)
            QTimer.singleShot(1500, QApplication.quit)
        except Exception as e:
            log("update failed", e)

    def notify(self, msg, force=False):
        log(msg)
        if getattr(self, "tray", None) and (force or self.s["notifications"]):
            self.tray.showMessage("Map Runner", msg, QSystemTrayIcon.Information, 4000)

    # ---------- tray
    def make_tray(self):
        ip = os.path.join(paths.APP_DIR, "assets", "icon.ico")
        self.tray = QSystemTrayIcon(QIcon(ip) if os.path.exists(ip) else self.windowIcon(), self)
        self.tray.activated.connect(lambda r: self.open_settings() if r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.update_tray()
        self.tray.show()

    def update_tray(self):
        hk = self.s["hotkeys"]
        m = QMenu()
        m.setStyleSheet("QMenu{background:#1b1e21;color:#eee;border:1px solid #333}QMenu::item:selected{background:#3a3f45}")
        def add(text, fn, key=None, checked=None):
            a = QAction(text + (f"\t{hk.get(key)}" if key and hk.get(key) else ""), m)
            if checked is not None:
                a.setCheckable(True); a.setChecked(checked)
            a.triggered.connect(fn); m.addAction(a)
        add("Settings…", self.open_settings, "settings")
        m.addSeparator()
        add("Show minimap", self.k_toggle, "toggle", self.s["visible"])
        add("Big map", self.k_big, "big")
        add("Rotate with truck", self.k_rotate, "rotate", self.s["rotate"])
        add("Fog of war", self.k_fog, "fog", self.s["fog"])
        add("Map markers", self.k_markers, "markers", self.s["markers"])
        add("Move / resize", self.k_unlock, "unlock")
        add("Reset position", lambda: self.on_action("reset_position"))
        m.addSeparator()
        add("Check for updates", lambda: self.check_updates(True))
        add(f"Quit Map Runner", QApplication.quit)
        self.tray.setContextMenu(m)
        self._tray_menu = m
        self.tray.setToolTip(f"Map Runner {VERSION}")

    # ---------- geometry / window
    def screen_geo(self):
        scr = QApplication.screens()
        i = int(self.s.get("monitor", 0))
        sc = scr[i] if 0 <= i < len(scr) else QGuiApplication.primaryScreen()
        return sc.availableGeometry()

    def apply_geometry(self):
        g = self.screen_geo()
        if self.big:
            sz = int(g.height() * float(self.s["big_size"]))
            sz = min(sz, g.width() - 40)
            self.setGeometry(g.center().x() - sz // 2, g.center().y() - sz // 2, sz, sz)
            return
        sz = int(self.s["size"]); h = sz + 32; m = 24
        pos = self.s["position"]
        if pos == "custom" and self.s["x"] is not None:
            x, y = self.s["x"], self.s["y"]
        elif pos == "top_left":
            x, y = g.left() + m, g.top() + m
        elif pos == "top_right":
            x, y = g.right() - sz - m, g.top() + m
        elif pos == "bottom_left":
            x, y = g.left() + m, g.bottom() - h - m
        elif pos == "bottom_right":
            x, y = g.right() - sz - m, g.bottom() - h - m
        else:  # right edge, between SnowRunner's task list and its truck controls
            x, y = g.right() - sz - m, g.top() + int(g.height() * 0.27)
        self.setGeometry(int(x), int(y), sz, h)

    def apply_opacity(self):
        self.setWindowOpacity(float(self.s["big_opacity"] if self.big else self.s["opacity"]))

    def refresh_visibility(self):
        want = self.s["visible"]
        if self.s["show_mode"] == "game" and not self.game_running:
            want = False
        if self.s["hide_when_not_driving"] and self.state is None and not self.big:
            want = False
        if not self.locked or self.big:
            want = True
        if self.settings_win is not None and self.s["visible"]:
            want = True          # preview while configuring
        if want != self.isVisible():
            self.setVisible(want)
            if want:
                QTimer.singleShot(30, self.set_click_through)

    # ---------- big map search
    def _build_search(self):
        css = ("QLineEdit{background:rgba(15,17,19,235);color:#eee;border:1px solid #3a4047;border-radius:7px;padding:6px 10px;font-size:12px}"
               "QListWidget{background:rgba(20,23,26,240);color:#e8e5dc;border:1px solid #3a4047;border-radius:7px;font-size:12px;outline:0}"
               "QListWidget::item{padding:5px 8px}QListWidget::item:selected{background:#3a3f45}")
        self.search = QLineEdit(self); self.search.setPlaceholderText("Search this region…  (garage, fuel, upgrade, task name)")
        self.search.setStyleSheet(css); self.search.setClearButtonEnabled(True)
        self.results = QListWidget(self); self.results.setStyleSheet(css)
        self.search.textChanged.connect(self._do_search)
        self.search.returnPressed.connect(lambda: self._pick(self.results.item(0)) if self.results.count() else None)
        self.results.itemClicked.connect(self._pick)
        self.search.hide(); self.results.hide()
        self._hilite = None

    def _layout_search(self):
        if not self.big:
            self.search.hide(); self.results.hide(); return
        W = self.width()
        w = min(360, int(W * 0.42))
        self.search.setGeometry(W - w - 16, 12, w, 32); self.search.show()
        self.results.setGeometry(W - w - 16, 48, w, min(300, self.height() - 80))
        self.results.setVisible(bool(self.search.text().strip()) and self.results.count() > 0)

    def _search_items(self):
        lv = self.level
        if not lv:
            return []
        items = [dict(m) for m in (self.markers.get(lv) or levels.markers(lv)) if m.get("name")]
        items += [{"name": n.replace("_", " ").title(), "icon": "_trailer" if "trailer" in n else "_vehicle", "x": x, "z": z, "id": t}
                  for n, t, x, z in self.vehicles(lv)]
        items += [{"name": p.get("label") or "Pin", "icon": "_pin:" + p.get("color", "#5AC8FF"), "x": p["x"], "z": p["z"], "id": "pin"}
                  for p in self.pins.get(lv, [])]
        return items

    def _do_search(self, text):
        self.results.clear()
        q = text.strip().lower()
        if not q:
            self.results.hide(); return
        done = self.completed()
        st = self.state
        hits = []
        for m in self._search_items():
            cat, _ = icons.category(m["icon"])
            hay = (m["name"] + " " + cat + " " + dict(S.MARKER_CATEGORIES).get(cat, "")).lower()
            if all(w in hay for w in q.split()):
                d = math.hypot(m["x"] - st.x, m["z"] - st.z) if st else 0
                hits.append((d, m, cat))
        hits.sort(key=lambda h: h[0])
        for d, m, cat in hits[:60]:
            fin = levels.is_done(m, done) if m.get("key") else False
            label = f"{m['name']}   ·   {dict(S.MARKER_CATEGORIES).get(cat, cat)}" + (f"   ·   {self.fmt_dist(d)}" if st else "") + ("   ✓" if fin else "")
            it = QListWidgetItem(label); it.setData(Qt.UserRole, (m["x"], m["z"])); self.results.addItem(it)
        self.results.setVisible(self.results.count() > 0)

    def _pick(self, item):
        if item is None:
            return
        x, z = item.data(Qt.UserRole)
        self.pan = (x, z)
        if not self.big_zoom or self.big_zoom < 0.8:
            self.big_zoom = 1.0
        self._hilite = (x, z, time.time())
        self.results.hide(); self.update()

    def progress_summary(self, lv):
        mk = self.markers.get(lv) or []
        done = self.completed()
        out = []
        for cats, label in ((("task", "contest"), "Tasks & contests"), (("upgrade",), "Upgrades"), (("tower",), "Watchtowers")):
            sel = [m for m in mk if icons.category(m["icon"])[0] in cats]
            if sel:
                out.append(f"{label} {sum(levels.is_done(m, done) for m in sel)}/{len(sel)}")
        return "   ·   ".join(out)

    def set_click_through(self):
        hwnd = int(self.winId())
        st = u32.GetWindowLongW(hwnd, -20)
        st |= 0x80000 | 0x80                       # LAYERED | TOOLWINDOW
        if self.big:
            st &= ~0x08000000                       # big map can take keyboard focus (search box)
        else:
            st |= 0x08000000                        # minimap never steals focus from the game
        if self.locked and not self.big:
            st |= 0x20                              # TRANSPARENT: clicks pass to the game
        else:
            st &= ~0x20
        u32.SetWindowLongW(hwnd, -20, st)

    def set_status(self, s):
        self.status = s; self.update()

    # ---------- hotkeys (polled so they work while the game has focus)
    def load_hotkeys(self):
        acts = {"toggle": self.k_toggle, "big": self.k_big, "zoom_in": lambda: self.k_zoom(1.25), "zoom_out": lambda: self.k_zoom(0.8),
                "rotate": self.k_rotate, "fog": self.k_fog, "markers": self.k_markers, "unlock": self.k_unlock,
                "settings": self.open_settings, "clear_trail": lambda: self.on_action("clear_trail")}
        self._binds = {}
        for k, fn in acts.items():
            hk = S.parse_hotkey(self.s["hotkeys"].get(k, ""))
            if hk:
                self._binds[hk] = fn
                if hk[1] == 0xBB:                     # '=' also on the numpad
                    self._binds[(hk[0], 0x6B)] = fn
                if hk[1] == 0xBD:
                    self._binds[(hk[0], 0x6D)] = fn

    def poll_keys(self):
        if self.settings_win is not None and self.settings_win.isActiveWindow():
            return                                   # don't fire while the user is recording a hotkey
        down = lambda vk: u32.GetAsyncKeyState(vk) & 0x8000
        held = frozenset(v for v in (0x11, 0x10, 0x12, 0x5B) if down(v))
        for (mods, vk), fn in self._binds.items():
            if mods == held and down(vk):
                if (mods, vk) not in self.keys_down:
                    self.keys_down.add((mods, vk)); fn()
            else:
                self.keys_down.discard((mods, vk))
        esc = bool(down(0x1B))
        if self.big and esc and not getattr(self, "_esc", False):   # Esc closes the big map
            if self.search.hasFocus() and self.search.text():
                self.search.clear()
            else:
                self.k_big()
        self._esc = esc

    def k_toggle(self):
        self.s["visible"] = not self.s["visible"]; S.save(self.s); self.refresh_visibility(); self.update_tray()

    def k_big(self):
        self.big = not self.big
        self.pan = None
        if self.big:
            self._prev_fg = u32.GetForegroundWindow()
        else:
            self.search.clear(); self.search.clearFocus()
            if getattr(self, "_prev_fg", None):
                u32.SetForegroundWindow(self._prev_fg)     # hand the keyboard back to the game
        if self.big and self.s["big_view"] == "whole":
            self.big_zoom = None
        elif self.big:
            self.big_zoom = self.s["big_zoom"]
        self.apply_geometry(); self.apply_opacity(); self.refresh_visibility(); self.set_click_through()
        self._layout_search(); self.update()

    def resizeEvent(self, e):
        self._layout_search()

    def k_zoom(self, f):
        if self.big:
            z = self.big_zoom or self._whole_zoom()
            self.big_zoom = float(np.clip(z * f, 0.05, 8)); self.s["big_zoom"] = self.big_zoom
        else:
            self.s["zoom"] = float(np.clip(self.s["zoom"] * f, 0.2, 8))
        S.save(self.s); self.update()

    def k_rotate(self):
        self.s["rotate"] = not self.s["rotate"]; S.save(self.s); self.update_tray(); self.update()

    def k_fog(self):
        self.s["fog"] = not self.s["fog"]; S.save(self.s); self.update_tray(); self.update()

    def k_markers(self):
        self.s["markers"] = not self.s["markers"]; S.save(self.s); self.update_tray(); self.update()

    def k_unlock(self):
        self.locked = not self.locked
        if self.locked:
            g = self.geometry(); self.s["x"], self.s["y"] = g.x(), g.y(); self.s["position"] = "custom"; S.save(self.s)
        self.set_click_through(); self.refresh_visibility(); self.update()

    # ---------- mouse: move mode on the minimap, pan / zoom / waypoint on the big map
    def _whole_zoom(self):
        m = self.store.meta.get(self.level)
        if not m:
            return 0.5
        return min(self.width(), self.height()) / (2 * max(m["half_x"], m["half_z"]))

    def _view(self):
        """(transform world->screen, zoom) for the current frame."""
        W, H = self.width(), self.height()
        st = self.state
        if self.big:
            zoom = self.big_zoom or self._whole_zoom()
            if self.pan is not None:
                fx, fz = self.pan
            elif self.big_zoom is None or st is None:
                fx, fz = 0.0, 0.0
            else:
                fx, fz = st.x, st.z
            cy = H / 2
        else:
            zoom = float(self.s["zoom"]); fx, fz = (st.x, st.z) if st else (0.0, 0.0); cy = W / 2
        t = QTransform(); t.translate(W / 2, cy)
        if self.s["rotate"] and not self.big and st is not None:
            t.rotate(-st.heading)
        t.scale(zoom, -zoom); t.translate(-fx, -fz)          # world +z is north = up on screen
        return t, zoom, (fx, fz)

    def mousePressEvent(self, e):
        if self.big:
            self.results.hide()
            if e.button() == Qt.LeftButton:
                t, zoom, c = self._view()
                self.drag = (e.position(), c)
            elif e.button() == Qt.RightButton and self.level:
                self.map_menu(e)
            return
        if not self.locked and e.button() == Qt.LeftButton:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def map_menu(self, e):
        lv = self.level
        t, z, _ = self._view()
        w = t.inverted()[0].map(e.position()); wx, wz = w.x(), w.y()
        near = lambda x, y: math.hypot(x - wx, y - wz) * z < 14
        pin = next((p for p in self.pins.get(lv, []) if near(p["x"], p["z"])), None)
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#1b1e21;color:#eee;border:1px solid #333}QMenu::item:selected{background:#3a3f45}")
        a_wp = m.addAction("Set waypoint here")
        a_clr = m.addAction("Clear waypoint") if lv in self.waypoint else None
        m.addSeparator()
        a_pin = m.addAction("Add pin here...")
        a_ren = m.addAction("Rename pin...") if pin else None
        a_col = m.addAction("Pin colour...") if pin else None
        a_del = m.addAction("Remove pin") if pin else None
        if self.state is not None:
            m.addSeparator()
            a_dist = m.addAction("Distance from truck: " + self.fmt_dist(math.hypot(wx - self.state.x, wz - self.state.z)))
            a_dist.setEnabled(False)
        r = m.exec(e.globalPosition().toPoint())
        if r is None:
            return
        if r == a_wp:
            self.waypoint[lv] = (wx, wz)
        elif r == a_clr:
            self.waypoint.pop(lv, None)
        elif r == a_pin:
            label, ok = QInputDialog.getText(self, "Add pin", "Name (optional):")
            if ok:
                self.pins.setdefault(lv, []).append({"x": wx, "z": wz, "label": label.strip(), "color": "#5AC8FF"})
                self._save_pins()
        elif pin and r == a_ren:
            label, ok = QInputDialog.getText(self, "Rename pin", "Name:", text=pin.get("label", ""))
            if ok:
                pin["label"] = label.strip(); self._save_pins()
        elif pin and r == a_col:
            c = QColorDialog.getColor(QColor(pin.get("color", "#5AC8FF")), self, "Pin colour")
            if c.isValid():
                pin["color"] = c.name(); self._save_pins()
        elif pin and r == a_del:
            self.pins[lv].remove(pin); self._save_pins()
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag is None:
            return
        if self.big:
            start, (fx, fz) = self.drag
            z = self.big_zoom or self._whole_zoom()
            if self.big_zoom is None:
                self.big_zoom = z
            d = e.position() - start
            self.pan = (fx - d.x() / z, fz + d.y() / z); self.update()
        else:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def mouseDoubleClickEvent(self, e):
        if self.big:
            self.pan = None; self.update()           # double-click recentres on the truck

    def wheelEvent(self, e):
        up = e.angleDelta().y() > 0
        if self.big:
            t, z, _ = self._view()
            w0 = t.inverted()[0].map(e.position())
            self.k_zoom(1.2 if up else 1 / 1.2)
            t2, z2, c2 = self._view()
            w1 = t2.inverted()[0].map(e.position())
            self.pan = (c2[0] + w0.x() - w1.x(), c2[1] + w0.y() - w1.y()); self.update()
            return
        if self.locked:
            return
        self.s["size"] = int(np.clip(self.s["size"] + (20 if up else -20), 160, 900))
        g = self.geometry(); self.s["x"], self.s["y"] = g.x(), g.y(); self.s["position"] = "custom"
        self.apply_geometry(); S.save(self.s); self.update()

    # ---------- data
    def slow(self):
        if not self.reader.P:
            ok = self.reader.attach()
            self.game_running = ok
            self.status = "Connected. Get in a truck." if ok else {"waiting for SnowRunner": "Waiting for SnowRunner…"}.get(self.reader.status, self.reader.status)
            self.refresh_visibility(); self.update()
        if self.save_dir is None:
            self.save_dir = savedata.save_dir()

    def tick(self):
        if not self.reader.P:
            return
        try:
            st = self.reader.read()
        except ConnectionError:
            self.state = None; self.level = None; self.game_running = False
            if self.pending_update:
                QTimer.singleShot(3000, self.apply_update)
            if self.detector: self.detector.reset()
            self.status = "Waiting for SnowRunner…"; self.refresh_visibility(); self.update(); return
        except Exception:
            log("read error", traceback.format_exc()); return
        if st is None:
            if self.state is not None:
                self.status = "Not driving (garage or menu)"
            self.state = None; self.refresh_visibility(); self.update(); return
        if self.state is not None and math.hypot(st.x - self.state.x, st.z - self.state.z) > 60 and self.detector:
            self.detector.samples = []               # switched truck or region
        if self._last:
            dt = st.t - self._last.t
            if dt > 0.2:
                v = math.hypot(st.x - self._last.x, st.z - self._last.z) / dt * 3.6
                self.speed = self.speed * 0.6 + v * 0.4 if v < 300 else self.speed
                self._last = st
        else:
            self._last = st
        first = self.state is None
        self.state = st
        if first:
            self.refresh_visibility()
        if self.detector is None:
            self.update(); return
        if not hasattr(self, "_hint_t") or time.time() - self._hint_t > 10:
            self._hint_t = time.time()
            try:
                self._hint = savedata.last_level(self.save_dir)
            except Exception:
                self._hint = None
        lv = self.detector.detect(st.x, st.y, st.z, hint=self._hint)
        if lv != self.level:
            self.level = lv
            if lv:
                log("region", lv, levels.display_name(lv))
                self.store.ensure(lv)
        if lv:
            tr = self.trails.setdefault(lv, [])
            if not tr or math.hypot(st.x - tr[-1][0], st.z - tr[-1][1]) > 3:
                tr.append((st.x, st.z))
                if len(tr) > self.s["trail_length"]:
                    del tr[: len(tr) - int(self.s["trail_length"])]
        if self.detector:
            self.status = "" if lv else "Finding which region you're in…"
        self.update()

    # ---------- drawing
    BEZEL = 9

    def _clip(self, W, H, D, inset=None):
        p = QPainterPath()
        b = self.BEZEL if inset is None else inset
        if self.big:
            p.addRoundedRect(QRectF(b * 0.4, b * 0.4, W - b * 0.8, H - b * 0.8), 16, 16)
        elif self.s["shape"] == "circle":
            p.addEllipse(QPointF(W / 2, D / 2), D / 2 - b, D / 2 - b)
        elif self.s["shape"] == "rounded":
            p.addRoundedRect(QRectF(b, b, D - 2 * b, D - 2 * b), D * 0.13, D * 0.13)
        else:
            p.addRoundedRect(QRectF(b, b, D - 2 * b, D - 2 * b), 6, 6)
        return p

    def fmt_dist(self, m):
        if self.use_metric:
            return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.1f} km"
        ft = m * 3.28084
        return f"{ft:.0f} ft" if ft < 1000 else f"{m / 1609.34:.1f} mi"

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        D = W if not self.big else min(W, H)
        cx, cy = W / 2, (D / 2 if not self.big else H / 2)
        clip = self._clip(W, H, D)
        p.setPen(Qt.NoPen); p.setBrush(QColor(18, 22, 24, 235)); p.drawPath(clip)
        lv, st = self.level, self.state
        have_map = lv in self.store.color and lv in self.store.meta
        rotate = self.s["rotate"] and not self.big
        if have_map:
            m = self.store.meta[lv]
            img = self.store.composite(lv, self.s["fog"], self.save_dir)
            t, zoom, (fx, fz) = self._view()
            p.save(); p.setClipPath(clip); p.setTransform(t)
            # pick the pyramid level closest to (but not below) screen resolution
            screen_ppm = zoom
            lvl = 0
            while lvl + 1 < len(img) and img[lvl + 1].width() / (2 * m["half_x"]) >= screen_ppm * 1.15:
                lvl += 1
            p.drawImage(QRectF(-m["half_x"], -m["half_z"], 2 * m["half_x"], 2 * m["half_z"]), img[lvl])
            tr = self.trails.get(lv, [])
            if self.s["trail"] and len(tr) > 1:
                pen = QPen(QColor(self.s["trail_color"])); pen.setWidthF(2.4 / zoom)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in tr]))
            wp = self.waypoint.get(lv)
            if wp and st is not None:
                pen = QPen(QColor(90, 200, 255, 230)); pen.setWidthF(2.4 / zoom); pen.setStyle(Qt.DashLine)
                p.setPen(pen); p.drawLine(QPointF(st.x, st.z), QPointF(*wp))
            p.restore()
            if self.s["markers"]:
                self.draw_markers(p, lv, t, clip, zoom)
            if wp:
                self.draw_waypoint(p, t, clip, wp, cx, cy, D)
            if self.big and self._hilite and time.time() - self._hilite[2] < 6:
                hp = t.map(QPointF(self._hilite[0], self._hilite[1]))
                k = (time.time() - self._hilite[2]) % 1.0
                p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(255, 210, 60, int(255 * (1 - k))), 3))
                p.drawEllipse(hp, 14 + 18 * k, 14 + 18 * k)
                QTimer.singleShot(40, self.update)
            if st is not None:
                self.draw_truck(p, t, st, rotate, cx, cy)
            if self.s["show_scale"]:
                self.draw_scale(p, W, H, D, cx, zoom)
        self.draw_frame(p, W, H, D, cx, cy, clip, rotate)
        self.draw_info(p, W, H, D, cx, cy, have_map)
        p.end()

    def draw_truck(self, p, t, st, rotate, cx, cy):
        p.save()
        if self.big:
            pt = t.map(QPointF(st.x, st.z)); sx, sy, ang = pt.x(), pt.y(), st.heading
        else:
            sx, sy, ang = cx, cy, (0 if rotate else st.heading)
        k = float(self.s["arrow_size"])
        p.translate(sx, sy)
        # soft halo so the arrow reads on any terrain
        g = QRadialGradient(QPointF(0, 0), 22 * k)
        g.setColorAt(0, QColor(0, 0, 0, 110)); g.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(g)); p.drawEllipse(QPointF(0, 0), 22 * k, 22 * k)
        p.rotate(ang); p.scale(k, k)
        arrow = QPolygonF([QPointF(0, -14), QPointF(10, 11), QPointF(0, 5.5), QPointF(-10, 11)])
        p.setPen(QPen(QColor(255, 255, 255), 2.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(self.s["arrow_color"])); p.drawPolygon(arrow)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 55))
        p.drawPolygon(QPolygonF([QPointF(0, -14), QPointF(10, 11), QPointF(0, 5.5)]))
        p.restore()

    # ---------- chrome
    def _font(self, size, weight=QFont.DemiBold):
        f = QFont("Segoe UI Variable Display", size, weight)
        if not f.exactMatch():
            f = QFont("Segoe UI", size, weight)
        return f

    def _pill(self, p, rect, radius=None, alpha=215):
        r = rect.height() / 2 if radius is None else radius
        p.setPen(QPen(QColor(255, 255, 255, 28), 1)); p.setBrush(QColor(16, 19, 22, alpha))
        p.drawRoundedRect(rect, r, r)

    def draw_scale(self, p, W, H, D, cx, zoom):
        target = (D * 0.2) / zoom
        meters = self._nice(target if self.use_metric else target * 3.28084)
        bl = meters * zoom / (1 if self.use_metric else 3.28084)
        unit = ("m" if meters < 1000 else "km") if self.use_metric else ("ft" if meters < 5280 else "mi")
        val = meters if unit in ("m", "ft") else meters / (1000 if self.use_metric else 5280)
        label = f"{val:g} {unit}"
        p.setFont(self._font(8))
        tw = p.fontMetrics().horizontalAdvance(label)
        if self.big:
            x0, yb = 22, H - 30
        else:
            x0, yb = cx - (bl + tw + 22) / 2, D - self.BEZEL - 30
        box = QRectF(x0 - 8, yb - 9, bl + tw + 26, 18)
        self._pill(p, box, alpha=170)
        p.setPen(QPen(QColor(255, 255, 255, 235), 2, Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QPointF(x0, yb + 3), QPointF(x0 + bl, yb + 3))
        p.drawLine(QPointF(x0, yb - 1), QPointF(x0, yb + 4)); p.drawLine(QPointF(x0 + bl, yb - 1), QPointF(x0 + bl, yb + 4))
        p.setPen(QColor(235, 235, 235)); p.drawText(QRectF(x0 + bl + 6, yb - 9, tw + 6, 18), Qt.AlignVCenter | Qt.AlignLeft, label)

    def draw_frame(self, p, W, H, D, cx, cy, clip, rotate):
        accent = QColor(self.s["accent"]) if (self.locked or self.big) else QColor(80, 200, 255)
        if self.big:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(0, 0, 0, 160), 4)); p.drawPath(clip)
            c = QColor(accent); c.setAlpha(170); p.setPen(QPen(c, 1.6)); p.drawPath(clip)
            return
        b = self.BEZEL
        outer = self._clip(W, H, D, inset=1.5)
        ring = outer.subtracted(clip)
        # inner shading so the map edge sinks under the bezel
        if self.s["shape"] == "circle":
            g = QRadialGradient(QPointF(cx, cy), D / 2 - b)
            g.setColorAt(0.82, QColor(0, 0, 0, 0)); g.setColorAt(1.0, QColor(0, 0, 0, 90))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(g)); p.drawPath(clip)
        # bezel
        lg = QLinearGradient(0, 0, 0, D)
        lg.setColorAt(0, QColor(58, 64, 70)); lg.setColorAt(0.5, QColor(30, 34, 38)); lg.setColorAt(1, QColor(18, 20, 23))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(lg)); p.drawPath(ring)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 1.2)); p.drawPath(outer)
        c = QColor(accent); c.setAlpha(150); p.setPen(QPen(c, 1.3)); p.drawPath(clip)
        # compass ticks and cardinal letters on the bezel (round minimap)
        if self.s["show_compass"] and self.s["shape"] == "circle":
            st = self.state
            base = math.radians(-st.heading) if (rotate and st is not None) else 0.0
            rr = D / 2 - b / 2 - 0.8
            for i in range(36):
                a = base + i * math.pi / 18
                major = i % 9 == 0
                if major:
                    continue
                l = 2.6 if i % 3 == 0 else 1.4
                x, y = cx + math.sin(a) * rr, cy - math.cos(a) * rr
                dx, dy = math.sin(a) * l, -math.cos(a) * l
                p.setPen(QPen(QColor(255, 255, 255, 120 if i % 3 == 0 else 60), 1.1))
                p.drawLine(QPointF(x - dx, y - dy), QPointF(x + dx, y + dy))
            for k, letter in enumerate("NESW"):
                a = base + k * math.pi / 2
                x, y = cx + math.sin(a) * rr, cy - math.cos(a) * rr
                if letter == "N":
                    p.setPen(QPen(QColor(0, 0, 0, 160), 1)); p.setBrush(QColor(214, 52, 44))
                    p.drawEllipse(QPointF(x, y), 9, 9)
                    p.setPen(QColor(255, 255, 255)); p.setFont(self._font(8, QFont.Bold))
                else:
                    p.setPen(QColor(225, 225, 225)); p.setFont(self._font(7, QFont.Bold))
                p.drawText(QRectF(x - 9, y - 9, 18, 18), Qt.AlignCenter, letter)
        elif self.s["show_compass"]:
            st = self.state
            a = math.radians(-st.heading) if (rotate and st is not None) else 0.0
            rr = D / 2 - b - 14
            x, y = cx + math.sin(a) * rr, cy - math.cos(a) * rr
            p.setPen(QPen(QColor(0, 0, 0, 160), 1)); p.setBrush(QColor(214, 52, 44)); p.drawEllipse(QPointF(x, y), 9, 9)
            p.setPen(QColor(255, 255, 255)); p.setFont(self._font(8, QFont.Bold))
            p.drawText(QRectF(x - 9, y - 9, 18, 18), Qt.AlignCenter, "N")

    def draw_waypoint(self, p, t, clip, wp, cx, cy, D):
        pt = t.map(QPointF(*wp))
        if clip.contains(pt):
            p.setPen(QPen(QColor(0, 0, 0, 200), 3)); p.setBrush(QColor(90, 200, 255))
            pin = QPainterPath(); x, y = pt.x(), pt.y()
            pin.moveTo(x, y); pin.cubicTo(x - 12, y - 14, x - 8, y - 26, x, y - 26); pin.cubicTo(x + 8, y - 26, x + 12, y - 14, x, y)
            p.drawPath(pin); p.setBrush(QColor(20, 30, 40)); p.drawEllipse(QPointF(x, y - 17), 3.5, 3.5)
        elif not self.big:
            a = math.atan2(pt.x() - cx, -(pt.y() - cy))
            rr = D / 2 - 14
            x, y = cx + math.sin(a) * rr, cy - math.cos(a) * rr
            p.save(); p.translate(x, y); p.rotate(math.degrees(a))
            p.setPen(QPen(QColor(0, 0, 0, 200), 2)); p.setBrush(QColor(90, 200, 255))
            p.drawPolygon(QPolygonF([QPointF(0, -9), QPointF(7, 5), QPointF(-7, 5)])); p.restore()

    def draw_info(self, p, W, H, D, cx, cy, have_map):
        lv, st = self.level, self.state
        name = levels.display_name(lv) if (lv and self.s["show_info"]) else ""
        extra = []
        if st is not None and lv and self.s["show_speed"]:
            extra.append(f"{self.speed:.0f} km/h" if self.use_metric else f"{self.speed / 1.609:.0f} mph")
        wp = self.waypoint.get(lv)
        if wp and st is not None:
            extra.append("WP " + self.fmt_dist(math.hypot(wp[0] - st.x, wp[1] - st.z)))
        if st is not None and self.s["show_coords"]:
            extra.append(f"{st.x:.0f}, {st.z:.0f}")
        status = self.status or ("Preparing map…" if lv and not have_map else "")
        hk = self.s["hotkeys"]
        if self.big:
            # header card
            p.setFont(self._font(15, QFont.Bold)); title = name or "Map Runner"
            tw = p.fontMetrics().horizontalAdvance(title)
            sub = "   ·   ".join(extra)
            prog = self.progress_summary(lv) if lv else ""
            p.setFont(self._font(9)); sw = max(p.fontMetrics().horizontalAdvance(sub), p.fontMetrics().horizontalAdvance(prog))
            card = QRectF(16, 14, max(tw, sw) + 34, 34 + (18 if sub else 0) + (18 if prog else 0))
            self._pill(p, card, radius=12, alpha=225)
            p.setPen(QColor(self.s["accent"])); p.setBrush(QColor(self.s["accent"])); p.drawRoundedRect(QRectF(16, 24, 3.5, card.height() - 20), 2, 2)
            p.setFont(self._font(15, QFont.Bold)); p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(30, 18, tw + 10, 28), Qt.AlignVCenter | Qt.AlignLeft, title)
            y = 46
            p.setFont(self._font(9))
            if sub:
                p.setPen(QColor(self.s["arrow_color"])); p.drawText(QRectF(30, y, sw + 10, 18), Qt.AlignVCenter | Qt.AlignLeft, sub); y += 18
            if prog:
                p.setPen(QColor(200, 200, 195)); p.drawText(QRectF(30, y, sw + 10, 18), Qt.AlignVCenter | Qt.AlignLeft, prog)
            # hint bar
            hint = f"Drag to pan   ·   Scroll to zoom   ·   Double-click to recentre   ·   Right-click for waypoint and pins   ·   {hk.get('big')} / Esc to close"
            p.setFont(self._font(8, QFont.Normal)); hw = p.fontMetrics().horizontalAdvance(hint)
            hb = QRectF(W / 2 - hw / 2 - 14, H - 38, hw + 28, 24)
            self._pill(p, hb, alpha=190); p.setPen(QColor(215, 215, 210)); p.drawText(hb, Qt.AlignCenter, hint)
            if status:
                self._status(p, W / 2, H / 2, status)
            return
        # minimap: name + speed pill under the dial
        if name or extra:
            p.setFont(self._font(9, QFont.Bold)); nw = p.fontMetrics().horizontalAdvance(name)
            ex = "  ·  ".join(extra)
            p.setFont(self._font(9)); ew = p.fontMetrics().horizontalAdvance(ex)
            gap = 10 if (name and ex) else 0
            total = nw + ew + gap + 24
            box = QRectF(cx - total / 2, D + 4, total, 22)
            self._pill(p, box, alpha=210)
            p.setFont(self._font(9, QFont.Bold)); p.setPen(QColor(245, 245, 240))
            p.drawText(QRectF(box.x() + 12, box.y(), nw + 2, 22), Qt.AlignVCenter | Qt.AlignLeft, name)
            p.setFont(self._font(9)); p.setPen(QColor(self.s["arrow_color"]))
            p.drawText(QRectF(box.x() + 12 + nw + gap, box.y(), ew + 4, 22), Qt.AlignVCenter | Qt.AlignLeft, ex)
        if status:
            self._status(p, cx, cy, status, max_w=D - 60)
        if not self.locked:
            p.setFont(self._font(7)); msg = f"Move mode: drag · wheel resizes · {hk.get('unlock')} locks"
            mw = p.fontMetrics().horizontalAdvance(msg)
            box = QRectF(cx - mw / 2 - 10, self.BEZEL + 18, mw + 20, 18); self._pill(p, box, alpha=220)
            p.setPen(QColor(120, 210, 255)); p.drawText(box, Qt.AlignCenter, msg)

    def _status(self, p, x, y, text, max_w=420):
        p.setFont(self._font(9))
        fm = p.fontMetrics()
        r = fm.boundingRect(0, 0, int(max_w), 200, Qt.AlignCenter | Qt.TextWordWrap, text)
        box = QRectF(x - r.width() / 2 - 14, y - r.height() / 2 - 9, r.width() + 28, r.height() + 18)
        self._pill(p, box, radius=10, alpha=225)
        p.setPen(QColor(240, 240, 235)); p.drawText(box, Qt.AlignCenter | Qt.TextWordWrap, text)

    def draw_markers(self, p, lv, t, clip, zoom):
        mk = self.markers.get(lv)
        if mk is None:
            mk = self.markers[lv] = levels.markers(lv)
        fog = None
        if self.s["fog"]:
            fog = self._fog_cache.get(lv)
            if fog is None or time.time() - fog[2] > 5:
                f = levels.fog(lv, self.save_dir)
                fog = self._fog_cache[lv] = (f[0], f[1], time.time()) if f else (None, None, time.time())
        flt = self.s["marker_filter"]
        size = (22 if self.big else 17) * float(self.s["marker_size"])
        lab_mode = self.s["labels"]
        show_labels = lab_mode == "always" or (lab_mode == "big" and self.big and zoom > 0.25)
        done = self.completed() if self.s["completed"] != "show" else set()
        items = list(mk)
        items += [{"id": tag, "icon": "_trailer" if "trailer" in n else "_vehicle", "key": "", "x": x, "z": z,
                   "name": n.replace("_", " ").title()} for n, tag, x, z in self.vehicles(lv)]
        items += [{"id": "pin%d" % i, "icon": "_pin:" + pn.get("color", "#5AC8FF"), "key": "", "x": pn["x"], "z": pn["z"],
                   "name": pn.get("label", ""), "_nofog": True} for i, pn in enumerate(self.pins.get(lv, []))]
        p.save(); p.setClipPath(clip)
        for m in items:
            cat, _ = icons.category(m["icon"])
            if not flt.get(cat, True):
                continue
            if not self.big and cat == "subtask" and zoom < 1.5:
                continue
            fin = bool(done) and levels.is_done(m, done)
            if fin and self.s["completed"] == "hide":
                continue
            if fog and fog[0] is not None and not m.get("_nofog"):
                g, (ox, oz, sx, sz), _ = fog
                h, w = g.shape
                u = int((m["x"] - ox) / sx * w); v = int((m["z"] - oz) / sz * h)
                if not (0 <= u < w and 0 <= v < h) or g[v, u] < 128:
                    continue
            pt = t.map(QPointF(m["x"], m["z"]))
            if not clip.contains(pt):
                continue
            lab = m["name"] if ((show_labels or cat == "pins") and m["name"] and (cat in icons.IMPORTANT or zoom > 0.9)) else None
            p.setOpacity(0.38 if fin else 1.0)
            icons.draw(p, pt.x(), pt.y(), m["icon"], size, lab)
        p.setOpacity(1.0)
        p.restore()

    def _text(self, p, rect, s, align=Qt.AlignCenter, size=None):
        if size:
            p.setFont(QFont("Segoe UI", size, QFont.DemiBold))
        p.setPen(QColor(0, 0, 0, 220))
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            p.drawText(rect.translated(dx, dy), align, s)
        p.setPen(QColor(255, 255, 255)); p.drawText(rect, align, s)

    @staticmethod
    def _nice(x):
        e = 10 ** math.floor(math.log10(max(x, 1e-6)))
        for k in (5, 2, 1):
            if k * e <= x:
                return k * e
        return e


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MapRunner")
    mtx = ctypes.windll.kernel32.CreateMutexW(None, False, "MapRunner_single_instance")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    app = QApplication(sys.argv)
    app.setApplicationName("Map Runner"); app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")
    ip = os.path.join(paths.APP_DIR, "assets", "icon.ico")
    if os.path.exists(ip):
        app.setWindowIcon(QIcon(ip))
    app.setQuitOnLastWindowClosed(False)
    w = Overlay()
    log(f"Map Runner {VERSION} started")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
