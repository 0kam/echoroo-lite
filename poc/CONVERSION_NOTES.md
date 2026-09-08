# Perch V2 -> ONNX conversion spike (M0 linchpin)

Date: 2026-07-07. Environment: full Echoroo repo, `echoroo-backend` container venv
(`/opt/venv`, Python 3.11.15, TensorFlow 2.20.0, onnxruntime 1.27.0). CPU only.

## Verdict: **GO** — use the prebuilt `justinchuby/Perch-onnx` ONNX (recommended: `perch_v2_no_dft.onnx`)

Self-conversion via tf2onnx is NO-GO (StableHLO/XlaCallModule, see below), BUT the
prebuilt Hugging Face ONNX drops in cleanly and is numerically identical to the TF
SavedModel while running **4-9x faster** on CPU via onnxruntime. This unblocks M0
packaging: no TF/XLA freeze needed for Perch.

### Prebuilt ONNX results (onnxruntime CPUExecutionProvider)

Downloaded from HF repo `justinchuby/Perch-onnx` (public, no token):
- `perch_v2.onnx`      = 409,148,616 B (390 MiB)
- `perch_v2_no_dft.onnx` = 413,350,933 B (394 MiB)  (DFT folded into MatMul)

Both expose input `inputs` `[batch, 160000]` f32 and outputs
`embedding [batch,1536]`, `spatial_embedding`, `spectrogram`, `label [batch,14795]` —
i.e. the exact serving_default signature we consume. Drop-in for our embedding path.

Parity vs TF SavedModel serving_default (6 mixed noise+chirp 5 s @ 32 kHz windows):

| model | embedding cosine (mean/min) | max abs diff |
|---|---|---|
| perch_v2.onnx        | 1.000000 / 1.000000 | 1.31e-05 |
| perch_v2_no_dft.onnx | 1.000000 / 1.000000 | 1.33e-05 |

Both far exceed the cosine >= 0.9999 bar; max abs diff ~1.3e-5 (well inside the
~2e-4 no_dft tolerance the model card quotes). no_dft shows no measurable accuracy
penalty here.

Speed (onnxruntime CPU, batch=1 single-segment, warmup excluded):

| model | threads | seg/s | 30-min (360 seg) wall |
|---|---|---|---|
| perch_v2.onnx        | 4 | 16.34 | ~22.0 s |
| perch_v2.onnx        | 8 | 22.59 | ~15.9 s |
| perch_v2_no_dft.onnx | 4 | 19.34 | ~18.6 s |
| perch_v2_no_dft.onnx | 8 | 30.63 | ~11.8 s |

Compare TF SavedModel XLA baseline: 8 thr -> ~66 s,
4 thr -> ~112 s for the same 30-min recording. onnxruntime is ~4x (perch_v2) to ~5.6x
(no_dft) faster AND needs no XLA warmup (~10-15 s/process saved), no TF at runtime.

### Recommendation: adopt `perch_v2_no_dft.onnx`

Fastest (30 % over perch_v2), identical accuracy in test, same signature. Keep
`perch_v2.onnx` as a bit-for-bit-DFT fallback if any downstream numeric drift shows up.

### Working recipe (reproduce in echoroo-backend container)

```bash
# 1. deps into venv only (pyproject/uv.lock untouched, not committed)
docker exec echoroo-backend sh -c \
  'uv pip install --python /opt/venv/bin/python onnxruntime huggingface_hub'

# 2. download both ONNX into cache (NOT git-tracked; ~800 MiB)
docker exec echoroo-backend sh -c 'uv run --python /opt/venv/bin/python - <<PY
from huggingface_hub import hf_hub_download
for f in ["perch_v2.onnx","perch_v2_no_dft.onnx"]:
    hf_hub_download("justinchuby/Perch-onnx", f,
                    local_dir="/home/appuser/.cache/perch_onnx")
PY'

# 3. parity + bench (script in this dir)
docker cp specs/echoroo-lite/poc/onnx_parity_bench.py echoroo-backend:/tmp/
docker exec -e PERCH_THREADS=8 echoroo-backend sh -c \
  'cd /tmp && uv run --python /opt/venv/bin/python python onnx_parity_bench.py'
```

Cached ONNX paths (recorded, not committed):
- `/home/appuser/.cache/perch_onnx/perch_v2.onnx`
- `/home/appuser/.cache/perch_onnx/perch_v2_no_dft.onnx`

### Gotchas
- `hf_hub_download` uses `hf-xet`; it materializes into a `.cache/.../*.incomplete`
  blob then moves it to the final path only at the end — dir looks "stuck at 274 MB"
  mid-download, that's normal, not a stall.
- Unauthenticated HF download warns about rate limits but succeeds (public repo).
- onnxruntime honours `SessionOptions.intra_op_num_threads`; set it explicitly, the
  default may not match the container CPU quota.
- ONNX has 4 outputs; request only `embedding` in `session.run([...])` to skip the
  14795-way classifier head compute when you just need the embedding.
