# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정. 저장소 맨 위에서 실행:  pyinstaller packaging/auto-edit.spec --noconfirm
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "autoedit", "web"), os.path.join("autoedit", "web"))]
binaries = []
hidden = collect_submodules("uvicorn") + ["autoedit.server", "autoedit.selftest", "anthropic"]

for pkg in ("sherpa_onnx", "python_multipart", "multipart"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

# 함께 묶을 FFmpeg (bin/ 폴더에 ffmpeg, ffprobe와 필요한 DLL을 넣어 둔다)
bin_dir = os.path.join(ROOT, "bin")
if os.path.isdir(bin_dir):
    for name in os.listdir(bin_dir):
        datas.append((os.path.join(bin_dir, name), "bin"))

a = Analysis([os.path.join(ROOT, "run.py")], pathex=[ROOT], binaries=binaries, datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "matplotlib", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
is_mac = sys.platform == "darwin"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="auto-edit", console=not is_mac, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="auto-edit", upx=False)
if is_mac:
    app = BUNDLE(coll, name="auto-edit.app", bundle_identifier="studio.ctrls.autoedit",
                 info_plist={"CFBundleShortVersionString": os.environ.get("APP_VERSION", "0.1.0"),
                             "NSHighResolutionCapable": True})
