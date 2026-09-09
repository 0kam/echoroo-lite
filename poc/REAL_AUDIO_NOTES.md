# M4 Pro real-audio PoC — 2026-09-08

This records a local runtime experiment, not a completed desktop application or
a classification-quality evaluation. The source recordings are read in place.

## Input and environment

- User-selected input: `/Users/okamoto/Music/whombat_data/test`.
- 24 WAVs, **30 minutes each**, 12 hours total; 2,073,628,032 bytes.
  The initial description said five minutes; actual headers show 1,800 seconds.
- All files: PCM signed 16-bit, mono, 24 kHz, 43,200,000 samples per file.
- Names: `2MA04037_20240808_000000.wav` through `..._230000.wav` at hourly starts.
- User-confirmed timezone: `Asia/Tokyo`; location: 35°29′29.5″N, 138°12′17.7″E
  (35.49152777777778, 138.20491666666666).
- Apple M4 Pro, 24 GiB RAM; Python 3.11.14. Exact installed packages are in
  [requirements-mac.txt](requirements-mac.txt).
- The annual target is a different protocol: 1 minute every 30 minutes,
  17,520 files / 292 audio hours per site-year, excluding missing recordings.

## Model sources and experimental settings

- Perch V2 ONNX: [justinchuby/Perch-onnx](https://huggingface.co/justinchuby/Perch-onnx),
  revision `da88bf8c37c0b3068fc706fe509f633b9fbd28c1`, `perch_v2_no_dft.onnx`.
  SHA256: `4dcf71c18a147198545944bb5149697e89e3ad2e16637fa8f0edf6d13035a017`.
- Labels: the export's linked base model [cgeorgiaw/Perch](https://huggingface.co/cgeorgiaw/Perch),
  revision `c3c3c816976a6c1bb75140b8fbaef7397a6f708f`, `assets/labels.csv`.
  SHA256: `e4d5c0397d8fb08bf90c6b13a34810af53504faad927e472fcc567793c9de057`.
  Header `inat2024_fsd50k`, 14,795 labels; includes non-bird classes.
- Perch requests both `embedding` and `label`, CPUExecutionProvider, 8 threads,
  batch 1, no window overlap. SoundFile decodes float32; SciPy `resample_poly`
  resamples the whole file from 24 to 32 kHz before slicing 160,000-sample windows.
  Only an incomplete final window is zero-padded. No partial window occurs here.
- BirdNET **V2.4 FP32**, loaded by `birdnet==1.1.1` with `library="litert"`.
  The acoustic model comes from the package's pinned Zenodo record 15050749.
  Model hashes are recorded in the full-run summary. No TensorFlow installation.
- Current [birdnet documentation](https://pypi.org/project/birdnet/) documents this
  LiteRT path; [ai-edge-litert](https://pypi.org/project/ai-edge-litert/) provides
  macOS ARM64 and Windows x86-64 wheels. Mac execution does not verify Windows freeze.
- BirdNET uses 4 workers, batch 1, 3-second windows, no overlap, threshold 0.1,
  top 5 per window, and **year-round** geo filtering at threshold 0.03 (313 labels).
  Seasonal/date-specific geo behavior is not tested in this experiment.
- Perch has no geographic filter in this PoC. Top-five raw logits are saved;
  no sigmoid/softmax or cross-model score averaging is applied. These logits
  and BirdNET sigmoid scores must not be compared as calibrated probabilities.
- This top-five diagnostic export is not sufficient for arbitrary later threshold
  evaluation. Output retention for the application remains a requirements decision.

## Reproduce

Run from the repository root. Model downloads are cached locally, outside Git.

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r poc/requirements-mac.txt
.venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
hf_hub_download('justinchuby/Perch-onnx', 'perch_v2_no_dft.onnx',
    revision='da88bf8c37c0b3068fc706fe509f633b9fbd28c1', local_dir='poc/models/perch')
hf_hub_download('cgeorgiaw/Perch', 'assets/labels.csv',
    revision='c3c3c816976a6c1bb75140b8fbaef7397a6f708f', local_dir='poc/models/perch-labels')
PY
BIRDNET_APP_DATA="$PWD/poc/models/birdnet" .venv/bin/python - <<'PY'
import birdnet
for kind in ['acoustic', 'geo']:
    birdnet.load(kind, '2.4', 'tf', library='litert')
PY
.venv/bin/python -m unittest discover -s poc -p 'test_*.py'
.venv/bin/python poc/real_audio_bench.py /Users/okamoto/Music/whombat_data/test \
  --engine perch --timezone Asia/Tokyo --output poc/results/test-perch-day
.venv/bin/python poc/real_audio_bench.py /Users/okamoto/Music/whombat_data/test \
  --engine birdnet --timezone Asia/Tokyo \
  --latitude 35.49152777777778 --longitude 138.20491666666666 \
  --output poc/results/test-birdnet-day
```

Output directories must be new. Use another name for a repeat; previous runs are
not overwritten. To process one file, pass that file instead of the directory.
This PoC intentionally accepts only the observed mono WAV / recorder-name format.
Its resource measurement uses the Unix `resource` module; it is not a Windows runner.

## Verification and limits

- Inventory checks audio header duration and explicit timezone parsing; invalid dates
  fail rather than falling back to file modification time.
- Perch checks output shapes, finite values, nonzero vectors, matching DB row counts,
  and a cosine self-match through sqlite-vec. Segments and vectors are stored in SQLite.
- BirdNET checks all files/durations were processed, no inputs failed, finite scores,
  and output labels are within the geo species list.
- `summary.json` records total engine-call wall time, per-stage times, versions and
  main-process peak RSS. Initial top-level imports and header inventory are outside
  that timer; model download time is excluded. Imports inside the engine, model loading,
  inference, output writes, and final model fingerprinting are inside it.
- BirdNET's main-process RSS excludes workers. Its benchmark stats separately record
  process-tree memory; do not compare main-process RSS as if it were total memory.
- The generated nearest-neighbor example uses the first window solely as a retrieval
  check. It does not establish bird-call similarity quality or recall.
- TensorFlow/ONNX parity on these recordings, independent species annotation,
  210,240-vector search latency, Windows packaging, and long-running resume are untested.

## Measurements

Both full-corpus runs completed successfully, sequentially, with all 24 recordings
accounted for. Raw local outputs are under `poc/results/` (Git-ignored).

| Engine | 12-hour corpus wall time | Output |
|---|---:|---|
| Perch classification + embedding + SQLite storage/search check | **186.96 s** | 8,640 vectors, top-five logits per window |
| BirdNET classification with year-round geo filtering + CSV | **79.80 s** | 5,032 candidate rows; 24 processed files; zero failed files |
| Sum of sequential engine calls | **266.77 s (4 min 27 s)** | Model downloads excluded |

Perch stage totals: decode/resample 12.07 s, inference 160.32 s, DB/CSV saving 12.84 s.
The search DB is 57,647,104 bytes; a single top-11 cosine query over 8,640 vectors
took 19.41 ms and recovered the query's own segment at near-zero distance.
Perch's main-process peak RSS was 1,313,357,824 bytes (about 1.22 GiB).
BirdNET's own sampled process-tree benchmark reported 787.39 M total max;
its JSON main-process-only RSS was 118,947,840 bytes. These are different measures.

Local result paths:

- `poc/results/test-perch-day/summary.json`, `inventory.json`, `perch_top5.csv`,
  `search.sqlite`, `similarity_example.csv`.
- `poc/results/test-birdnet-day/summary.json`, `inventory.json`, `birdnet.csv`, `geo.csv`.
- BirdNET detailed stats:
  `poc/models/birdnet/acoustic-benchmarks/lib-v1.1.1/predictions/20260908T132954-session-c731a/run-1/`.

This establishes execution feasibility on the supplied 12-hour corpus. It does not
establish classification correctness, useful cross-recording retrieval, or annual
performance. The annual target has many more one-minute files, so per-file import,
inference scheduling and DB commit costs also need measurement; do not scale only
by total audio duration and call that a measured annual result.

The initial single-file smoke test (`..._060000.wav`, 30 minutes) completed with:

| Engine | Total engine-call wall time | Details |
|---|---:|---|
| Perch | 19.86 s | 360 vectors; inference 6.28 s; decode/resample 0.48 s; DB/CSV save 0.33 s |
| BirdNET | 11.99 s | Geo + model loading 0.88 s; predict call 11.04 s; 341 candidate rows |

These are single observations, not benchmark medians or guarantees.