- The `cuInit ... UNKNOWN ERROR (303)` line is TF probing for GPU; harmless on CPU host.

---

## Historical: NO-GO for the self-conversion (tf2onnx) ONNX path

The Perch V2 CPU SavedModel is a **JAX native-serialized StableHLO blob**: the
entire graph is a single `XlaCallModule` op (plus 497 Identity + 4 PreventGradient).
`tf2onnx` has no lowering for `XlaCallModule` / StableHLO, so it cannot produce an
ONNX graph. onnxruntime never gets a model to run; numerical parity / ONNX speed are
therefore not measurable. This is a hard, model-structural blocker, not a version nit.

## Model located (CPU variant)

`AcousticPBDownloaderPerchV2.get_model_path_and_labels("CPU")` downloads and returns:
`~/.cache/kagglehub/models/google/bird-vocalization-classifier/tensorFlow2/perch_v2_cpu/1`
(variables ~388 MB). Note: this differs from the `perch_v2/2` path in the brief; the
`birdnet` CPU resolver fetches a dedicated `perch_v2_cpu` export.

serving_default signature (confirmed):
- input  `inputs`  (None, 160000) float32  (5 s @ 32 kHz)
- outputs: `embedding` (None, 1536), `spectrogram` (None, 500, 128),
  `spatial_embedding` (None, 16, 4, 1536), `label` (None, 14795)

## What was attempted

1. `uv pip install tf2onnx onnx onnxruntime` into `/opt/venv` (pyproject/uv.lock
   untouched). Installed tf2onnx 1.17.0, onnxruntime 1.27.0, onnx 1.22.0.
2. `python -m tf2onnx.convert --saved-model <MP> --output perch_v2.onnx --opset 18
   --tag serve --signature_def serving_default`
   -> `ValueError: Could not infer the attribute type ...` in `onnx.helper.make_attribute`.
3. Suspected onnx-version strictness -> downgraded onnx to 1.16.1 then 1.14.1. Same failure.
4. Root-caused via `_diag_attr.py`: the failing attribute is `Sout`
   (a list of `TensorShapeProto`) on op **`XlaCallModule`** — i.e. StableHLO. tf2onnx
   cannot convert it. Opset choice is irrelevant (fails at graph load, before op mapping).

## Alternatives probed

- **TFLite (`try_tflite.py`)**: converts ONLY with `SELECT_TF_OPS` (Flex). The
  `XlaCallModule` remains a Flex op, so a standalone LiteRT runtime still needs the
  Flex delegate (which embeds full TF/XLA). Output 407 MB. => No lightweight packaging win.
- **TF SavedModel + XLA (works today)**: runs on CPU via XLA JIT. Speed baseline below.

## Speed baseline (TF SavedModel, CPU, XLA JIT, batch=12, random audio)

| intra-op threads | seg/s | 30-min recording (360 seg) wall |
|---|---|---|
| 4 | 3.20 | ~112 s |
| 8 | 5.43 | ~66 s |

inter-op threads = 1. One-time XLA compile warmup ~10-15 s per process (excluded from
the timings; done before measurement). Content-independent (fixed 5 s window), so
extrapolation from a small batch to 360 segments is valid.

## Recommended follow-ups (to actually reach ONNX, in priority order)

1. **Re-export Perch from source without native serialization.** Perch V2 is
   JAX/Flax (google-research `perch` / `perch-hoplite`). Re-export via
   `jax2tf.convert(..., native_serialization=False)` to emit plain TF ops (no
   `XlaCallModule`), then run tf2onnx. This is the only realistic ONNX route and needs
   its own spike (JAX ops may still hit tf2onnx gaps). Requires perch source + params.
2. **Direct JAX->ONNX** (e.g. `jax2onnx`): experimental, higher risk; needs JAX model.
3. **Fallback already in plan.md §7**: keep Perch on the TF+XLA SavedModel inside the
   sidecar. CPU speed is fine (~66-112 s / 30 min). Cost = bundling full TF with
   PyInstaller (heavy ~1 GB+, historically painful) — the real packaging risk shifts
   from "ONNX numerics" to "freeze full TF/XLA". Consider isolating Perch (embedding)
   from BirdNET (already tflite) and treating Perch runtime as the open packaging item.

## Reproduce

```bash
# in echoroo-backend container
uv pip install --python /opt/venv/bin/python tf2onnx onnx onnxruntime
# scripts (docker cp into /tmp, run with: uv run --python /opt/venv/bin/python python <script>)
#   _diag_attr.py    -> proves XlaCallModule/Sout is the blocker
#   bench_tf_cpu.py  -> TF CPU speed (PERCH_THREADS, PERCH_BATCH env)
#   try_tflite.py    -> TFLite converts only via Flex (SELECT_TF_OPS)
```

Installed packages were NOT committed; pyproject.toml / uv.lock unchanged. No ONNX
artifact was produced (conversion failed), so nothing is carried into echoroo-lite yet.
