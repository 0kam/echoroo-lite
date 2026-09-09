"""Freeze on the target OS before running npm run desktop:build."""
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
target = subprocess.check_output(["rustc", "--print", "host-tuple"], text=True).strip()
name = f"echoroo-sidecar-{target}"
command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
           "--name", name, "--distpath", str(ROOT / "apps/desktop/binaries"),
           "--workpath", str(ROOT / "build/sidecar"), "--specpath", str(ROOT / "build")]
for package in ("birdnet", "ai_edge_litert", "onnxruntime", "sqlite_vec"):
    command += ["--collect-all", package]
command += ["--collect-data", "tzdata", "--add-data", f"{ROOT / 'apps/sidecar/data'}:data",
            str(ROOT / "apps/sidecar/server.py")]
subprocess.run(command, check=True, cwd=ROOT)
print(ROOT / "apps/desktop/binaries" / (name + (".exe" if platform.system() == "Windows" else "")))
