"""Explicit online model setup; analysis itself uses the local cache."""
import argparse
import hashlib
import os
import shutil
import urllib.request
import urllib.parse
from pathlib import Path

KINGLET_BASE_URL = 'https://github.com/0kam/kinglet/releases/download/v0.4.0/'
KINGLET_ASSETS = (
    ('kinglet-b-v0.4.0.onnx', '47a80ad1800bf77b343612e07c86d92e50d6551e5211d620515f06ec8cdbfdf3'),
    ('kinglet-s-v0.4.0.onnx', 'a0baab1201014876e40bde6c9e64d28b880ffa215f0125842ea6066db96a1238'),
    ('kinglet-xs-v0.4.0.onnx', '343447f39a8f79737a8426e8c48a482f5cfb3c5f60b44f9509638a4e718773b3'),
)
# A release head pack also carries calibration features that the app does not use (two thirds of its size), so
# only its head is kept: (tier, sha256 of the release head pack, sha256 of the kept W, b and labels).
KINGLET_HEADS = (
    ('b', 'e2f4ceeca9fda2e203f5a4bc9a94aaa34b13863b9e4f4d1f3968faac9e904b74', '71955322ee39ba5001b313e148f352bdab6811493445f9ba3423ee848bb2a3aa'),
    ('s', 'd1b7017c5ee8517293b9c47793a6e9f2c775d8af177aed61fb161467f5361d0c', '50a95373df06f56f9f4db22ae1c3f391a8545e17c97c5d8013d5ffc575e2edf9'),
    ('xs', '52cc4ba828345f35b320494830ab53e938d26a7a253879aaf2a507d4d8c1ae2a', 'b3cc49171d18464b481f4aed94c88560cb8383f828d34308f822aa2eff520dcf'),
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


def fetch(relative, url, expected, temporary):
    """Download (or copy from the Kinglet handover folder) to `temporary` and check it."""
    if relative.startswith('kinglet/') and args.kinglet_from:
        source = args.kinglet_from / Path(relative).name
        if digest(source) != expected:
            raise RuntimeError(f'Model checksum mismatch: {source}')
        shutil.copyfile(source, temporary)
    else:
        with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
    if digest(temporary) != expected:
        raise RuntimeError(f"Model checksum mismatch: {relative}")


def head_digest(path):
    """One checksum of a kept Kinglet head, whatever wrote the .npz around it."""
    import numpy as np
    try:
        with np.load(path, allow_pickle=False) as head:
            parts = (head['W'].tobytes(), head['b'].tobytes(), '\n'.join(map(str, head['labels'])).encode())
    except (OSError, KeyError, ValueError):
        return None
    return hashlib.sha256(b''.join(parts)).hexdigest()


for relative, url, expected in assets:
    path = models / relative
    if path.is_file() and digest(path) == expected:
        print(f"Verified {relative}")
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        fetch(relative, url, expected, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Prepared and verified {relative}")

for tier, pack_sha, head_sha in () if args.perch_only else KINGLET_HEADS:
    import numpy as np
    relative = f'kinglet/kinglet-{tier}-v0.4.0.head.npz'
    path = models / relative
    if path.is_file() and head_digest(path) == head_sha:
        print(f"Verified {relative}")
        continue
    pack = path.with_name(f'kinglet-{tier}-v0.4.0.headpack.npz')
    temporary = path.with_suffix('.download.npz')
    try:
        fetch('kinglet/' + pack.name, KINGLET_BASE_URL + pack.name, pack_sha, pack)
        with np.load(pack, allow_pickle=False) as source:
            np.savez(temporary, W=source['W'], b=source['b'], labels=source['labels'])
        if head_digest(temporary) != head_sha:
            raise RuntimeError(f"Model checksum mismatch: {relative}")
        temporary.replace(path)
    finally:
        pack.unlink(missing_ok=True)
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
