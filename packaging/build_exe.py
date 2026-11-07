"""Build ``dist/guardrun-<version>-windows-x64.exe`` and its SHA-256 file.

Run from the repository root after ``pip install . pyinstaller``.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import guardrun

ROOT = Path(__file__).resolve().parent.parent


def build(name: str) -> Path:
    subprocess.run(  # noqa: S603 - fixed argv built from constants, no shell
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--console",
            "--clean",
            "--noconfirm",
            "--name",
            name,
            "--collect-submodules",
            "guardrun",
            "--specpath",
            str(ROOT / "build"),
            "--workpath",
            str(ROOT / "build" / "pyinstaller"),
            "--distpath",
            str(ROOT / "dist"),
            str(ROOT / "packaging" / "guardrun_exe.py"),
        ],
        check=True,
    )
    return ROOT / "dist" / f"{name}.exe"


def write_checksum(exe: Path) -> Path:
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    checksum = exe.with_name(exe.name + ".sha256")
    checksum.write_text(f"{digest}  {exe.name}\n", encoding="utf-8", newline="\n")
    return checksum


def main() -> int:
    exe = build(f"guardrun-{guardrun.__version__}-windows-x64")
    write_checksum(exe)
    print(exe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
