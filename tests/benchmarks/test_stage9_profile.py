from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.observability.benchmark import (
    ProfileRow,
    extract_profile_rows,
    select_optimization_target,
)

ROOT = Path(__file__).parents[2]
TRACKED_JSON = ROOT / "docs" / "benchmarks" / "2026-07-26-stage9-baseline.json"
TRACKED_MD = ROOT / "docs" / "benchmarks" / "2026-07-26-stage9-baseline.md"


def _row(
    symbol: str,
    *,
    path: str = "src/zotero_arxiv_daily/viewer/renderer.py",
    time_ns: int = 100,
    allocation_bytes: int = 0,
) -> ProfileRow:
    return ProfileRow(
        symbol=symbol,
        relative_path=path,
        cumulative_time_ns=time_ns,
        allocation_bytes=allocation_bytes,
    )


def test_selector_requires_twenty_percent_and_chooses_largest_share() -> None:
    decision = select_optimization_target(
        (
            _row("viewer.render", time_ns=210),
            _row("viewer.build", time_ns=300),
            _row("viewer.small", time_ns=190),
        ),
        total_time_ns=1000,
        total_allocation_bytes=1000,
    )

    assert decision.symbol == "viewer.build"
    assert decision.time_share_ppm == 300_000
    assert decision.allocation_share_ppm == 0
    assert decision.eligible_count == 2


def test_allocation_share_can_make_a_target_eligible_and_win() -> None:
    decision = select_optimization_target(
        (
            _row("viewer.time", time_ns=300, allocation_bytes=10),
            _row("viewer.memory", time_ns=10, allocation_bytes=500),
        ),
        total_time_ns=1000,
        total_allocation_bytes=1000,
    )

    assert decision.symbol == "viewer.memory"
    assert decision.allocation_share_ppm == 500_000


def test_ties_use_cumulative_time_then_symbol() -> None:
    time_winner = select_optimization_target(
        (
            _row("viewer.a", time_ns=250, allocation_bytes=300),
            _row("viewer.b", time_ns=300, allocation_bytes=250),
        ),
        total_time_ns=1000,
        total_allocation_bytes=1000,
    )
    assert time_winner.symbol == "viewer.b"

    symbol_winner = select_optimization_target(
        (_row("viewer.z", time_ns=250), _row("viewer.a", time_ns=250)),
        total_time_ns=1000,
        total_allocation_bytes=0,
    )
    assert symbol_winner.symbol == "viewer.a"


def test_selector_rejects_no_eligible_target_and_duplicate_symbol() -> None:
    with pytest.raises(ValueError, match="eligible"):
        select_optimization_target(
            (_row("viewer.small", time_ns=199),),
            total_time_ns=1000,
            total_allocation_bytes=0,
        )
    with pytest.raises(ValueError, match="duplicate"):
        select_optimization_target(
            (_row("viewer.same"), _row("viewer.same")),
            total_time_ns=100,
            total_allocation_bytes=0,
        )


def test_selector_rejects_impossible_share_and_ignores_inclusive_wrappers() -> None:
    decision = select_optimization_target(
        (
            _row("pipeline.run_daily", time_ns=900).model_copy(
                update={
                    "eligible": False,
                    "exclusion_code": "inclusive_wrapper",
                }
            ),
            _row("viewer.build", time_ns=300),
        ),
        total_time_ns=1000,
        total_allocation_bytes=0,
    )
    assert decision.symbol == "viewer.build"

    with pytest.raises(ValueError, match="exceeds"):
        select_optimization_target(
            (_row("viewer.impossible", time_ns=1001),),
            total_time_ns=1000,
            total_allocation_bytes=0,
        )


