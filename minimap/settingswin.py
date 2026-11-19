"""Settings window. Every change applies immediately and is saved; nothing needs an OK button."""
import os, webbrowser
from PySide6.QtCore import Qt, Signal, QTimer, QSize
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPalette, QFont, QPixmap, QPainter
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QWidget,
                               QLabel, QCheckBox, QComboBox, QSlider, QPushButton, QFormLayout, QGroupBox, QLineEdit,
                               QColorDialog, QFileDialog, QMessageBox, QGridLayout, QSpinBox, QDoubleSpinBox,
                               QScrollArea, QFrame, QSizePolicy, QApplication)

import paths, settings as S
from version import VERSION, REPO

STYLE = """
QDialog, QWidget#page { background: #16191c; color: #e4e1d8; }
QDialog#card { background: #16191c; border: 1px solid #2c3136; border-radius: 14px; }
QLabel { color: #e4e1d8; }
QLabel#h1 { font-size: 18px; font-weight: 600; color: #ffffff; }
QLabel#hint { color: #9a9890; font-size: 11px; }
QWidget#side { background: #101214; border-right: 1px solid #22262a; }
QListWidget { background: #101214; border: none; color: #cfccc3; font-size: 13px; outline: 0; }
QListWidget::item { padding: 10px 14px; border-left: 3px solid transparent; }
QListWidget::item:selected { background: #1f2327; color: #ffffff; border-left: 3px solid #ffc400; }
QListWidget::item:hover { background: #1a1d20; }
QGroupBox { border: 1px solid #2a2e33; border-radius: 8px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #ffc400; }
QCheckBox, QComboBox, QPushButton, QLineEdit, QSpinBox, QDoubleSpinBox { font-size: 12px; }
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox { background: #0f1113; border: 1px solid #2f343a; border-radius: 5px; padding: 4px 8px; color: #eee; min-height: 22px; }
QComboBox QAbstractItemView { background: #0f1113; color: #eee; selection-background-color: #3a3f45; }
QPushButton { background: #2a2f35; border: 1px solid #3a4047; border-radius: 6px; padding: 6px 14px; color: #f0ede4; }
QPushButton:hover { background: #343a41; }
QPushButton#primary { background: #ffc400; color: #1a1a1a; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #ffd23c; }
QPushButton#danger { color: #ff8c7a; }
QSlider::groove:horizontal { height: 4px; background: #2f343a; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffc400; width: 14px; margin: -6px 0; border-radius: 7px; }
QSlider::sub-page:horizontal { background: #b38a00; border-radius: 2px; }
QScrollArea { border: none; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #4a5058; border-radius: 4px; background: #0f1113; }
QCheckBox::indicator:hover { border-color: #ffc400; }
QCheckBox::indicator:checked { background: #ffc400; border-color: #ffc400; image: url(CHECK_PNG); }
QToolTip { background: #1b1e21; color: #eee; border: 1px solid #333; }
"""
STYLE = STYLE.replace("CHECK_PNG", os.path.join(paths.APP_DIR, "assets", "check.png").replace("\\", "/"))


