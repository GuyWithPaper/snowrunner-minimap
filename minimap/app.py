"""SnowRunner Minimap: an always-on-top overlay that follows your truck on a map rendered from the game files.

Hotkeys (work while the game has focus):
  Ctrl+Shift+M   show / hide
  Ctrl+Shift+F   big map (whole region, north up) / minimap
  Ctrl+Shift+=   zoom in          Ctrl+Shift+-   zoom out
  Ctrl+Shift+R   rotate with truck / north up
  Ctrl+Shift+G   fog of war on / off (reveal whole map)
  Ctrl+Shift+U   unlock to drag / resize (scroll wheel = size), press again to lock (click-through)
  Ctrl+Shift+Q   quit
"""
import ctypes, json, math, os, sys, threading, time, traceback
import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QObject
from PySide6.QtGui import (QImage, QPainter, QPainterPath, QColor, QPen, QBrush, QFont, QPolygonF,
                           QTransform, QGuiApplication, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QAction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths
import reader as rd, levels, maprender, savedata, updater, icons
from version import VERSION

SETTINGS = paths.SETTINGS
LOG = paths.LOG
u32 = ctypes.windll.user32


def log(*a):
    msg = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(msg, flush=True)
    try:
        open(LOG, "a", encoding="utf-8").write(msg + "\n")
    except Exception:
        pass


DEFAULTS = {"x": None, "y": None, "size": 320, "zoom": 1.1, "rotate": True, "fog": True,
            "big_size": 900, "visible": True, "opacity": 0.95}


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.load(open(SETTINGS)))
    except Exception:
        pass
    return s


# ---------------------------------------------------------------- map store (render + fog compositing)
class MapStore(QObject):
    ready = Signal(str)
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self.color = {}      # level -> np.uint8 HxWx3
        self.meta = {}
        self.images = {}     # (level, fog_on) -> QImage
        self.fog_sig = {}
        self.busy = set()
        self.lock = threading.Lock()

    def path(self, lv):
        return os.path.join(maprender.OUT, lv + ".jpg")

    def ensure(self, lv):
        if lv in self.color or lv in self.busy:
            return
        self.busy.add(lv)
        threading.Thread(target=self._load, args=(lv,), daemon=True).start()

    def _load(self, lv):
        try:
            idx_p = os.path.join(maprender.OUT, "index.json")
            idx = json.load(open(idx_p)) if os.path.exists(idx_p) else {}
            if not os.path.exists(self.path(lv)) or idx.get(lv, {}).get("version") != 3:
                self.status.emit(f"Drawing map of {levels.display_name(lv)} from game files (first time, ~1 min)…")
                maprender.build(lv, log=log)
                idx = json.load(open(idx_p))
            from PIL import Image
            img = np.asarray(Image.open(self.path(lv)).convert("RGB")).copy()
            with self.lock:
                self.color[lv] = img
                self.meta[lv] = idx[lv]
            self.ready.emit(lv)
        except Exception:
            log("map load failed", lv, traceback.format_exc())
            self.status.emit(f"Could not build map for {lv} (see minimap.log)")
        finally:
            self.busy.discard(lv)

    def composite(self, lv, fog_on, save_dir):
        """QImage for display; fog greys out what the player has not uncovered (like the game)."""
        key = (lv, fog_on)
        sig = None
        f = None
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
            # place the fog grid in map-pixel space
            fx0 = (ox + m["half_x"]) * m["px_per_m"]; fz0 = (oz + m["half_z"]) * m["px_per_m"]
            fw = sx * m["px_per_m"]; fh = sz * m["px_per_m"]
            fim = Image.fromarray(g).resize((max(1, int(fw)), max(1, int(fh))), Image.BILINEAR)
            canvas = Image.new("L", (W, H), 0)
            canvas.paste(fim, (int(round(fx0)), int(round(fz0))))
            fv = np.asarray(canvas, np.float32) / 255.0
            col = img.astype(np.float32)
            lum = col @ np.array([0.30, 0.59, 0.11], np.float32)
            grey = (lum * 0.55 + 70)[..., None] * np.array([1.0, 1.0, 1.04], np.float32)
            dark = grey * 0.42
            t = np.clip((fv - 0.5) * 2.0, 0, 1)[..., None]   # edge -> uncovered
            s = np.clip(fv * 2.0, 0, 1)[..., None]           # hidden -> edge
            out = (dark * (1 - s) + grey * s) * (1 - t) + col * t
            arr = np.clip(out, 0, 255).astype(np.uint8)
        else:
            arr = img
        arr = np.ascontiguousarray(arr)
        q = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 3, QImage.Format_RGB888).copy()
        self.images[key] = q
        self.fog_sig[key] = sig
        return q


