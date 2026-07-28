import json
from pathlib import Path

import numpy as np
import pytest

from tools.benchmarks.compare_rerankers import (
    MODEL_SPECS,
    canonical_fixture_hash,
    compare_report,
    retrieval_metrics,
)


REPORT = (
    Path(__file__).parents[2]
    / "docs"
    / "benchmarks"
    / "2026-07-28-reranker-linux-repair.json"
)


def test_retrieval_metrics_use_mean_cosine_and_stable_input_ties():
    interests = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    candidates = np.asarray(
        [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0], [0.0, -1.0]],
        dtype=np.float32,
    )

    result = retrieval_metrics(
        interests,
        candidates,
        labels=(1, 1, 0, 0),
        top_k=2,
    )

    assert result == {
        "ndcg_at_2_ppm": 1_000_000,
        "precision_at_2_ppm": 1_000_000,
        "recall_at_2_ppm": 1_000_000,
        "top2_labels": [1, 1],
    }


def test_report_fixture_and_model_specs_are_bound_to_recomputation_code():
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert canonical_fixture_hash(report["fixture_data"]) == report["fixture_sha256"]
    for name, spec in MODEL_SPECS.items():
        recorded = report["models"][name]
        assert recorded["model"] == spec.model
        assert recorded["revision"] == spec.revision
        assert recorded["trust_remote_code"] is spec.trust_remote_code
        assert recorded["encode_kwargs"] == spec.encode_kwargs


@pytest.mark.slow
def test_tracked_report_recomputes_with_both_fixed_models_offline():
    result = compare_report(
        REPORT,
        replacement_cache_folder=Path("models/reranker"),
    )

    assert result["verdict"] == "non_regression_pass"
    assert result["models"]["reference"]["precision_at_5_ppm"] == 1_000_000
    assert result["models"]["replacement"]["precision_at_5_ppm"] == 1_000_000
