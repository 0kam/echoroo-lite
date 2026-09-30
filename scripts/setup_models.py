"""Explicit online model setup; analysis itself uses the local cache."""
import argparse
import hashlib
import os
import shutil
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "poc/models")
parser.add_argument("--perch-only", action="store_true")
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

if args.perch_only:
    print(f"Perch models ready: {models}")
    raise SystemExit

os.environ["BIRDNET_APP_DATA"] = str(models / "birdnet")
import birdnet
from birdnet.acoustic.models.v2_4.tf import models as acoustic_downloads
from birdnet.geo.models.v2_4.tf import GeoTFDownloaderV2_4

# ponytail: adapt pinned birdnet 1.1.1 metadata; remove when upstream uses these API URLs.
for info in (acoustic_downloads['fp32'], GeoTFDownloaderV2_4._model_info):
    info.dl_url = info.dl_url.replace('/records/', '/api/records/') + '/content'

for kind, expected in (
    ('acoustic', '55f3e4055b1a13bfa9a2452731d0d34f6a02d6b775a334362665892794165e4c'),
    ('geo', '33aea6d21cc887d2414e9596d2531a480a3e5f4770c22aa257f217fb757d4653'),
):
    birdnet.load(kind, "2.4", "tf", library="litert")
    root = models / 'birdnet' / f'{kind}-models/v2.4/tf'
    for relative, checksum in (
        ('model-fp32.tflite', expected),
        ('labels/en_us.txt', 'b50b77b7c3dfe40cd637e8cccdca0173a0a4ddee8867b830ff3c1a566f477f16'),
        ('labels/ja.txt', '158c5360676d7fc154b95ed7f161cf845be9ca219a67960901463ddd870b6362'),
    ):
        if digest(root / relative) != checksum:
            raise RuntimeError(f'BirdNET checksum mismatch: {kind}/{relative}')
    print(f'Verified BirdNET 2.4 {kind} and English/Japanese labels')
print(f"Models ready: {models}")