def dark_title_bar(w):
    """Ask Windows 10/11 for a dark title bar on this window."""
    try:
        import ctypes
        v = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(int(w.winId()), attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                break
    except Exception:
        pass


def on_top(dlg):
    """Qt (non-native) dialog that stays above the always-on-top overlay."""
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
    dlg.setStyleSheet(STYLE)
    try:
        dark_title_bar(dlg)
    except Exception:
        pass
    return dlg


def pick_color(parent, current, title="Pick colour"):
    d = QColorDialog(QColor(current), parent)
    d.setOption(QColorDialog.DontUseNativeDialog, True); d.setWindowTitle(title); on_top(d)
    return d.selectedColor() if d.exec() else QColor()


def pick_folder(parent, title, start):
    d = QFileDialog(parent, title, start)
    d.setFileMode(QFileDialog.Directory); d.setOption(QFileDialog.ShowDirsOnly, True)
    d.setOption(QFileDialog.DontUseNativeDialog, True); on_top(d)
    return d.selectedFiles()[0] if d.exec() and d.selectedFiles() else ""


class Notice(QFrame):
    """Message strip shown inside the settings panel (never a popup window)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("notice")
        h = QHBoxLayout(self); h.setContentsMargins(14, 8, 8, 8); h.setSpacing(8)
        self.text = QLabel(); self.text.setWordWrap(True); h.addWidget(self.text, 1)
        self.yes = QPushButton("Confirm"); self.yes.setObjectName("primary")
        self.no = QPushButton("Cancel")
        self.close_b = QPushButton("Dismiss")
        for b in (self.yes, self.no, self.close_b):
            h.addWidget(b)
        self.no.clicked.connect(self.hide); self.close_b.clicked.connect(self.hide)
        self._cb = None
        self.yes.clicked.connect(self._confirm)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.hide)
        self.hide()

    def _confirm(self):
        cb, self._cb = self._cb, None
        self.hide()
        if cb:
            cb()

    def show_msg(self, text, kind="info", confirm=None, confirm_text="Confirm"):
        colors = {"info": "#2b3a4a", "warn": "#4a3a1c", "danger": "#4a2222"}
        self.setStyleSheet(f"QFrame#notice{{background:{colors.get(kind, colors['info'])};border:1px solid rgba(255,255,255,40);"
                           f"border-radius:9px}} QLabel{{color:#f2f0ea;font-size:12px}}")
        self.text.setText(text)
        self._cb = confirm
        self.yes.setText(confirm_text)
        self.yes.setVisible(confirm is not None); self.no.setVisible(confirm is not None)
        self.close_b.setVisible(confirm is None)
        self.show(); self.raise_()
        if confirm is None:
            self.timer.start(4500)
        else:
            self.timer.stop()


class HotkeyEdit(QPushButton):
    """Click, then press a key combination. Esc cancels, Backspace clears."""
    changed = Signal(str)
    problem = Signal(str)

    def __init__(self, text):
        super().__init__(text or "Not set")
        self.value = text
        self.listening = False
        self.setCheckable(True)
        self.setMinimumWidth(170)
        self.clicked.connect(self.start)

    def start(self):
        self.listening = True
        self.setText("Press keys…")
        self.grabKeyboard()

    def keyPressEvent(self, e):
        if not self.listening:
            return super().keyPressEvent(e)
        k = e.key()
        if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        self.releaseKeyboard(); self.listening = False; self.setChecked(False)
        if k == Qt.Key_Escape:
            self.setText(self.value or "Not set"); return
        if k in (Qt.Key_Backspace, Qt.Key_Delete) and not e.modifiers():
            self.value = ""; self.setText("Not set"); self.changed.emit(""); return
        vk = e.nativeVirtualKey()
        mods = set()
        m = e.modifiers()
        if m & Qt.ControlModifier: mods.add(0x11)
        if m & Qt.ShiftModifier: mods.add(0x10)
        if m & Qt.AltModifier: mods.add(0x12)
        if m & Qt.MetaModifier: mods.add(0x5B)
        if not mods:
            self.setText(self.value or "Not set")
            self.problem.emit("Hold Ctrl, Shift or Alt with the key, so the hotkey doesn't clash with driving controls. Click the box to try again.")
            return
        txt = S.hotkey_text(mods, vk)
        self.value = txt; self.setText(txt); self.changed.emit(txt)


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color):
        super().__init__()
        self.color = color
        self.setFixedSize(46, 24)
        self.clicked.connect(self.pick)
        self._paint()

    def _paint(self):
        self.setStyleSheet(f"background:{self.color}; border:1px solid #555; border-radius:5px;")

    def pick(self):
        c = pick_color(self.window(), self.color)
        if c.isValid():
            self.color = c.name(); self._paint(); self.changed.emit(self.color)


class SettingsWindow(QDialog):
    changed = Signal(str)          # key that changed
    action = Signal(str)           # buttons that ask the app to do something

    def __init__(self, s, parent=None, embedded=False):
        super().__init__(parent)
        self.s = s
        self.embedded = embedded
        self.setWindowTitle("Map Runner — Settings")
        if embedded:
            self.setWindowFlags(Qt.Widget)
            self.setObjectName("card")
            self.setAttribute(Qt.WA_StyledBackground, True)
        else:
            self.setWindowFlags(Qt.Window | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint)
        ip = os.path.join(paths.APP_DIR, "assets", "icon.ico")
        if os.path.exists(ip):
            self.setWindowIcon(QIcon(ip))
        self.setStyleSheet(STYLE)
        self.resize(860, 620)
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.nav = QListWidget(); self.nav.setFixedWidth(200)
        self.pages = QStackedWidget()
        side = QWidget(); side.setObjectName("side"); side.setFixedWidth(200)
        sv = QVBoxLayout(side); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(0)
        brand = QWidget(); bh = QHBoxLayout(brand); bh.setContentsMargins(16, 18, 12, 16); bh.setSpacing(10)
        ipng = os.path.join(paths.APP_DIR, "assets", "icon.png")
        if os.path.exists(ipng):
            lg = QLabel(); lg.setPixmap(QPixmap(ipng).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation)); bh.addWidget(lg)
        tt = QLabel(f"<div style='font-size:15px;font-weight:700;color:#fff'>Map Runner</div>"
                    f"<div style='font-size:11px;color:#8d8b84'>for SnowRunner · v{VERSION}</div>")
        tt.setTextFormat(Qt.RichText); bh.addWidget(tt, 1)
        sv.addWidget(brand); sv.addWidget(self.nav, 1)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        self.notice = Notice(right)
        nwrap = QWidget(); nl = QVBoxLayout(nwrap); nl.setContentsMargins(20, 12, 20, 0); nl.addWidget(self.notice)
        rv.addWidget(nwrap); rv.addWidget(self.pages, 1)
        root.addWidget(side); root.addWidget(right, 1)
        self._save_timer = QTimer(self); self._save_timer.setSingleShot(True); self._save_timer.timeout.connect(lambda: S.save(self.s))
        for title, build in (("General", self.p_general), ("Minimap", self.p_overlay), ("Map & markers", self.p_map),
                             ("Big map", self.p_big), ("Hotkeys", self.p_hotkeys), ("Updates", self.p_updates),
                             ("Advanced", self.p_advanced), ("About", self.p_about)):
            self.nav.addItem(QListWidgetItem(title))
            self.pages.addWidget(self._page(title, build))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(0)
        if not embedded:
            dark_title_bar(self)

    # ---------- helpers
    @property
    def recording(self):
        return any(e.listening for e in getattr(self, "_hk", {}).values())

    def _page(self, title, build):
        w = QWidget(); w.setObjectName("page")
        v = QVBoxLayout(w); v.setContentsMargins(28, 22, 28, 22); v.setSpacing(6)
        h = QLabel(title); h.setObjectName("h1"); v.addWidget(h)
        build(v)
        v.addStretch(1)
        sc = QScrollArea(); sc.setWidget(w); sc.setWidgetResizable(True)
        return sc

    def set(self, key, val, sub=None):
        if sub:
            self.s[key][sub] = val
        else:
            self.s[key] = val
        self._save_timer.start(250)
        self.changed.emit(key)

    def group(self, v, title):
        g = QGroupBox(title); f = QFormLayout(g); f.setLabelAlignment(Qt.AlignLeft); f.setHorizontalSpacing(18); f.setVerticalSpacing(10)
        v.addWidget(g)
        return f

    def check(self, f, label, key, hint=None):
        c = QCheckBox(label); c.setChecked(bool(self.s[key])); c.toggled.connect(lambda x: self.set(key, x))
        f.addRow(c)
        if hint:
            self.hint(f, hint)
        return c

    def hint(self, f, text):
        l = QLabel(text); l.setObjectName("hint"); l.setWordWrap(True); f.addRow(l)

    def combo(self, f, label, key, options):
        c = QComboBox()
        for val, text in options:
            c.addItem(text, val)
        idx = max(0, [o[0] for o in options].index(self.s[key]) if self.s[key] in [o[0] for o in options] else 0)
        c.setCurrentIndex(idx)
        c.currentIndexChanged.connect(lambda i: self.set(key, c.itemData(i)))
        f.addRow(label, c)
        return c

    def slider(self, f, label, key, lo, hi, scale=1.0, fmt="{:.0f}"):
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        sl = QSlider(Qt.Horizontal); sl.setRange(int(lo / scale), int(hi / scale)); sl.setValue(int(round(self.s[key] / scale)))
        val = QLabel(fmt.format(self.s[key])); val.setMinimumWidth(52)
        def ch(x):
            v = x * scale
            val.setText(fmt.format(v)); self.set(key, round(v, 4) if scale < 1 else int(v))
        sl.valueChanged.connect(ch)
        h.addWidget(sl, 1); h.addWidget(val)
        f.addRow(label, row)
        return sl

    def color(self, f, label, key):
        b = ColorButton(self.s[key]); b.changed.connect(lambda c: self.set(key, c)); f.addRow(label, b)

    # ---------- pages
    def p_general(self, v):
        f = self.group(v, "Startup")
        c = QCheckBox("Start Map Runner when Windows starts")
        c.setChecked(S.get_autostart())
        def auto(x):
            ok = S.set_autostart(x); self.set("start_with_windows", x)
            if x and not ok:
                self.notice.show_msg("Couldn't register with Windows startup. This only works in the installed app.", "warn")
        c.toggled.connect(auto); f.addRow(c)
        self.combo(f, "Show the minimap", "show_mode", [("game", "Only while SnowRunner is running"), ("always", "Always")])
        self.check(f, "Hide while not driving (garage, menus)", "hide_when_not_driving")
        f = self.group(v, "Display")
        self.combo(f, "Units", "units", [("auto", "Same as the game"), ("metric", "Metric (km/h, m)"), ("imperial", "Imperial (mph, ft)")])
        self.check(f, "Show tray notifications", "notifications")
        self.hint(f, "Tip: play SnowRunner in Borderless or Windowed mode. Exclusive fullscreen hides every overlay.")

    def p_overlay(self, v):
        f = self.group(v, "Layout")
        self.combo(f, "Shape", "shape", [("circle", "Round"), ("rounded", "Rounded square"), ("square", "Square")])
        self.slider(f, "Size", "size", 160, 900, 10, "{:.0f} px")
        self.slider(f, "Opacity", "opacity", 0.3, 1.0, 0.01, "{:.0%}")
        self.combo(f, "Position", "position", [("top_left", "Top left"), ("top_right", "Top right"), ("right_middle", "Right edge"),
                                               ("bottom_left", "Bottom left"), ("bottom_right", "Bottom right"), ("custom", "Custom (drag it)")])
        screens = QApplication.screens()
        if len(screens) > 1:
            self.combo(f, "Monitor", "monitor", [(i, f"{i + 1}: {sc.name()} ({sc.size().width()}x{sc.size().height()})") for i, sc in enumerate(screens)])
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Move / resize now"); b.clicked.connect(lambda: self.action.emit("unlock"))
        b2 = QPushButton("Reset position"); b2.clicked.connect(lambda: self.action.emit("reset_position"))
        h.addWidget(b); h.addWidget(b2); h.addStretch(1); f.addRow(row)
        self.hint(f, "In move mode drag the minimap anywhere and use the mouse wheel to resize. Press the move hotkey again to lock it; locked, clicks pass straight through to the game.")
        f = self.group(v, "View")
        self.check(f, "Rotate the map with the truck (off = north up)", "rotate")
        self.slider(f, "Zoom", "zoom", 0.2, 6.0, 0.05, "{:.2f}x")
        self.check(f, "Compass (N)", "show_compass")
        self.check(f, "Scale bar", "show_scale")
        self.check(f, "Region name under the map", "show_info")
        self.check(f, "Speed", "show_speed")
        self.check(f, "Coordinates", "show_coords")
        f = self.group(v, "Colours")
        self.color(f, "Border", "accent")
        self.color(f, "Truck arrow", "arrow_color")
        self.slider(f, "Arrow size", "arrow_size", 0.6, 2.0, 0.05, "{:.2f}x")

    def p_map(self, v):
        f = self.group(v, "Layers")
        self.check(f, "Fog of war (grey out what you haven't uncovered, like the game)", "fog")
        self.check(f, "Map markers", "markers")
        self.slider(f, "Marker size", "marker_size", 0.6, 2.0, 0.05, "{:.2f}x")
        self.combo(f, "Marker names", "labels", [("never", "Never"), ("big", "On the big map"), ("always", "Always")])
        self.combo(f, "Finished tasks, found watchtowers, collected upgrades", "completed",
                   [("dim", "Show faded"), ("hide", "Hide"), ("show", "Show normally")])
        self.hint(f, "Progress is read from your save, so it updates after the game saves. Right-click the big map to add your own pins.")
        g = QGroupBox("Which markers"); grid = QGridLayout(g); v.addWidget(g)
        for i, (k, label) in enumerate(S.MARKER_CATEGORIES):
            c = QCheckBox(label.replace("&", "&&")); c.setChecked(self.s["marker_filter"].get(k, True))
            c.toggled.connect(lambda x, k=k: self.set("marker_filter", x, k))
            grid.addWidget(c, i // 2, i % 2)
        f = self.group(v, "Trail")
        self.check(f, "Draw the path you've driven", "trail")
        self.color(f, "Trail colour", "trail_color")
        self.slider(f, "Trail length", "trail_length", 200, 20000, 100, "{:.0f} pts")
        b = QPushButton("Clear trail"); b.clicked.connect(lambda: self.action.emit("clear_trail")); f.addRow(b)
        f = self.group(v, "Map drawing")
        self.combo(f, "Detail", "map_quality", [(2.0, "Standard (2 px per metre)"), (3.0, "High (3 px per metre, sharp at 1440p)"), (4.0, "Ultra (4 px per metre, more memory)")])
        self.check(f, "Draw maps of regions you've visited in the background", "prerender_visited")
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        b1 = QPushButton("Redraw current map"); b1.clicked.connect(lambda: self.action.emit("redraw"))
        b2 = QPushButton("Delete all drawn maps"); b2.setObjectName("danger"); b2.clicked.connect(self._clear_maps)
        h.addWidget(b1); h.addWidget(b2); h.addStretch(1); f.addRow(row)
        self.hint(f, "Maps are drawn from your SnowRunner files the first time you visit a region (about a minute) and kept in the data folder.")

    def _clear_maps(self):
        self.notice.show_msg("Delete every drawn map? They'll be drawn again when needed (about a minute per region).",
                             "danger", confirm=lambda: self.action.emit("clear_maps"), confirm_text="Delete maps")

    def p_big(self, v):
        f = self.group(v, "Big map")
        self.slider(f, "Size (share of screen height)", "big_size", 0.4, 1.0, 0.01, "{:.0%}")
        self.slider(f, "Opacity", "big_opacity", 0.3, 1.0, 0.01, "{:.0%}")
        self.combo(f, "Opens showing", "big_view", [("follow", "Area around the truck"), ("whole", "Whole region")])
        self.hint(f, "Zoom hotkeys work on the big map too.")

    def p_hotkeys(self, v):
        g = QGroupBox("Hotkeys (work while the game has focus)"); grid = QGridLayout(g); grid.setVerticalSpacing(8); v.addWidget(g)
        self._hk = {}
        for i, (k, label, default) in enumerate(S.ACTIONS):
            grid.addWidget(QLabel(label), i, 0)
            e = HotkeyEdit(self.s["hotkeys"].get(k, default)); e.changed.connect(lambda t, k=k: self._set_hk(k, t))
            e.problem.connect(lambda m: self.notice.show_msg(m, "warn"))
            grid.addWidget(e, i, 1); self._hk[k] = e
        b = QPushButton("Restore default hotkeys"); b.clicked.connect(self._reset_hk); v.addWidget(b, 0, Qt.AlignLeft)
        l = QLabel("Click a box and press the new combination. Esc cancels, Backspace clears."); l.setObjectName("hint"); v.addWidget(l)

    def _set_hk(self, k, t):
        for other, val in self.s["hotkeys"].items():
            if other != k and val and val == t:
                name = next((lab for kk, lab, _ in S.ACTIONS if kk == other), other)
                self.notice.show_msg(f"{t} is already used for \"{name}\". Pick another combination.", "warn")
                self._hk[k].value = self.s["hotkeys"][k]; self._hk[k].setText(self.s["hotkeys"][k] or "Not set"); return
        self.set("hotkeys", t, k)

    def _reset_hk(self):
        for k, _, d in S.ACTIONS:
            self.s["hotkeys"][k] = d; self._hk[k].value = d; self._hk[k].setText(d)
        self.set("hotkeys", self.s["hotkeys"])

    def p_updates(self, v):
        f = self.group(v, "Updates")
        f.addRow("Installed version", QLabel(VERSION))
        self.check(f, "Install updates automatically", "auto_update")
        sp = QSpinBox(); sp.setRange(1, 48); sp.setValue(int(self.s["update_hours"])); sp.setSuffix(" h")
        sp.valueChanged.connect(lambda x: self.set("update_hours", x)); f.addRow("Check every", sp)
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        b = QPushButton("Check now"); b.setObjectName("primary"); b.clicked.connect(lambda: self.action.emit("check_updates"))
        b2 = QPushButton("Release notes"); b2.clicked.connect(lambda: webbrowser.open(f"https://github.com/{REPO}/releases"))
        h.addWidget(b); h.addWidget(b2); h.addStretch(1); f.addRow(row)

    def p_advanced(self, v):
        f = self.group(v, "Game files")
        self._path_row(f, "SnowRunner folder", "game_dir", paths.game_dir() or "not found")
        self._path_row(f, "Save folder", "save_dir", self.s.get("save_dir") or "detected automatically")
        self.hint(f, "Leave empty to detect automatically (Steam libraries, Epic). Restart the app after changing.")
        f = self.group(v, "Troubleshooting")
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        for text, act in (("Open data folder", "open_data"), ("Open log", "open_log"), ("Copy diagnostics", "diagnostics")):
            b = QPushButton(text); b.clicked.connect(lambda _=False, a=act: self.action.emit(a)); h.addWidget(b)
        h.addStretch(1); f.addRow(row)
        b = QPushButton("Reset all settings"); b.setObjectName("danger"); b.clicked.connect(self._reset_all); f.addRow(b)

    def _path_row(self, f, label, key, placeholder):
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0)
        e = QLineEdit(self.s.get(key, "")); e.setPlaceholderText(placeholder)
        e.editingFinished.connect(lambda: self.set(key, e.text().strip()))
        b = QPushButton("Browse…")
        def browse():
            d = pick_folder(self.window(), label, e.text() or os.path.expanduser("~"))
            if d:
                e.setText(d); self.set(key, d)
        b.clicked.connect(browse)
        h.addWidget(e, 1); h.addWidget(b); f.addRow(label, row)

    def _reset_all(self):
        self.notice.show_msg("Reset every setting, including hotkeys, to its default?", "danger",
                             confirm=lambda: self.action.emit("reset"), confirm_text="Reset settings")

    def p_about(self, v):
        ip = os.path.join(paths.APP_DIR, "assets", "icon.png")
        if os.path.exists(ip):
            l = QLabel(); l.setPixmap(QPixmap(ip).scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)); v.addWidget(l)
        t = QLabel(f"<b>Map Runner {VERSION}</b><br>A live minimap overlay for SnowRunner. Maps are drawn on your PC from your own "
                   "game files; the truck position is read from the running game without changing it.<br><br>"
                   f"<a style='color:#ffc400' href='https://github.com/{REPO}'>github.com/{REPO}</a><br><br>"
                   "Mesh parsing adapted from SnowrunnerMapConverter (MIT) and Snowrunner-Model-Importer (MIT). "
                   "Built with Qt for Python (LGPL), NumPy and Pillow.<br><br>"
                   "Not affiliated with Saber Interactive or Focus Entertainment. SnowRunner is a trademark of its owners.")
        t.setWordWrap(True); t.setOpenExternalLinks(True); t.setTextFormat(Qt.RichText); v.addWidget(t)
