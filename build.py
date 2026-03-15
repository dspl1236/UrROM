"""
Build UrROM-vX.Y.Z.exe with PyInstaller.
Usage:  python build.py
"""
import subprocess, sys, shutil, os, re
from pathlib import Path

ROOT    = Path(__file__).parent
DIST    = ROOT / "dist"
BUILD   = ROOT / "build"

version_text = (ROOT / "urrom" / "version.py").read_text()
m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', version_text)
VERSION  = m.group(1) if m else "0.0.0"
EXE_NAME = f"UrROM-v{VERSION}"


def main():
    for d in [DIST, BUILD]:
        if d.exists():
            shutil.rmtree(d)

    roms_dir = ROOT / "roms"
    roms_dir.mkdir(exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", EXE_NAME,
        "--add-data", f"{ROOT / 'urrom'}{os.pathsep}urrom",
        "--add-data", f"{ROOT / 'roms'}{os.pathsep}roms",
        str(ROOT / "app" / "main.py"),
    ]

    subprocess.run(cmd, check=True)

    exe_suffix = ".exe" if sys.platform == "win32" else ""
    exe = DIST / f"{EXE_NAME}{exe_suffix}"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print(f"\n✓ Built: {exe}  ({size_mb:.1f} MB)")
    else:
        print(f"\n✗ Expected output not found: {exe}")
        sys.exit(1)


if __name__ == "__main__":
    main()
