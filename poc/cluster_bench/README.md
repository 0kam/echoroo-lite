# Fuji clustering benchmark

This one-shot benchmark compares the two clustering candidates defined in
`docs/explore-and-contribute-proposal-2026-11.md`. It writes only aggregate
results to `docs/benchmarks`; embeddings and working files remain under
`.local-data/cluster-bench-20261101`.

```sh
python3.11 -m venv poc/cluster_bench/.venv
poc/cluster_bench/.venv/bin/python -m ensurepip
poc/cluster_bench/.venv/bin/python -m pip install -r poc/cluster_bench/requirements.txt
poc/cluster_bench/.venv/bin/python poc/cluster_bench/bench.py --self-check
poc/cluster_bench/.venv/bin/python poc/cluster_bench/bench.py
```

The Fuji evaluation set (60 WAV clips + per-clip annotation CSVs) is passed with
`--data-dir`; it is not stored in the repository.

Every stage has a 45-minute limit. Input hashes are checked before a Fuji
embedding cache is reused.
