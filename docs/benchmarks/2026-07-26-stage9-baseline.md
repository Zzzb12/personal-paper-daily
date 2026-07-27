# Stage 9A Offline Quality, Cost, and Runtime Baseline

**Date:** 2026-07-26  
**Status:** Passed all configured offline budgets.

## Method

The benchmark used one discarded warm-up followed by nine warm steady-state repetitions.
The measured loop exercised the real candidate ranker, Stage 4 validator,
quality evaluator, five-paper static viewer, and artifact audit with
deterministic prebuilt fixture inputs and fake boundaries. Fixture parsing and
construction were completed before the warm-up and were not profiled.

The reviewed command was equivalent to:

`uv run python -m tools.benchmarks.run_stage9_benchmark --fixture-root tests/fixtures/benchmarks/stage9 --daily-fixture tests/fixtures/evidence/stage4_golden.json --work-root outputs/stage9/work-steady --output outputs/stage9/baseline.json --profile-output outputs/stage9/profile.json --repetitions 9`

A nested `cProfile.Profile` was enabled only around the nine measured
repetitions. Imports, fixture discovery, interpreter startup, the discarded
warm-up, pytest, external-library frames, and the benchmark implementation
itself were excluded from target selection.

## Observed baseline

- Shape: 30 candidates, 15 ranking selections, 5 analysis selections.
- Sorted durations: 248,735,500; 258,610,700; 265,940,300; 269,206,500;
  272,165,800; 278,866,900; 282,616,300; 284,043,800; 288,333,700 ns.
- Median: 272,165,800 ns.
- p95: 288,333,700 ns.
- Peak traced Python allocation: 1,594,470 bytes.
- Network calls: 0. Paid calls: 0. Analysis attempts: 0.
- Viewer result: five Stage 4-eligible publications with a reviewed artifact hash.
- Precision@5, recall@15, NDCG@5, evidence precision, and evidence recall:
  1,000,000 ppm.
- Unsupported claims and missing required fields: 0 ppm.

No network, paid model, Zotero, or Feishu call was performed.

## Measured optimization target

The deterministic extractor classified inclusive orchestration frames,
benchmark-only frames, and the artifact auditor security boundary before
selection. The selector found two eligible project rows at the 20% gate.
The selected target is
`zotero_arxiv_daily.viewer.builder:build:19` in
`src/zotero_arxiv_daily/viewer/builder.py`.

Its cumulative time was 646,214,900 ns of 2,444,571,700 ns measured profiler
time, or 264,347 ppm. Its self time was 9,131,400 ns; the measured cost is
therefore primarily its viewer-writing descendants. Stage 9B may optimize only
this measured viewer build path while preserving Stage 4 eligibility and
artifact audit semantics.

## Limits

This deterministic fixture is a regression and comparison tool. It does not predict real PDF or hosted Actions performance.
It does not measure provider
latency, model quality, network variability, Docling model startup, or real
private-library scale.
