from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.benchmarks.run_stage9_benchmark import main
from zotero_arxiv_daily.observability.benchmark import (
    BenchmarkReport,
    percentile_nearest_rank,
    run_stage9_benchmark,
)


ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "benchmarks" / "stage9"
DAILY_FIXTURE = ROOT / "tests" / "fixtures" / "evidence" / "stage4_golden.json"


def _forbid_network_and_paid_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network, paid call, send, and real sleep are forbidden")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("httpx.Client", forbidden)
    monkeypatch.setattr("openai.OpenAI", forbidden)
    monkeypatch.setattr("time.sleep", forbidden)
    monkeypatch.setattr(
        "zotero_arxiv_daily.delivery.feishu.FeishuClient.send",
        forbidden,
    )


def test_percentiles_use_sorted_integer_nearest_rank() -> None:
    values = (90, 10, 50, 30, 70, 20, 40, 60, 80)
    assert percentile_nearest_rank(values, 25) == 30
    assert percentile_nearest_rank(values, 50) == 50
    assert percentile_nearest_rank(values, 95) == 90
    with pytest.raises(ValueError):
        percentile_nearest_rank((), 50)


def test_offline_benchmark_exercises_exact_shape_and_passes_budgets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_network_and_paid_calls(monkeypatch)
    report = run_stage9_benchmark(
        fixture_root=FIXTURE,
        daily_fixture=DAILY_FIXTURE,
        work_root=tmp_path,
        repetitions=9,
    )

    assert len(report.duration_observations_ns) == 9
    assert tuple(report.duration_observations_ns) == tuple(
        sorted(report.duration_observations_ns)
    )
    assert report.candidate_count == 30
    assert report.selected_for_llm_count == 15
    assert report.selected_for_analysis_count == 5
    assert report.analysis_attempt_count <= 15
    assert report.successful_analysis_call_count <= 5
    assert report.configured_output_token_count <= 40_960
    assert report.network_call_count == 0
    assert report.paid_call_count == 0
    assert report.quality.budget_passed is True
    assert report.budget_passed is True
    assert report.viewer_published_count == 1
    assert len(report.viewer_artifact_hash) == 64


def test_report_is_strict_reproducible_and_privacy_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _forbid_network_and_paid_calls(monkeypatch)
    first = run_stage9_benchmark(
        fixture_root=FIXTURE,
        daily_fixture=DAILY_FIXTURE,
        work_root=tmp_path / "first",
        repetitions=9,
    )
    second = run_stage9_benchmark(
        fixture_root=FIXTURE,
        daily_fixture=DAILY_FIXTURE,
        work_root=tmp_path / "second",
        repetitions=9,
    )
    ignored = {
        "duration_observations_ns",
        "median_duration_ns",
        "p95_duration_ns",
        "q1_duration_ns",
        "q3_duration_ns",
        "peak_traced_allocation_bytes",
    }
    assert first.model_dump(exclude=ignored) == second.model_dump(exclude=ignored)
    serialized = first.to_canonical_json().casefold()
    forbidden = (
        "synthetic-candidate",
        "arxiv:",
        "zotero:",
        "http://",
        "https://",
        "\\users\\",
        "/home/",
        "prompt",
        "response",
        "api_key",
        "hostname",
        "username",
    )
    assert all(value not in serialized for value in forbidden)
    with pytest.raises(Exception):
        BenchmarkReport(**first.model_dump(), hostname="private")


def test_cli_writes_canonical_report_and_returns_nonzero_on_budget_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _forbid_network_and_paid_calls(monkeypatch)
    output = tmp_path / "baseline.json"
    assert main(
        [
            "--fixture-root",
            str(FIXTURE),
            "--daily-fixture",
            str(DAILY_FIXTURE),
            "--work-root",
            str(tmp_path / "work"),
            "--output",
            str(output),
            "--repetitions",
            "9",
        ]
    ) == 0
    parsed = BenchmarkReport.model_validate_json(output.read_bytes())
    assert output.read_text(encoding="utf-8") == parsed.to_canonical_json()
    summary = capsys.readouterr().out
    assert "status=passed" in summary
    assert str(tmp_path) not in summary

    monkeypatch.setattr(
        "tools.benchmarks.run_stage9_benchmark.run_stage9_benchmark",
        lambda **kwargs: parsed.model_copy(update={"budget_passed": False}),
    )
    assert main(
        [
            "--fixture-root",
            str(FIXTURE),
            "--daily-fixture",
            str(DAILY_FIXTURE),
            "--work-root",
            str(tmp_path / "failed-work"),
            "--output",
            str(tmp_path / "failed.json"),
            "--repetitions",
            "9",
        ]
    ) == 2
