"""Render the overlay offscreen (minimap + big map) to PNGs using the live truck position."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "minimap"))
from PySide6.QtWidgets import QApplication
import app as A
qa = QApplication(sys.argv)
w = A.Overlay()
w.hide()
t0 = time.time()
while time.time() - t0 < 25 and (w.level is None or w.level not in w.store.color):
    qa.processEvents(); w.tick(); time.sleep(0.05)
w.s["fog"] = True
out = sys.argv[1]
w.grab().save(out + "_mini.png")
w.big = True; w.apply_geometry(); w.s["big_zoom"] = 0.55
w.grab().save(out + "_big.png")
w.s.pop("big_zoom")
w.grab().save(out + "_bigall.png")
print("level", w.level, w.state and (w.state.x, w.state.z))
