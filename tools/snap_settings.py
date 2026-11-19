import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "minimap"))
from PySide6.QtWidgets import QApplication
qa = QApplication(sys.argv); qa.setStyle("Fusion")
import app as A
out = sys.argv[1]
w = A.Overlay(); w.s["first_run_done"] = True
t0 = time.time()
while time.time() - t0 < 240 and (w.level is None or w.level not in w.store.color):
    qa.processEvents(); w.tick(); time.sleep(0.05)
w.show()
print("pre: visible", w.isVisible(), "big", w.big, "busy", w._busy())
def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        qa.processEvents(); time.sleep(0.01)
pump(0.5); print("pre2 visible", w.isVisible()); w.open_settings(); pump(1.0)
print("anims", [a.state() for a in w._anims])
print("after open:", "big", w.big, "view", w.view, "opacity", round(w.windowOpacity(), 2), "settings visible", w.settings_win.isVisible())
w.grab().save(out + "_s1_settings.png")
w._toggle_view("map"); pump(0.11); w.grab().save(out + "_s2_mid.png"); pump(0.5)
print("after map tab:", w.view, "dim", round(w._dim, 2), "settings visible", w.settings_win.isVisible())
w.grab().save(out + "_s3_map.png")
w.open_settings(); pump(0.5); print("back to settings:", w.view)
w.close_settings(); pump(0.6); print("close from big:", "big", w.big, w.view)
w.k_big(); pump(0.7); print("close big:", "big", w.big)
w.open_settings(); pump(0.7); w.close_settings(); pump(0.7); print("open+close from mini:", "big", w.big, w.view)
w.hide()
