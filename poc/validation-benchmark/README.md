# Reference scripts for the validation benchmark

Copied from a design consultation on 2026-09-30 for reference only. They were run
against a local score file (`jay_scores.npz`, one site's BirdNET scores) that is not
included, and `sim.py` still points at the original scratch path. See
[../../docs/validation-benchmark-handoff-2026-09-30.md](../../docs/validation-benchmark-handoff-2026-09-30.md).

- `sketch_estimate.py`: threshold-anchored strata, floor + logistic curve on a grid
  posterior, posterior predictive over the population, stratum-wise Beta cross-check.
- `sim.py`: simulations comparing the current method, stratified Beta and the curve.
- `perch_sim.py`: re-implementation of Perch's `call_density.py` for comparison.
