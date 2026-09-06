# PyInstaller spec for the portable Windows build.
#
# Produces a folder build (dist/PhotoTimestampEditor/) rather than a single
# file: it starts in about a second instead of unpacking Qt to a temp folder on
# every launch, and antivirus heuristics are far kinder to it.
#
# Build it with build_windows.bat, or by hand:
#     pyinstaller packaging/PhotoTimestampEditor.spec --noconfirm --clean

from pathlib import Path

PROJECT = Path(SPECPATH).parent
RESOURCES = PROJECT / "photo_timestamp_editor" / "resources"

# Qt ships far more than a widgets app needs. Dropping these Python modules
# stops PyInstaller pulling in their (very large) native libraries.
EXCLUDED_MODULES = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # Python bits nothing here uses.
    "tkinter",
    "unittest",
    "doctest",
    "pydoc_data",
    "lib2to3",
    "setuptools",
    "pip",
    "pytest",
    "numpy",
    "PIL",
    "matplotlib",
]

# Native libraries to drop even if something pulled them in transitively.
# Deliberately conservative: opengl32sw.dll stays, because it is the software
# OpenGL fallback that keeps Qt rendering on machines with poor GPU drivers.
UNWANTED_BINARIES = (
    "qt6webengine",
    "qt6quick",
    "qt6qml",
    "qt6pdf",
    "qt63d",
    "qt6multimedia",
    "qt6charts",
    "qt6datavisualization",
    "qt6designer",
    "qt6sql",
    "qt6test",
    "qt6bluetooth",
    "qt6nfc",
    "qt6positioning",
    "qt6sensors",
    "qt6serialport",
    "qt6texttospeech",
    "qt6spatialaudio",
    "qt6webchannel",
    "qt6websockets",
    "qt6scxml",
    "qt6remoteobjects",
)


def wanted(entry):
    """True unless this bundled binary matches the unwanted list."""
    name = Path(entry[0]).name.lower()
    return not any(token in name for token in UNWANTED_BINARIES)


analysis = Analysis(
    [str(PROJECT / "run_app.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[(str(RESOURCES), "photo_timestamp_editor/resources")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    noarchive=False,
)

analysis.binaries = [entry for entry in analysis.binaries if wanted(entry)]
analysis.datas = [entry for entry in analysis.datas if wanted(entry)]

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PhotoTimestampEditor",
    debug=False,
    strip=False,
    upx=False,
    # windowed: no console window flashes up behind the GUI.
    console=False,
    icon=str(RESOURCES / "app.ico"),
    version=str(PROJECT / "packaging" / "version_info.txt"),
)

COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="PhotoTimestampEditor",
)