# ---------------------------------------------------------------- overlay window
class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        self.s = load_settings()
        self.setWindowTitle("SnowRunner Minimap")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.big = False
        self.locked = True
        self.drag = None
        self.reader = rd.Reader(log=log)
        self.detector = None
        threading.Thread(target=self._init_detector, daemon=True).start()
        self.store = MapStore()
        self.store.ready.connect(lambda lv: self.update())
        self.store.status.connect(self.set_status)
        self.save_dir = savedata.save_dir()
        self.state = None
        self.level = None
        self.trail = []
        self.markers = {}
        self._fog_cache = {}
        self.speed = 0.0
        self._last = None
        self.status = "Waiting for SnowRunner…"
        self.keys_down = set()
        self.apply_geometry()
        self.setWindowOpacity(self.s["opacity"])

        self.t_read = QTimer(self); self.t_read.timeout.connect(self.tick); self.t_read.start(33)
        self.t_keys = QTimer(self); self.t_keys.timeout.connect(self.poll_keys); self.t_keys.start(40)
        self.t_slow = QTimer(self); self.t_slow.timeout.connect(self.slow); self.t_slow.start(2000)
        if self.s["visible"]:
            self.show()
        QTimer.singleShot(200, self.set_click_through)
        self.make_tray()
        self.slow()
        QTimer.singleShot(8000, self.check_updates)
        self.t_upd = QTimer(self); self.t_upd.timeout.connect(self.check_updates); self.t_upd.start(4 * 3600 * 1000)
        if not os.path.exists(SETTINGS):
            self.save()
            QTimer.singleShot(1500, lambda: self.notify("Minimap running. Ctrl+Shift+M show/hide, Ctrl+Shift+F big map, Ctrl+Shift+U move. Right-click the tray icon for more."))

    def _init_detector(self):
        if not maprender.PAKS:
            self.status = "SnowRunner install not found. Set SNOWRUNNER_DIR."; return
        if not os.path.exists(os.path.join(paths.MAPS, "levels.json")):
            self.status = "First run: indexing SnowRunner maps (about 2 minutes)…"
        try:
            self.detector = levels.Detector()
        except Exception:
            log("index failed", traceback.format_exc()); self.status = "Could not index maps (see log)"; return
        self.status = ""
        # draw maps of regions the player has visited ahead of time, one at a time
        try:
            visited = savedata.complete_save().get("visitedLevels", [])
            visited = [v if v.startswith("level_") else "level_" + v for v in visited]
            for lv in [v.lower() for v in visited]:
                if lv in self.detector.info and not os.path.exists(self.store.path(lv)):
                    while self.store.busy:
                        time.sleep(1)
                    log("pre-drawing", lv)
                    maprender.build(lv, log=log)
        except Exception:
            log("prerender", traceback.format_exc())

    # ---------- updates
    def check_updates(self, manual=False):
        def run():
            if not updater.can_self_update():
                if manual: self.notify("Running from source: updates come from git.")
                return
            r = updater.check(log=log)
            if not r:
                if manual: self.notify(f"You have the latest version ({VERSION}).")
                return
            tag, url = r
            self.notify(f"Updating to {tag}…")
            try:
                updater.install(url, log=log)
                QTimer.singleShot(0, QApplication.quit)
            except Exception as e:
                log("update failed", e); self.notify("Update download failed.")
        threading.Thread(target=run, daemon=True).start()

    def notify(self, msg):
        log(msg)
        if getattr(self, "tray", None):
            self.tray.showMessage("SnowRunner Minimap", msg, QSystemTrayIcon.Information, 4000)

    def make_tray(self):
        icon_p = os.path.join(paths.APP_DIR, "assets", "icon.ico")
        self.tray = QSystemTrayIcon(QIcon(icon_p) if os.path.exists(icon_p) else self.windowIcon(), self)
        m = QMenu()
        def add(text, fn):
            a = QAction(text, m); a.triggered.connect(fn); m.addAction(a)
        add("Show / hide   (Ctrl+Shift+M)", self.k_toggle)
        add("Big map   (Ctrl+Shift+F)", self.k_big)
        add("Rotate / north-up   (Ctrl+Shift+R)", self.k_rotate)
        add("Fog of war on/off   (Ctrl+Shift+G)", self.k_fog)
        add("Move / resize   (Ctrl+Shift+U)", self.k_unlock)
        m.addSeparator()
        add("Check for updates", lambda: self.check_updates(True))
        add("Open data folder", lambda: os.startfile(paths.DATA))
        add(f"Quit   (v{VERSION})", QApplication.quit)
        self.tray.setContextMenu(m)
        self.tray.setToolTip(f"SnowRunner Minimap {VERSION}")
        self.tray.show()

    # ---------- geometry / window
    def apply_geometry(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        if self.big:
            sz = min(self.s["big_size"], scr.height() - 80, scr.width() - 80)
            self.setGeometry(scr.center().x() - sz // 2, scr.center().y() - sz // 2, sz, sz)
        else:
            sz = self.s["size"]
            x = self.s["x"] if self.s["x"] is not None else scr.right() - sz - 24
            # right edge, between the task list (top) and the truck controls (bottom) of the game HUD
            y = self.s["y"] if self.s["y"] is not None else scr.top() + int(scr.height() * 0.27)
            self.setGeometry(x, y, sz, sz + 34)

    def save(self):
        try:
            json.dump(self.s, open(SETTINGS, "w"), indent=1)
        except Exception:
            pass

    def set_click_through(self):
        hwnd = int(self.winId())
        GWL_EXSTYLE = -20
        st = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        st |= 0x80000 | 0x80 | 0x08000000          # LAYERED | TOOLWINDOW | NOACTIVATE
        if self.locked:
            st |= 0x20                              # TRANSPARENT: clicks go to the game
        else:
            st &= ~0x20
        u32.SetWindowLongW(hwnd, GWL_EXSTYLE, st)

    def set_status(self, s):
        self.status = s
        self.update()

    # ---------- hotkeys (polled so they work while the game is focused)
    def poll_keys(self):
        down = lambda vk: u32.GetAsyncKeyState(vk) & 0x8000
        if not (down(0x11) and down(0x10)):     # Ctrl + Shift
            self.keys_down.clear(); return
        binds = {0x4D: self.k_toggle, 0x46: self.k_big, 0xBB: lambda: self.k_zoom(1.25), 0x6B: lambda: self.k_zoom(1.25),
                 0xBD: lambda: self.k_zoom(0.8), 0x6D: lambda: self.k_zoom(0.8), 0x52: self.k_rotate,
                 0x47: self.k_fog, 0x55: self.k_unlock, 0x51: self.k_quit}
        for vk, fn in binds.items():
            if down(vk):
                if vk not in self.keys_down:
                    self.keys_down.add(vk); fn()
            else:
                self.keys_down.discard(vk)

    def k_toggle(self):
        self.s["visible"] = not self.isVisible()
        self.setVisible(self.s["visible"]); self.save()
        if self.s["visible"]:
            QTimer.singleShot(50, self.set_click_through)

    def k_big(self):
        self.big = not self.big; self.apply_geometry(); self.update()

    def k_zoom(self, f):
        key = "big_zoom" if self.big else "zoom"
        self.s[key] = float(np.clip(self.s.get(key, 0.45 if self.big else 1.6) * f, 0.05, 8)); self.save(); self.update()

    def k_rotate(self):
        self.s["rotate"] = not self.s["rotate"]; self.save(); self.update()

    def k_fog(self):
        self.s["fog"] = not self.s["fog"]; self.save(); self.update()

    def k_unlock(self):
        self.locked = not self.locked; self.set_click_through(); self.update()
        if self.locked:
            g = self.geometry(); self.s["x"], self.s["y"] = g.x(), g.y(); self.save()

    def k_quit(self):
        QApplication.quit()

    # ---------- mouse (only when unlocked)
    def mousePressEvent(self, e):
        if not self.locked and e.button() == Qt.LeftButton:
            self.drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            self.move(e.globalPosition().toPoint() - self.drag)

    def mouseReleaseEvent(self, e):
        self.drag = None

    def wheelEvent(self, e):
        if self.locked or self.big:
            return
        self.s["size"] = int(np.clip(self.s["size"] + (20 if e.angleDelta().y() > 0 else -20), 160, 900))
        g = self.geometry(); self.s["x"], self.s["y"] = g.x(), g.y()
        self.apply_geometry(); self.save(); self.update()

    # ---------- data
    def slow(self):
        if not self.reader.P:
            if self.reader.attach():
                self.status = "Connected. Get in a truck."
            else:
                self.status = {"waiting for SnowRunner": "Waiting for SnowRunner…"}.get(self.reader.status, self.reader.status)
            self.update()
        if self.save_dir is None:
            self.save_dir = savedata.save_dir()

    def tick(self):
        if not self.reader.P:
            return
        try:
            st = self.reader.read()
        except ConnectionError:
            self.state = None; self.level = None; self.trail = []
            if self.detector: self.detector.reset()
            self.status = "Waiting for SnowRunner…"; self.update(); return
        except Exception:
            log("read error", traceback.format_exc()); return
        if st is None:
            if self.state is not None:
                self.status = "Not driving (garage / menu)"
            self.state = None; self.update(); return
        # teleport (switching trucks / maps): clear trail and re-detect
        if self.state is not None and math.hypot(st.x - self.state.x, st.z - self.state.z) > 60:
            self.trail = []; self.detector.samples = []
        if self._last:
            dt = st.t - self._last.t
            if dt > 0.2:
                v = math.hypot(st.x - self._last.x, st.z - self._last.z) / dt * 3.6
                self.speed = self.speed * 0.6 + v * 0.4 if v < 300 else self.speed
                self._last = st
        else:
            self._last = st
        self.state = st
        if self.detector is None:
            self.update(); return
        lv = self.detector.detect(st.x, st.y, st.z, hint=None)
        if lv != self.level:
            self.level = lv
            self.trail = []
            if lv:
                log("level", lv, levels.display_name(lv))
                self.store.ensure(lv)
        if lv and (not self.trail or math.hypot(st.x - self.trail[-1][0], st.z - self.trail[-1][1]) > 3):
            self.trail.append((st.x, st.z)); self.trail = self.trail[-4000:]
        if lv or self.detector:
            self.status = "" if lv else "Finding which map you're on…"
        self.update()

    # ---------- drawing
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        W, H = self.width(), self.height()
        D = W if not self.big else min(W, H)
        r = D / 2 - 4
        cx, cy = W / 2, (D / 2)
        clip = QPainterPath()
        if self.big:
            clip.addRoundedRect(QRectF(2, 2, W - 4, H - 4), 14, 14)
        else:
            clip.addEllipse(QPointF(cx, cy), r, r)

        # backdrop
        p.setPen(Qt.NoPen); p.setBrush(QColor(18, 22, 24, 235)); p.drawPath(clip)

        lv = self.level
        have_map = lv in self.store.color
        st = self.state
        rotate = self.s["rotate"] and not self.big
        if have_map:
            m = self.store.meta[lv]
            img = self.store.composite(lv, self.s["fog"], self.save_dir)
            ppm = m["px_per_m"]
            if self.big:
                zoom = self.s.get("big_zoom", min(W, H) / (2 * max(m["half_x"], m["half_z"])))
            else:
                zoom = self.s["zoom"]           # screen px per metre
            if st is not None:
                fx, fz = st.x, st.z
            else:
                fx, fz = 0.0, 0.0
            if self.big and "big_zoom" not in self.s:
                fx, fz = 0.0, 0.0
            p.save()
            p.setClipPath(clip)
            t = QTransform()
            t.translate(cx, cy if not self.big else H / 2)
            if rotate and st is not None:
                t.rotate(-st.heading)
            t.scale(zoom, zoom)
            t.translate(-fx, -fz)
            p.setTransform(t)
            # world rect of the image
            p.drawImage(QRectF(-m["half_x"], -m["half_z"], 2 * m["half_x"], 2 * m["half_z"]), img)
            # trail
            if len(self.trail) > 1:
                pen = QPen(QColor(255, 210, 60, 200)); pen.setWidthF(2.2 / zoom); pen.setCosmetic(False)
                p.setPen(pen); p.setBrush(Qt.NoBrush)
                p.drawPolyline(QPolygonF([QPointF(a, b) for a, b in self.trail]))
            p.restore()

            # map markers (garages, fuel, watchtowers, tasks...), shown once uncovered like the game
            self.draw_markers(p, lv, t, clip, zoom)

            # truck marker (screen space)
            if st is not None:
                p.save()
                if self.big:
                    sx = cx + (st.x - fx) * zoom; sy = H / 2 + (st.z - fz) * zoom
                    ang = st.heading
                else:
                    sx, sy = cx, cy
                    ang = 0 if rotate else st.heading
                p.translate(sx, sy); p.rotate(ang)
                arrow = QPolygonF([QPointF(0, -13), QPointF(9, 10), QPointF(0, 5), QPointF(-9, 10)])
                p.setPen(QPen(QColor(20, 20, 20), 2.5)); p.setBrush(QColor(255, 196, 0)); p.drawPolygon(arrow)
                p.restore()

            # compass N on the rim
            if not self.big:
                a = math.radians(-st.heading) if (rotate and st is not None) else 0.0
                nx, ny = cx + math.sin(a) * (r - 12), cy - math.cos(a) * (r - 12)
                p.setPen(Qt.NoPen); p.setBrush(QColor(180, 30, 30, 230)); p.drawEllipse(QPointF(nx, ny), 10, 10)
                p.setPen(QColor(255, 255, 255)); f = QFont("Segoe UI", 9, QFont.Bold); p.setFont(f)
                p.drawText(QRectF(nx - 10, ny - 10, 20, 20), Qt.AlignCenter, "N")

            # scale bar
            meters = self._nice((D * 0.22) / zoom)
            bl = meters * zoom
            yb = (D - 26) if not self.big else H - 24
            xb = cx - bl / 2
            p.setPen(QPen(QColor(0, 0, 0, 160), 5)); p.drawLine(QPointF(xb, yb), QPointF(xb + bl, yb))
            p.setPen(QPen(QColor(255, 255, 255), 2)); p.drawLine(QPointF(xb, yb), QPointF(xb + bl, yb))
            p.setFont(QFont("Segoe UI", 8)); self._text(p, QRectF(cx - 60, yb - 18, 120, 14), f"{meters:g} m" if meters < 1000 else f"{meters / 1000:g} km")

        # ring
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 200), 6)); p.drawPath(clip)
        p.setPen(QPen(QColor(230, 220, 190, 230) if self.locked else QColor(80, 200, 255), 2.5)); p.drawPath(clip)

        # info strip
        info = levels.display_name(lv) if lv else ""
        if st is not None and lv:
            info += f"   {self.speed:4.0f} km/h"
        status = self.status
        if lv and not have_map:
            status = status or "Preparing map…"
        p.setFont(QFont("Segoe UI", 9, QFont.Bold))
        if self.big:
            if info:
                self._text(p, QRectF(16, 10, W - 32, 20), info, Qt.AlignLeft)
            self._text(p, QRectF(16, 30, W - 32, 18), ("Fog of war ON" if self.s["fog"] else "Whole map revealed") + "   Ctrl+Shift+F close", Qt.AlignLeft, 8)
            if status:
                self._text(p, QRectF(0, H / 2 - 10, W, 20), status)
        else:
            if info:
                self._text(p, QRectF(0, D + 2, W, 16), info)
            if status:
                self._text(p, QRectF(10, cy - 30, W - 20, 60), status, Qt.AlignCenter | Qt.TextWordWrap)
            if not self.locked:
                self._text(p, QRectF(0, D + 17, W, 14), "UNLOCKED: drag / wheel = size, Ctrl+Shift+U to lock", size=7)
        p.end()

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
        p.save(); p.setClipPath(clip)
        size = 22 if self.big else 17
        show_labels = self.big and zoom > 0.25
        for m in mk:
            cat, _ = icons.category(m["icon"])
            if not self.big and cat == "subtask" and zoom < 1.5:
                continue
            if fog and fog[0] is not None:
                g, (ox, oz, sx, sz), _ = fog
                h, w = g.shape
                u = int((m["x"] - ox) / sx * w); v = int((m["z"] - oz) / sz * h)
                if not (0 <= u < w and 0 <= v < h) or g[v, u] < 128:
                    continue
            pt = t.map(QPointF(m["x"], m["z"]))
            if not clip.contains(pt):
                continue
            lab = m["name"] if (show_labels and (cat in icons.IMPORTANT or zoom > 0.9)) else None
            icons.draw(p, pt.x(), pt.y(), m["icon"], size, lab)
        p.restore()

    def _text(self, p, rect, s, align=Qt.AlignCenter, size=None):
        if size:
            p.setFont(QFont("Segoe UI", size))
        p.setPen(QColor(0, 0, 0, 220))
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            p.drawText(rect.translated(dx, dy), align, s)
        p.setPen(QColor(255, 255, 255))
        p.drawText(rect, align, s)

    @staticmethod
    def _nice(x):
        e = 10 ** math.floor(math.log10(max(x, 1e-6)))
        for k in (5, 2, 1):
            if k * e <= x:
                return k * e
        return e


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    mtx = ctypes.windll.kernel32.CreateMutexW(None, False, "SnowRunnerMinimap_single_instance")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return
    app = QApplication(sys.argv)
    icon_p = os.path.join(paths.APP_DIR, "assets", "icon.ico")
    if os.path.exists(icon_p):
        app.setWindowIcon(QIcon(icon_p))
    app.setQuitOnLastWindowClosed(False)
    w = Overlay()
    log("SnowRunner Minimap started")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
