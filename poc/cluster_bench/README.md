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

## Hierarchy prototype (2026-11-03)

`hierarchy_proto.py` tries the drill-down revision (leaf k-means → Ward tree → flattened
levels) against the same Fuji annotations, against sound maps already built in an Echoroo Lite
data folder (`user --data DIR --datasets …`, read-only), and for timing on the synthetic
210,000-segment matrix (`scale`). The recorded output is
`docs/benchmarks/hierarchy-proto-2026-11-03.txt`.
Follow-ups the same day, after the user asked for a complete hierarchy and a nonlinear map:
`hierarchy_exact.py` (exact Ward on all points vs leaves, trees down to single sounds),
`embedding_proto.py` (numpy-only PaCMAP vs PCA 2D vs openTSNE), `embedding_landmarks.py`
(landmark layout for 210,000 segments) and `hierarchy_mock.py` (bakes
`docs/prototypes/explore-hierarchy-mock.html` from `hierarchy_mock_template.html`). The user-data
runs read an Echoroo Lite data folder named by `ECHOROO_PROTO_DATA` (read-only).
`hierarchy_mock_v21.py` bakes the v2.1 redesign of the same mock (rows that open to their sounds,
a sound card under the map, top-3 AI candidates per sound from the Japan candidate list).

## Map agreement prototype (2026-11-05)

`agreement_proto.py` measures how well the sound map's groups match what the map shows (island purity, badge
accuracy, chance-corrected 10-NN agreement) and the species fidelity for eight ways to build the tree or the map.
Run it with this folder's venv (scikit-learn for one method, Pillow for the figures). The recorded output and the
figures are `docs/benchmarks/agreement-proto-2026-11-05.*` and `docs/benchmarks/agreement-{fuji,3}-2026-11-05.png`.

## Layout prototype (2026-11-05)

`layout_proto.py` scores map layouts on the Fuji annotations (2-D 10-NN species purity and the best-node F1 of a tree
built from the map), over three seeds: PaCMAP settings, a numpy UMAP-style layout, exact t-SNE as a reference, input
preprocessing, and inputs with Kinglet-B embeddings or Perch class scores added (`fuji_extra.py` computes those from the
Fuji WAV clips into `.local-data`). Results: `docs/benchmarks/layout-proto-2026-11-05.*` and `layout-fuji-2026-11-05.png`.