def test_raw_profile_extraction_is_reproducible_and_classifies_boundaries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    daily = root / "src/zotero_arxiv_daily/pipeline/daily.py"
    auditor = root / "src/zotero_arxiv_daily/pipeline/artifacts.py"
    viewer = root / "src/zotero_arxiv_daily/viewer/builder.py"
    benchmark = root / "src/zotero_arxiv_daily/observability/benchmark.py"
    for path in (daily, auditor, viewer, benchmark):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    stats = SimpleNamespace(
        stats={
            (str(daily), 148, "run_daily"): (1, 1, 0.01, 0.80, {}),
            (str(auditor), 180, "audit"): (1, 1, 0.02, 0.40, {}),
            (str(viewer), 19, "build"): (1, 1, 0.10, 0.30, {}),
            (str(benchmark), 1, "_fixture_execution"): (1, 1, 0.1, 0.9, {}),
            (str(tmp_path / "outside.py"), 1, "outside"): (1, 1, 1.0, 1.0, {}),
        }
    )

    rows = extract_profile_rows(stats, repository_root=root)
    by_symbol = {row.symbol: row for row in rows}

    assert tuple(row.symbol for row in rows) == tuple(sorted(by_symbol))
    assert len(rows) == 3
    assert by_symbol[
        "zotero_arxiv_daily.pipeline.daily:run_daily:148"
    ].exclusion_code == "inclusive_wrapper"
    assert by_symbol[
        "zotero_arxiv_daily.pipeline.artifacts:audit:180"
    ].exclusion_code == "security_invariant"
    build = by_symbol["zotero_arxiv_daily.viewer.builder:build:19"]
    assert build.eligible is True
    assert build.self_time_ns == 100_000_000
    assert build.cumulative_time_ns == 300_000_000


@pytest.mark.parametrize(
    ("path", "symbol"),
    [
        ("C:/private/source.py", "viewer.render"),
        ("../source.py", "viewer.render"),
        ("src/other/source.py", "viewer.render"),
        ("src/zotero_arxiv_daily/observability/benchmark.py", "benchmark.fake"),
        ("src/zotero_arxiv_daily/viewer/renderer.py", "viewer.render private/path"),
    ],
)
def test_profile_rows_reject_unsafe_or_excluded_paths_and_symbols(
    path: str, symbol: str
) -> None:
    with pytest.raises(ValidationError):
        _row(symbol, path=path)


def test_selection_is_independent_of_input_order() -> None:
    rows = (
        _row("viewer.a", time_ns=220),
        _row("viewer.b", time_ns=350),
        _row("viewer.c", time_ns=250),
    )
    forward = select_optimization_target(
        rows, total_time_ns=1000, total_allocation_bytes=0
    )
    reverse = select_optimization_target(
        tuple(reversed(rows)), total_time_ns=1000, total_allocation_bytes=0
    )
    assert forward == reverse


def test_tracked_baseline_is_privacy_safe_and_records_measured_target() -> None:
    payload = json.loads(TRACKED_JSON.read_text(encoding="utf-8"))
    markdown = TRACKED_MD.read_text(encoding="utf-8")

    assert set(payload) == {"benchmark", "profile"}
    assert payload["benchmark"]["repetition_count"] == 9
    assert payload["benchmark"]["budget_passed"] is True
    assert payload["profile"] == {
        "profile_version": "stage9-profile-v2",
        "total_time_ns": 2079131100,
        "project_row_count": 207,
        "excluded_count": 92,
        "eligible_count": 2,
        "symbol": "zotero_arxiv_daily.viewer.builder:build:19",
        "relative_path": "src/zotero_arxiv_daily/viewer/builder.py",
        "self_time_ns": 9422200,
        "cumulative_time_ns": 665420900,
        "allocation_bytes": 0,
        "time_share_ppm": 320048,
        "allocation_share_ppm": 0,
    }
    serialized = json.dumps(payload, sort_keys=True).casefold()
    for forbidden in (
        "hostname",
        "username",
        "\\users\\",
        "/home/",
        "api_key",
        "prompt",
        "response",
        "synthetic-candidate",
        "arxiv:",
        "zotero:",
    ):
        assert forbidden not in serialized
    assert "nine warm steady-state repetitions" in markdown
    assert "viewer.builder:build" in markdown
    assert "does not predict real PDF or hosted Actions performance" in markdown
    assert "No network, paid model, Zotero, or Feishu call was performed" in markdown
