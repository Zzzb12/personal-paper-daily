# Stage 9A Offline Quality, Cost, and Runtime Baseline

**Date:** 2026-07-26  
**Status:** Passed all configured offline budgets.

## Method

The benchmark used one discarded warm-up followed by nine warm steady-state repetitions.
The measured loop exercised the real candidate ranker, aggregate
quality evaluator, Stage 4 validation, static viewer, artifact audit, run
metrics, and manifest paths with deterministic fixture and fake boundaries.

The reviewed command was equivalent to:

`uv run python -m tools.benchmarks.run_stage9_benchmark --fixture-root tests/fixtures/benchmarks/stage9 --daily-fixture tests/fixtures/evidence/stage4_golden.json --work-root outputs/stage9/work-steady --output outputs/stage9/baseline.json --repetitions 9`

A nested `cProfile.Profile` was enabled only around the nine measured
repetitions. Imports, fixture discovery, interpreter startup, the discarded
warm-up, pytest, external-library frames, and the benchmark implementation
itself were excluded from target selection.

## Observed baseline

- Shape: 30 candidates, 15 ranking selections, 5 analysis selections.
- Sorted durations: 142,229,300; 146,926,900; 147,909,300; 149,096,800;
  149,844,400; 153,715,900; 154,974,500; 167,857,400; 195,378,900 ns.
- Median: 149,844,400 ns.
- p95: 195,378,900 ns.
- Peak traced Python allocation: 1,438,193 bytes.
- Network calls: 0. Paid calls: 0. Analysis attempts: 0.
- Viewer result: one eligible publication with a reviewed artifact hash.
- Precision@5, recall@15, NDCG@5, evidence precision, and evidence recall:
  1,000,000 ppm.
- Unsupported claims and missing required fields: 0 ppm.

No network, paid model, Zotero, or Feishu call was performed.

## Measured optimization target

The deterministic selector found seven eligible project rows at the 20% gate.
The selected target is
`zotero_arxiv_daily.pipeline.daily:run_daily:148` in
`src/zotero_arxiv_daily/pipeline/daily.py`.

Its cumulative time was 1,124,229,500 ns of 1,403,841,400 ns measured profiler
time, or 800,824 ppm. This is an inclusive orchestration path; the largest
measured descendants were artifact audit and viewer build. Stage 9B therefore
uses the `run_daily` end-to-end paired distribution as its performance verdict
and permits only one semantics-preserving internal optimization.

## Limits

This deterministic fixture is a regression and comparison tool. It does not predict real PDF or hosted Actions performance.
It does not measure provider
latency, model quality, network variability, Docling model startup, or real
private-library scale.
