from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.observability.benchmark import (
    ProfileRow,
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
        "profile_version": "stage9-profile-v1",
        "total_time_ns": 1403841400,
        "project_row_count": 426,
        "eligible_count": 7,
        "symbol": "zotero_arxiv_daily.pipeline.daily:run_daily:148",
        "relative_path": "src/zotero_arxiv_daily/pipeline/daily.py",
        "cumulative_time_ns": 1124229500,
        "allocation_bytes": 0,
        "time_share_ppm": 800824,
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
    assert "run_daily" in markdown
    assert "does not predict real PDF or hosted Actions performance" in markdown
    assert "No network, paid model, Zotero, or Feishu call was performed" in markdown
