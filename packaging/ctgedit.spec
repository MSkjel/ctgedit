# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the ctgedit Windows build (one folder, no console).

    python -m PyInstaller packaging/ctgedit.spec --noconfirm --distpath dist --workpath build/pyinstaller

packaging/build.ps1 runs this, smoke-tests the result and makes the installer.
Output: dist/ctgedit/ctgedit.exe next to _internal/ (Python, Qt and the data
files). It needs nothing installed on the target PC -- no Python, no Qt.

Bundled data: every non-Python file of the ctgedit package (the seed and
catalog JSON of ctgedit/format, the manual under ctgedit/help) and the build
identity ctgedit/_build_info.json (version, commit, date; shown in About).

The Cicode simulator: ctsim.exe + CicodeSim.Engine.dll (+ ctsim.exe.config),
the engine's net48 Release output (external/cicode-sim, or the folder named
by CTG_CTSIM_BUILD), are copied next to ctgedit.exe when they are built. Only
those files: nothing else of external/ ships, and the engine is a separate
.NET program reached over its ctsim protocol, never imported
(ctgedit/sim/client.py finds it there; tests/test_sim_boundary.py).
"""
import os
import re
import shutil
import sys

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct,
    VSVersionInfo)

REPO = os.path.dirname(SPECPATH)          # noqa: F821 -- SPECPATH is set by PyInstaller
# this checkout first: the current folder (another checkout?) precedes
# PYTHONPATH under "python -m PyInstaller"
sys.path[:] = [REPO] + [p for p in sys.path if os.path.normcase(os.path.abspath(p or "."))
                        != os.path.normcase(REPO)]

from ctgedit import __version__                     # noqa: E402
from ctgedit.buildinfo import BUILD_INFO_NAME, write_build_info  # noqa: E402

GENERATED = os.path.join(workpath, "generated")     # noqa: F821 -- set by PyInstaller
os.makedirs(GENERATED, exist_ok=True)
build_info = write_build_info(os.path.join(GENERATED, BUILD_INFO_NAME), cwd=REPO)
print("ctgedit %s, commit %s" % (__version__, build_info["commit"]))

# the programmatic app icon as a real .ico (the exe, its shortcuts, the installer)
from ctgedit.ui.style.theme import write_app_ico          # noqa: E402
ICON = write_app_ico(os.path.join(GENERATED, "ctgedit.ico"))

datas = [(p, d) for p, d in collect_data_files("ctgedit")
         if "__pycache__" not in p and not p.endswith(BUILD_INFO_NAME)]
datas.append((os.path.join(GENERATED, BUILD_INFO_NAME), "ctgedit"))

_nums = [int(x) for x in re.findall(r"\d+", __version__)[:3]]
_nums = tuple((_nums + [0, 0, 0])[:3]) + (0,)
VERSION = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_nums, prodvers=_nums, mask=0x3F, flags=0x0, OS=0x40004,
                      fileType=0x1, subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "ctgedit"),
            StringStruct("FileDescription", "ctgedit - graphics page editor for AVEVA Plant SCADA"),
            StringStruct("FileVersion", __version__),
            StringStruct("InternalName", "ctgedit"),
            StringStruct("OriginalFilename", "ctgedit.exe"),
            StringStruct("ProductName", "ctgedit"),
            StringStruct("ProductVersion", __version__),
            StringStruct("Comments", "commit %s" % build_info["commit"]),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ])

a = Analysis(                                        # noqa: F821
    [os.path.join(SPECPATH, "launcher.py")],         # noqa: F821
    pathex=[REPO],
    binaries=[],
    datas=datas,
    # imported inside functions only (the scanner finds most; these make sure)
    hiddenimports=["ctgedit.selftest", "win32process", "win32gui", "win32con",
                   "win32com.client", "pythoncom", "pywintypes",
                   "ctgedit.infra.autosave", "ctgedit.infra.logsetup",
                   "ctgedit.infra.backups", "ctgedit.format.journal",
                   "ctgedit.format.integrity", "ctgedit.ui.recovery",
                   # the display check (--display-digest runs in its own process)
                   "ctgedit.diag", "ctgedit.diag.render_digest",
                   "ctgedit.diag.display_check", "ctgedit.diag.open_report",
                   "ctgedit.ui.panels.diag_ui",
                   # Find Duplicate Pictures' worker; SVG import (QSvgRenderer)
                   "ctgedit.workers.picture_worker", "PySide6.QtSvg",
                   "ctgedit.workers.find_worker",
                   # Replace Genie's worker (every page of some projects), and what the
                   # page-changing workers share
                   "ctgedit.workers.genieswap_worker", "ctgedit.workers.batchjob"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # development packages that must never ship ("external": the Cicode engine checkout)
    excludes=["tkinter", "PIL", "pytest", "_pytest", "pywinauto", "comtypes",
              "IPython", "matplotlib", "tools", "external"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)                                    # noqa: F821

exe = EXE(                                           # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ctgedit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
    version=VERSION,
)
coll = COLLECT(                                      # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ctgedit",
)

# -- the Cicode simulator next to ctgedit.exe (only its three files) --------------------
ENGINE_BUILD = os.environ.get("CTG_CTSIM_BUILD") or os.path.join(
    REPO, "external", "cicode-sim", "engine", "src", "ctsim", "bin", "Release", "net48")
ENGINE_FILES = ("ctsim.exe", "CicodeSim.Engine.dll", "ctsim.exe.config")
APP_DIR = os.path.join(DISTPATH, "ctgedit")          # noqa: F821 -- set by PyInstaller
if all(os.path.isfile(os.path.join(ENGINE_BUILD, f)) for f in ENGINE_FILES[:2]):
    for f in ENGINE_FILES:
        src = os.path.join(ENGINE_BUILD, f)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(APP_DIR, f))
    print("Cicode simulator: %s from %s" % (", ".join(
        f for f in ENGINE_FILES if os.path.isfile(os.path.join(APP_DIR, f))), ENGINE_BUILD))
elif os.environ.get("CTG_BUILD_WITHOUT_SIM") == "1":
    print("Cicode simulator: left out (CTG_BUILD_WITHOUT_SIM=1)")
else:
    raise SystemExit("The Cicode simulator is not built (%s): packaging\\build.ps1 builds "
                     "it first, or set CTG_BUILD_WITHOUT_SIM=1 to build without it."
                     % ENGINE_BUILD)
