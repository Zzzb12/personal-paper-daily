from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tools.benchmarks.run_stage9_benchmark import main
from zotero_arxiv_daily.observability.benchmark import (
    OptimizationComparison,
    build_optimization_comparison,
    run_stage9_paired_comparison,
)


ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "benchmarks" / "stage9"
DAILY_FIXTURE = ROOT / "tests" / "fixtures" / "evidence" / "stage4_golden.json"
HASH = "a" * 64
TRACKED_JSON = (
    ROOT / "docs" / "benchmarks" / "2026-07-26-stage9-parallel-viewer-writes.json"
)
TRACKED_MD = (
    ROOT / "docs" / "benchmarks" / "2026-07-26-stage9-parallel-viewer-writes.md"
)


def test_comparison_math_enforces_exact_median_and_p95_gates() -> None:
    passing = build_optimization_comparison(
        baseline_observations_ns=(1_000_000,) * 9,
        optimized_observations_ns=(900_000,) * 9,
        baseline_peak_bytes=100,
        optimized_peak_bytes=100,
        equivalence_hash=HASH,
    )
    assert passing.improvement_ppm == 100_000
    assert passing.p95_ratio_ppm == 900_000
    assert passing.passed is True

    below_ten = build_optimization_comparison(
        baseline_observations_ns=(1_000_000,) * 9,
        optimized_observations_ns=(900_001,) * 9,
        baseline_peak_bytes=100,
        optimized_peak_bytes=100,
        equivalence_hash=HASH,
    )
    assert below_ten.improvement_ppm == 99_999
    assert below_ten.passed is False

    p95_too_high = build_optimization_comparison(
        baseline_observations_ns=(1_000_000,) * 9,
        optimized_observations_ns=(900_000,) * 8 + (1_050_001,),
        baseline_peak_bytes=100,
        optimized_peak_bytes=100,
        equivalence_hash=HASH,
    )
    assert p95_too_high.p95_ratio_ppm == 1_050_001
    assert p95_too_high.passed is False


def test_comparison_rejects_mismatched_pairs_and_unsafe_identity() -> None:
    with pytest.raises(ValueError):
        build_optimization_comparison(
            baseline_observations_ns=(100,) * 9,
            optimized_observations_ns=(90,) * 10,
            baseline_peak_bytes=1,
            optimized_peak_bytes=1,
            equivalence_hash=HASH,
        )
    with pytest.raises(Exception):
        OptimizationComparison(
            **build_optimization_comparison(
                baseline_observations_ns=(100,) * 9,
                optimized_observations_ns=(90,) * 9,
                baseline_peak_bytes=1,
                optimized_peak_bytes=1,
                equivalence_hash=HASH,
            ).model_dump(),
            private_path="PRIVATE",
        )
    with pytest.raises((ValueError, ValidationError)):
        build_optimization_comparison(
            baseline_observations_ns=(0,) + (100,) * 8,
            optimized_observations_ns=(90,) * 9,
            baseline_peak_bytes=1,
            optimized_peak_bytes=1,
            equivalence_hash=HASH,
        )


def test_paired_runner_alternates_order_and_uses_fresh_roots(
    tmp_path: Path,
) -> None:
    calls: list[tuple[bool, Path]] = []

    def variant_runner(prepared, root: Path, parallel_writes: bool) -> str:
        calls.append((parallel_writes, root))
        return HASH

    clock_values = iter(
        value
        for _ in range(18)
        for value in (0, 1_000_000, 0, 900_000)
    )
    comparison = run_stage9_paired_comparison(
        fixture_root=FIXTURE,
        daily_fixture=DAILY_FIXTURE,
        work_root=tmp_path,
        pairs=9,
        monotonic_ns=lambda: next(clock_values),
        variant_runner=variant_runner,
        peak_sampler=lambda: 1,
    )

    measured = calls[2:]
    assert calls[:2] == [
        (False, tmp_path.resolve() / "warmup-baseline"),
        (True, tmp_path.resolve() / "warmup-optimized"),
    ]
    assert [parallel for parallel, _ in measured[:4]] == [
        False,
        True,
        True,
        False,
    ]
    assert len({root for _, root in measured}) == 18
    assert comparison.pair_count == 9


def test_paired_runner_rejects_any_semantic_mismatch(tmp_path: Path) -> None:
    calls = 0

    def variant_runner(prepared, root: Path, parallel_writes: bool) -> str:
        nonlocal calls
        calls += 1
        return ("b" if calls == 4 else "a") * 64

    with pytest.raises(ValueError, match="equivalence"):
        run_stage9_paired_comparison(
            fixture_root=FIXTURE,
            daily_fixture=DAILY_FIXTURE,
            work_root=tmp_path,
            pairs=9,
            monotonic_ns=iter(range(100)).__next__,
            variant_runner=variant_runner,
            peak_sampler=lambda: 1,
        )


