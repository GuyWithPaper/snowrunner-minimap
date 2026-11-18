# PyInstaller spec: one-folder windowed build of the minimap
import os
block_cipher = None
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
a = Analysis([os.path.join(ROOT, "minimap", "app.py")],
             pathex=[os.path.join(ROOT, "minimap")],
             datas=[(os.path.join(ROOT, "assets", "icon.ico"), "assets"), (os.path.join(ROOT, "assets", "icon.png"), "assets"), (os.path.join(ROOT, "assets", "check.png"), "assets")],
             hiddenimports=["PIL.DdsImagePlugin", "PIL.JpegImagePlugin", "PIL.PngImagePlugin"],
             excludes=["tkinter", "matplotlib", "scipy", "pandas", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
                       "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf",
                       "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtNetwork", "PySide6.QtSql",
                       "PySide6.QtOpenGL", "PySide6.QtSvg", "PySide6.QtBluetooth", "PySide6.QtPositioning"],
             cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MapRunner", console=False,
          icon=os.path.join(ROOT, "assets", "icon.ico"), version=os.path.join(SPECPATH, "file_version.txt"))
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="MapRunner")
