"""Diagnostic: identify the TF attr that tf2onnx cannot lower to ONNX.

Wraps onnx.helper.make_attribute to log the offending key/value on failure.
Run inside the echoroo-backend container venv.
"""

from __future__ import annotations

import sys

import onnx.helper as oh

_orig = oh.make_attribute


def _patched(key, value, doc_string=None):  # type: ignore[no-untyped-def]
    try:
        return _orig(key, value, doc_string)
    except Exception:
        try:
            vlist = list(value)
            types = {type(v) for v in vlist}
            print(
                f"[DIAG] make_attribute FAILED key={key!r} "
                f"len={len(vlist)} elem_types={types} sample={vlist[:3]!r}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[DIAG] key={key!r} value_type={type(value)} ({e})", file=sys.stderr)
        raise


oh.make_attribute = _patched

MP = (
    "/home/appuser/.cache/kagglehub/models/google/"
    "bird-vocalization-classifier/tensorFlow2/perch_v2_cpu/1"
)

sys.argv = [
    "tf2onnx.convert",
    "--saved-model",
    MP,
    "--output",
    "/tmp/perch_v2.onnx",
    "--opset",
    "18",
    "--tag",
    "serve",
    "--signature_def",
    "serving_default",
]

from tf2onnx import convert  # noqa: E402

convert.main()
