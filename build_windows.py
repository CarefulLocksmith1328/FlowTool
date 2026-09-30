"""Build the self-contained Windows app and its install ZIP."""
from pathlib import Path
import sys
import zipfile

import PyInstaller.__main__


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "downloads" / "FlowTool-Windows.zip"
DIST = ROOT / "dist"


def main():
    if sys.platform != "win32":
        raise SystemExit("Der Windows-Build muss auf Windows ausgeführt werden")
    PyInstaller.__main__.run([
        "--noconfirm", "--clean", "--windowed", "--onedir",
        "--name", "FlowTool", "--add-data", f"{ROOT / 'web'}:web",
        "--distpath", str(DIST), "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"), str(ROOT / "desktop.py"),
    ])
    program = DIST / "FlowTool" / "FlowTool.exe"
    if not program.is_file():
        raise SystemExit("FlowTool.exe wurde nicht erzeugt")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in (DIST / "FlowTool").rglob("*"):
            if file.is_file():
                archive.write(file, Path("FlowTool") / file.relative_to(DIST / "FlowTool"))
        archive.write(ROOT / "installer" / "Install-FlowTool.cmd", "Install-FlowTool.cmd")
        archive.write(ROOT / "installer" / "INSTALL.txt", "INSTALL.txt")
    print(f"Fertig: {OUTPUT} ({OUTPUT.stat().st_size:,} Bytes)")


if __name__ == "__main__":
    main()
