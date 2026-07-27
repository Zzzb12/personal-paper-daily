from __future__ import annotations

import argparse
import cProfile
import pstats
from pathlib import Path
from typing import Sequence

from zotero_arxiv_daily.observability.benchmark import (
    build_profile_report,
    run_stage9_benchmark,
)
from zotero_arxiv_daily.pipeline.artifacts import atomic_write_bytes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--daily-fixture", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile-output", type=Path)
    parser.add_argument("--repetitions", type=int, default=9)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    profiler = cProfile.Profile() if arguments.profile_output is not None else None
    report = run_stage9_benchmark(
        fixture_root=arguments.fixture_root,
        daily_fixture=arguments.daily_fixture,
        work_root=arguments.work_root,
        repetitions=arguments.repetitions,
        steady_state_profiler=profiler,
    )
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(output, report.to_canonical_json().encode("utf-8"))
    if profiler is not None:
        profile_report = build_profile_report(
            pstats.Stats(profiler),
            repository_root=Path(__file__).parents[2],
        )
        profile_output = arguments.profile_output.resolve()
        profile_output.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(
            profile_output,
            profile_report.to_canonical_json().encode("utf-8"),
        )
    status = "passed" if report.budget_passed else "failed"
    print(
        f"status={status} repetitions={report.repetition_count} "
        f"median_ns={report.median_duration_ns} p95_ns={report.p95_duration_ns}"
    )
    return 0 if report.budget_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
