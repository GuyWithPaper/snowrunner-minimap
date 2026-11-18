"""Dev helper: screenshot the screen and send scan-code key presses to the focused game window."""
import ctypes, sys, time
from ctypes import wintypes as wt
from PIL import ImageGrab
u32 = ctypes.WinDLL("user32", use_last_error=True)
ctypes.windll.shcore.SetProcessDpiAwareness(2)

class KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class MI(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
class U(ctypes.Union):
    _fields_ = [("ki", KI), ("mi", MI), ("pad", ctypes.c_byte * 32)]
class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", U)]

SC = {"esc": 1, "enter": 0x1C, "space": 0x39, "w": 0x11, "a": 0x1E, "s": 0x1F, "d": 0x20, "m": 0x32, "tab": 0x0F,
      "up": 0xC8, "down": 0xD0, "left": 0xCB, "right": 0xCD, "f": 0x21, "e": 0x12, "q": 0x10, "shift": 0x2A}

def key(name, down):
    sc = SC[name]; ext = sc > 0x7F
    flags = 0x8 | (0x2 if not down else 0) | (0x1 if ext else 0)
    i = INPUT(type=1); i.u.ki = KI(0, sc & 0x7F, flags, 0, 0)
    u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))

def tap(name, hold=0.08):
    key(name, True); time.sleep(hold); key(name, False)

def click(x, y):
    u32.SetCursorPos(x, y); time.sleep(0.05)
    for f in (2, 4):
        i = INPUT(type=0); i.u.mi = MI(0, 0, 0, f, 0, 0); u32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)); time.sleep(0.05)

def focus(title="SnowRunner"):
    h = u32.FindWindowW(None, title)
    if h:
        u32.ShowWindow(h, 9); u32.SetForegroundWindow(h)
    return h

def shot(path, scale=0.5):
    im = ImageGrab.grab(all_screens=False)
    if scale != 1: im = im.resize((int(im.width * scale), int(im.height * scale)))
    im.save(path); print(path, im.size)

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "shot": shot(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.5)
    elif cmd == "tap": focus(); time.sleep(0.3); [tap(k) or time.sleep(0.4) for k in sys.argv[2:]]
    elif cmd == "hold": focus(); time.sleep(0.3); key(sys.argv[2], True); time.sleep(float(sys.argv[3])); key(sys.argv[2], False)
    elif cmd == "click": focus(); time.sleep(0.3); click(int(sys.argv[2]), int(sys.argv[3]))
