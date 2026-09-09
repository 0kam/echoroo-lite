"""Explicit online model setup; analysis itself uses the local cache."""
import argparse
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "poc/models")
args = parser.parse_args()
models = args.output.resolve()
assets = [
    ("perch/perch_v2_no_dft.onnx",
     "https://huggingface.co/justinchuby/Perch-onnx/resolve/da88bf8c37c0b3068fc706fe509f633b9fbd28c1/perch_v2_no_dft.onnx",
     "4dcf71c18a147198545944bb5149697e89e3ad2e16637fa8f0edf6d13035a017"),
    ("perch-labels/assets/labels.csv",
     "https://huggingface.co/cgeorgiaw/Perch/resolve/c3c3c816976a6c1bb75140b8fbaef7397a6f708f/assets/labels.csv",
     "e4d5c0397d8fb08bf90c6b13a34810af53504faad927e472fcc567793c9de057"),
]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


for relative, url, expected in assets:
    path = models / relative
    if path.is_file() and digest(path) == expected:
        print(f"Verified {relative}")
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target)
    if digest(temporary) != expected:
        raise RuntimeError(f"Model checksum mismatch: {relative}")
    temporary.replace(path)
    print(f"Downloaded and verified {relative}")

os.environ["BIRDNET_APP_DATA"] = str(models / "birdnet")
import birdnet
for kind in ("acoustic", "geo"):
    birdnet.load(kind, "2.4", "tf", library="litert")
print(f"Models ready: {models}")
