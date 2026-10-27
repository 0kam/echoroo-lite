"""Explicit online model setup; analysis itself uses the local cache."""
import argparse
import hashlib
import os
import shutil
import urllib.request
import urllib.parse
from pathlib import Path

KINGLET_BASE_URL = 'https://github.com/0kam/kinglet/releases/download/v0.1.0/'
KINGLET_ASSETS = (
    ('kinglet-b-v0.1.0.onnx', 'c248cad949e5b3e50cb84ee22c8deb4c8e8615cb2097c3cf3d155184af92b2e9'),
    ('kinglet-s-v0.1.0.onnx', '9d6556f6b8679b65dfb4f0e50f80c461e645b7fe58f6409304398de6a853ff96'),
    ('kinglet-xs-v0.1.0.onnx', 'a59e9887dcda13d5c226186dd459fb88a71cc6767151f802848ae6725b088ece'),
    ('kinglet-b-v0.1.0.classes.csv', '08d38f9693b5012ed6ed782e6c00d827c7ca1e62087d401daf488dc434b04e3d'),
    ('kinglet-s-v0.1.0.classes.csv', '5d340861ba77549b8293ebea14f51ac80ebd32cbf8a06963f6b2eae270d126bd'),
    ('kinglet-xs-v0.1.0.classes.csv', 'd259e144144b66a2382aeea96e3877b78c6b32bbca9a8f4921b758c25c7d0b70'),
)

V3_ASSETS = (
    ('BirdNET+_V3.0-preview3.1_Global_11K_FP16_pruned.onnx',
     '69cfc8db3ebec163feb6329e546eb56e1aadac2a309f1ee99aecfabd1aa9bd24'),
    ('BirdNET+_V3.0-preview3.1_Global_11K_Labels.csv',
     '8124b0ea2d187104c5e2cd95a0f937165647e20349c8fd34d4d5ef991821f8f0'),
    ('TERMS_OF_USE.txt', '91b474bddbfdbd7cb2f5d85fd4511eee8af151d899c8450cd0b22000c425bd8f'),
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "poc/models")
parser.add_argument("--perch-only", action="store_true")
parser.add_argument('--kinglet-from', type=Path, help='Copy verified Kinglet assets from a local handover folder')
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
if not args.perch_only:
    assets.extend(('birdnet-v3/' + name, 'https://zenodo.org/api/records/20703646/files/'
                   + urllib.parse.quote(name, safe='') + '/content', sha) for name, sha in V3_ASSETS)
    assets.extend(('kinglet/' + name, KINGLET_BASE_URL + name, sha) for name, sha in KINGLET_ASSETS)


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
    try:
        if relative.startswith('kinglet/') and args.kinglet_from:
            source = args.kinglet_from / path.name
            if digest(source) != expected:
                raise RuntimeError(f'Model checksum mismatch: {source}')
            shutil.copyfile(source, temporary)
        else:
            with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
        if digest(temporary) != expected:
            raise RuntimeError(f"Model checksum mismatch: {relative}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Prepared and verified {relative}")

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
