"""Render the overlay (minimap, big map) and every settings page offscreen to PNGs, using the live game if running."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "minimap"))
from PySide6.QtWidgets import QApplication
qa = QApplication(sys.argv)
qa.setStyle("Fusion")
import app as A
from settingswin import SettingsWindow

out = sys.argv[1]
w = A.Overlay()
w.s["first_run_done"] = True
t0 = time.time()
while time.time() - t0 < 240 and (w.level is None or w.level not in w.store.color):
    qa.processEvents(); w.tick(); time.sleep(0.05)
w.hide()
w.pins.setdefault(w.level, []).append({"x": 700, "z": -300, "label": "Test pin", "color": "#ff5ad0"})
w.waypoint[w.level] = (600, -200)
for shape in ("circle", "rounded"):
    w.s["shape"] = shape; w.apply_geometry(); w.grab().save(f"{out}_mini_{shape}.png")
w.s["shape"] = "circle"
w.big = True; w.big_zoom = 0.6; w.apply_geometry(); w.grab().save(out + "_big.png")
w.big_zoom = None; w._layout_search(); w.grab().save(out + "_bigall.png")
w.search.setText("water"); qa.processEvents(); w._layout_search(); w.grab().save(out + "_search.png")
w._pick(w.results.item(0)); qa.processEvents(); w.grab().save(out + "_picked.png")
print("results", w.results.count(), "progress:", w.progress_summary(w.level))
w.pins[w.level].pop()
sw = SettingsWindow(w.s)
for i in range(sw.nav.count()):
    sw.nav.setCurrentRow(i); qa.processEvents()
    sw.grab().save(f"{out}_set{i}.png")
print("level", w.level, "state", w.state and (round(w.state.x), round(w.state.z)), "done", len(w.completed()))