def test_cli_writes_canonical_comparison_and_returns_gate_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    passing = build_optimization_comparison(
        baseline_observations_ns=(1_000_000,) * 9,
        optimized_observations_ns=(900_000,) * 9,
        baseline_peak_bytes=100,
        optimized_peak_bytes=90,
        equivalence_hash=HASH,
    )
    monkeypatch.setattr(
        "tools.benchmarks.run_stage9_benchmark.run_stage9_paired_comparison",
        lambda **kwargs: passing,
        raising=False,
    )
    output = tmp_path / "comparison.json"
    arguments = [
        "--fixture-root",
        str(FIXTURE),
        "--daily-fixture",
        str(DAILY_FIXTURE),
        "--work-root",
        str(tmp_path / "work"),
        "--comparison-output",
        str(output),
        "--pairs",
        "9",
    ]

    assert main(arguments) == 0
    assert output.read_text(encoding="utf-8") == passing.to_canonical_json()
    summary = capsys.readouterr().out
    assert "status=passed" in summary
    assert "improvement_ppm=100000" in summary
    assert str(tmp_path) not in summary

    monkeypatch.setattr(
        "tools.benchmarks.run_stage9_benchmark.run_stage9_paired_comparison",
        lambda **kwargs: passing.model_copy(update={"passed": False}),
        raising=False,
    )
    failed_arguments = list(arguments)
    failed_arguments[failed_arguments.index(str(output))] = str(tmp_path / "fail.json")
    assert main(failed_arguments) == 2


def test_cli_rejects_profile_output_in_comparison_mode(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--fixture-root",
                str(FIXTURE),
                "--daily-fixture",
                str(DAILY_FIXTURE),
                "--work-root",
                str(tmp_path / "work"),
                "--comparison-output",
                str(tmp_path / "comparison.json"),
                "--profile-output",
                str(tmp_path / "profile.json"),
            ]
        )


def test_paired_runner_stops_only_tracing_it_started(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tracing = False
    calls: list[str] = []

    def is_tracing() -> bool:
        return tracing

    def start() -> None:
        nonlocal tracing
        tracing = True
        calls.append("start")

    def stop() -> None:
        nonlocal tracing
        tracing = False
        calls.append("stop")

    monkeypatch.setattr(
        "zotero_arxiv_daily.observability.benchmark.tracemalloc.is_tracing",
        is_tracing,
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.observability.benchmark.tracemalloc.start",
        start,
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.observability.benchmark.tracemalloc.stop",
        stop,
    )
    monkeypatch.setattr(
        "zotero_arxiv_daily.observability.benchmark.tracemalloc.reset_peak",
        lambda: calls.append("reset"),
    )
    clock = iter(value for _ in range(18) for value in (0, 100, 0, 90))

    variant_calls = 0

    def variant(prepared, root, parallel):
        nonlocal variant_calls
        variant_calls += 1
        if variant_calls == 1:
            start()
        return HASH

    run_stage9_paired_comparison(
        fixture_root=FIXTURE,
        daily_fixture=DAILY_FIXTURE,
        work_root=tmp_path,
        pairs=9,
        monotonic_ns=clock.__next__,
        variant_runner=variant,
        peak_sampler=lambda: 1,
    )

    assert calls.count("start") == 1
    assert calls.count("stop") == 1
    assert tracing is False


def test_tracked_optimization_evidence_is_identity_and_privacy_safe() -> None:
    payload = json.loads(TRACKED_JSON.read_text(encoding="utf-8"))
    markdown = TRACKED_MD.read_text(encoding="utf-8")
    assert set(payload) == {
        "candidate",
        "confirmation",
        "confirmation_protocol_version",
        "evidence_version",
        "historical_observations",
        "optimized_profile",
        "semantic_audit",
    }
    confirmation = payload["confirmation"]
    assert payload["confirmation_protocol_version"] == "stage9c-confirmation-v1"
    assert confirmation["pair_count"] == 99
    assert confirmation["improvement_ppm"] >= 100_000
    assert confirmation["p95_ratio_ppm"] <= 1_050_000
    assert confirmation["passed"] is True
    assert len(confirmation["equivalence_hash"]) == 64
    assert len(confirmation["raw_result_sha256"]) == 64
    assert confirmation["baseline_distribution_ns"]["median"] == 234_164_600
    assert confirmation["optimized_distribution_ns"]["median"] == 208_970_900
    assert payload["candidate"] == {
        "frontend_assets_changed": False,
        "max_write_workers": 8,
        "name": "bounded_parallel_atomic_viewer_writes",
    }
    assert payload["historical_observations"] == [
        {
            "classification": "formal_failure",
            "improvement_ppm": 80_858,
            "pair_count": 15,
            "p95_ratio_ppm": 966_262,
            "passed": False,
        },
        {
            "classification": "exploratory_non_verdict",
            "improvement_ppm": 119_479,
            "pair_count": 31,
            "p95_ratio_ppm": 854_900,
            "passed": True,
        },
    ]
    assert payload["semantic_audit"] == {
        "candidate_count": 30,
        "network_call_count": 0,
        "paid_call_count": 0,
        "quality_budget_passed": True,
        "selected_for_analysis_count": 5,
        "selected_for_llm_count": 15,
        "selection_labels_matched": True,
        "viewer_published_count": 5,
    }
    assert payload["optimized_profile"]["target_status"] == "no_eligible_target"

    serialized = json.dumps(payload, sort_keys=True).casefold() + markdown.casefold()
    for forbidden in (
        "hostname",
        "username",
        "\\users\\",
        "/home/",
        "api_key",
        "zotero_key",
        "app_secret",
        "prompt",
        "response",
        "synthetic-candidate",
        "arxiv:",
        "zotero:",
    ):
        assert forbidden not in serialized
    assert "99 对" in markdown
    assert "15 对" in markdown
    assert "31 对" in markdown
    assert "frontend design" in markdown.casefold()
    assert "gsap" in markdown.casefold()
