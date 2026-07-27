from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import time
import tracemalloc
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Self

import numpy as np
from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.paper_schemas import AnalysisBatchResult
from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidatePaper,
    CandidateSelectionLimits,
    InterestPaper,
    StrictModel,
)
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    EmbeddingIdentity,
    RankingLimits,
)
from zotero_arxiv_daily.delivery.ledger import DeliveryLedger
from zotero_arxiv_daily.observability.collector import (
    MetricsSession,
    default_peak_memory_sampler,
)
from zotero_arxiv_daily.observability.metrics import RunMetrics
from zotero_arxiv_daily.observability.quality import (
    ClaimLabel,
    EvidenceLabel,
    QualityEvaluation,
    RankedLabel,
    RequiredFieldLabel,
    evaluate_quality,
)
from zotero_arxiv_daily.observability.store import MetricsWriter
from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor, ManifestStore
from zotero_arxiv_daily.pipeline.daily import (
    AnalysisStageOutput,
    DailyDependencies,
    DailySettings,
    PreparedDelivery,
    run_daily,
)
from zotero_arxiv_daily.pipeline.validation import (
    ValidationDependencies,
    ValidationSettings,
    _offline_inputs,
    build_validation_batch,
)
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import ViewerSettings


REPORT_VERSION = "stage9-benchmark-v1"
_SHA256 = "0123456789abcdef"
_PROFILE_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:<>\[\]-]{0,255}$")


@dataclass
class BenchmarkBoundaryCounts:
    network_call_count: int = 0
    paid_call_count: int = 0


class OptimizationComparison(StrictModel):
    comparison_version: Literal["stage9-optimization-v1"] = (
        "stage9-optimization-v1"
    )
    pair_count: int = Field(ge=9, le=99)
    baseline_observations_ns: tuple[int, ...]
    optimized_observations_ns: tuple[int, ...]
    baseline_median_ns: int = Field(gt=0)
    optimized_median_ns: int = Field(gt=0)
    baseline_p95_ns: int = Field(gt=0)
    optimized_p95_ns: int = Field(gt=0)
    improvement_ppm: int
    p95_ratio_ppm: int = Field(ge=0)
    baseline_peak_bytes: int = Field(ge=0)
    optimized_peak_bytes: int = Field(ge=0)
    equivalence_hash: str
    passed: bool

    @field_validator("equivalence_hash")
    @classmethod
    def validate_equivalence_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in _SHA256 for character in value):
            raise ValueError("equivalence identity must be a SHA-256 digest")
        return value

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        baseline = self.baseline_observations_ns
        optimized = self.optimized_observations_ns
        if any(value <= 0 for value in (*baseline, *optimized)):
            raise ValueError("comparison observations must be positive")
        if (
            len(baseline) != self.pair_count
            or len(optimized) != self.pair_count
            or baseline != tuple(sorted(baseline))
            or optimized != tuple(sorted(optimized))
        ):
            raise ValueError("comparison observations must be sorted paired samples")
        expected_values = (
            percentile_nearest_rank(baseline, 50),
            percentile_nearest_rank(optimized, 50),
            percentile_nearest_rank(baseline, 95),
            percentile_nearest_rank(optimized, 95),
        )
        if expected_values != (
            self.baseline_median_ns,
            self.optimized_median_ns,
            self.baseline_p95_ns,
            self.optimized_p95_ns,
        ):
            raise ValueError("comparison summary differs from observations")
        expected_improvement = _signed_improvement_ppm(
            self.baseline_median_ns,
            self.optimized_median_ns,
        )
        expected_ratio = _share_ppm(
            self.optimized_p95_ns,
            self.baseline_p95_ns,
        )
        if (
            self.improvement_ppm != expected_improvement
            or self.p95_ratio_ppm != expected_ratio
        ):
            raise ValueError("comparison ratios differ from observations")
        expected_passed = expected_improvement >= 100_000 and expected_ratio <= 1_050_000
        if self.passed != expected_passed:
            raise ValueError(f"comparison passed must be {expected_passed}")
        return self

    def to_canonical_json(self) -> str:
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )


def _signed_improvement_ppm(baseline: int, optimized: int) -> int:
    difference = baseline - optimized
    magnitude = (abs(difference) * 1_000_000 + baseline // 2) // baseline
    return magnitude if difference >= 0 else -magnitude


def build_optimization_comparison(
    *,
    baseline_observations_ns: tuple[int, ...],
    optimized_observations_ns: tuple[int, ...],
    baseline_peak_bytes: int,
    optimized_peak_bytes: int,
    equivalence_hash: str,
) -> OptimizationComparison:
    if (
        len(baseline_observations_ns) != len(optimized_observations_ns)
        or len(baseline_observations_ns) < 9
    ):
        raise ValueError("comparison requires at least nine paired observations")
    baseline = tuple(sorted(baseline_observations_ns))
    optimized = tuple(sorted(optimized_observations_ns))
    baseline_median = percentile_nearest_rank(baseline, 50)
    optimized_median = percentile_nearest_rank(optimized, 50)
    baseline_p95 = percentile_nearest_rank(baseline, 95)
    optimized_p95 = percentile_nearest_rank(optimized, 95)
    improvement = _signed_improvement_ppm(baseline_median, optimized_median)
    ratio = _share_ppm(optimized_p95, baseline_p95)
    return OptimizationComparison(
        pair_count=len(baseline),
        baseline_observations_ns=baseline,
        optimized_observations_ns=optimized,
        baseline_median_ns=baseline_median,
        optimized_median_ns=optimized_median,
        baseline_p95_ns=baseline_p95,
        optimized_p95_ns=optimized_p95,
        improvement_ppm=improvement,
        p95_ratio_ppm=ratio,
        baseline_peak_bytes=baseline_peak_bytes,
        optimized_peak_bytes=optimized_peak_bytes,
        equivalence_hash=equivalence_hash,
        passed=improvement >= 100_000 and ratio <= 1_050_000,
    )


class ProfileRow(StrictModel):
    symbol: str
    relative_path: str
    self_time_ns: int = Field(default=0, ge=0)
    cumulative_time_ns: int = Field(ge=0)
    allocation_bytes: int = Field(ge=0)
    eligible: bool = True
    exclusion_code: Literal[
        "inclusive_wrapper",
        "security_invariant",
        "generated_wrapper",
    ] | None = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        if not _PROFILE_SYMBOL_RE.fullmatch(value):
            raise ValueError("profile symbol is unsafe")
        return value

    @field_validator("relative_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if (
            path.is_absolute()
            or windows.is_absolute()
            or ".." in path.parts
            or not value.startswith("src/zotero_arxiv_daily/")
            or value.endswith("/observability/benchmark.py")
        ):
            raise ValueError("profile path is unsafe or excluded")
        return value

    @field_validator(
        "self_time_ns", "cumulative_time_ns", "allocation_bytes", mode="before"
    )
    @classmethod
    def reject_boolean_counts(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("profile counts must not be booleans")
        return value

    @model_validator(mode="after")
    def validate_eligibility(self) -> Self:
        if self.self_time_ns > self.cumulative_time_ns:
            raise ValueError("profile self time cannot exceed cumulative time")
        if self.eligible == (self.exclusion_code is not None):
            raise ValueError("profile eligibility and exclusion code differ")
        return self


class ProfileTargetDecision(StrictModel):
    symbol: str
    relative_path: str
    cumulative_time_ns: int = Field(ge=0)
    allocation_bytes: int = Field(ge=0)
    time_share_ppm: int = Field(ge=0)
    allocation_share_ppm: int = Field(ge=0)
    eligible_count: int = Field(ge=1)


class ProfileReport(StrictModel):
    profile_version: Literal["stage9-profile-v3"] = "stage9-profile-v3"
    total_time_ns: int = Field(gt=0)
    project_row_count: int = Field(ge=1)
    excluded_count: int = Field(ge=0)
    target_status: Literal["selected", "no_eligible_target"]
    eligible_count: int = Field(ge=0)
    symbol: str | None = None
    relative_path: str | None = None
    self_time_ns: int | None = Field(default=None, ge=0)
    cumulative_time_ns: int | None = Field(default=None, ge=0)
    allocation_bytes: int | None = Field(default=None, ge=0)
    time_share_ppm: int | None = Field(default=None, ge=0, le=1_000_000)
    allocation_share_ppm: int | None = Field(default=None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if self.excluded_count >= self.project_row_count:
            raise ValueError("profile must retain eligible project rows")
        target_fields = (
            self.symbol,
            self.relative_path,
            self.self_time_ns,
            self.cumulative_time_ns,
            self.allocation_bytes,
            self.time_share_ppm,
            self.allocation_share_ppm,
        )
        if self.target_status == "no_eligible_target":
            if self.eligible_count != 0 or any(
                value is not None for value in target_fields
            ):
                raise ValueError("empty profile target must not contain target fields")
            return self
        if self.eligible_count < 1 or any(value is None for value in target_fields):
            raise ValueError("selected profile target is incomplete")
        assert self.symbol is not None
        assert self.relative_path is not None
        assert self.self_time_ns is not None
        assert self.cumulative_time_ns is not None
        assert self.allocation_bytes is not None
        assert self.time_share_ppm is not None
        assert self.allocation_share_ppm is not None
        ProfileRow(
            symbol=self.symbol,
            relative_path=self.relative_path,
            self_time_ns=self.self_time_ns,
            cumulative_time_ns=self.cumulative_time_ns,
            allocation_bytes=self.allocation_bytes,
        )
        if self.cumulative_time_ns > self.total_time_ns:
            raise ValueError("profile target exceeds total time")
        if max(self.time_share_ppm, self.allocation_share_ppm) < 200_000:
            raise ValueError("selected profile target is below the threshold")
        return self

    def to_canonical_json(self) -> str:
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )


def _share_ppm(value: int, total: int) -> int:
    if total == 0:
        return 0
    return (value * 1_000_000 + total // 2) // total


def select_optimization_target(
    rows: tuple[ProfileRow, ...],
    *,
    total_time_ns: int,
    total_allocation_bytes: int,
) -> ProfileTargetDecision:
    if (
        isinstance(total_time_ns, bool)
        or isinstance(total_allocation_bytes, bool)
        or total_time_ns <= 0
        or total_allocation_bytes < 0
    ):
        raise ValueError("profile totals are invalid")
    symbols = tuple(row.symbol for row in rows)
    if len(symbols) != len(set(symbols)):
        raise ValueError("profile rows contain a duplicate symbol")
    candidates: list[tuple[int, int, str, ProfileRow, int, int]] = []
    for row in rows:
        if row.cumulative_time_ns > total_time_ns or (
            row.allocation_bytes > total_allocation_bytes
        ):
            raise ValueError("profile row exceeds profile totals")
        if not row.eligible:
            continue
        time_share = _share_ppm(row.cumulative_time_ns, total_time_ns)
        allocation_share = _share_ppm(
            row.allocation_bytes, total_allocation_bytes
        )
        largest_share = max(time_share, allocation_share)
        if largest_share >= 200_000:
            candidates.append(
                (
                    -largest_share,
                    -row.cumulative_time_ns,
                    row.symbol,
                    row,
                    time_share,
                    allocation_share,
                )
            )
    if not candidates:
        raise ValueError("profile contains no eligible optimization target")
    candidates.sort(key=lambda item: item[:3])
    _, _, _, selected, time_share, allocation_share = candidates[0]
    return ProfileTargetDecision(
        symbol=selected.symbol,
        relative_path=selected.relative_path,
        cumulative_time_ns=selected.cumulative_time_ns,
        allocation_bytes=selected.allocation_bytes,
        time_share_ppm=time_share,
        allocation_share_ppm=allocation_share,
        eligible_count=len(candidates),
    )


def extract_profile_rows(
    profile_stats: object,
    *,
    repository_root: Path,
) -> tuple[ProfileRow, ...]:
    """Convert raw cProfile rows into privacy-safe, reproducible project rows."""
    stats = getattr(profile_stats, "stats", None)
    if not isinstance(stats, dict):
        raise ValueError("profile stats are unavailable")
    root = Path(repository_root).resolve()
    rows: list[ProfileRow] = []
    for key, values in stats.items():
        if (
            not isinstance(key, tuple)
            or len(key) != 3
            or not isinstance(values, tuple)
            or len(values) < 4
        ):
            raise ValueError("profile stats contain an invalid row")
        filename, line_number, function_name = key
        if not isinstance(filename, str) or not isinstance(function_name, str):
            raise ValueError("profile stats contain an invalid symbol")
        try:
            relative = Path(filename).resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if (
            not relative.startswith("src/zotero_arxiv_daily/")
            or relative.endswith("/observability/benchmark.py")
        ):
            continue
        try:
            line = int(line_number)
            self_ns = round(float(values[2]) * 1_000_000_000)
            cumulative_ns = round(float(values[3]) * 1_000_000_000)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("profile stats contain an invalid measurement") from None
        if line < 0 or self_ns < 0 or cumulative_ns < 0:
            raise ValueError("profile stats contain an invalid measurement")
        module = relative.removeprefix("src/").removesuffix(".py").replace("/", ".")
        symbol = f"{module}:{function_name}:{line}"
        exclusion_code = _profile_exclusion(relative, function_name)
        rows.append(
            ProfileRow(
                symbol=symbol,
                relative_path=relative,
                self_time_ns=self_ns,
                cumulative_time_ns=cumulative_ns,
                allocation_bytes=0,
                eligible=exclusion_code is None,
                exclusion_code=exclusion_code,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.symbol))


def build_profile_report(
    profile_stats: object,
    *,
    repository_root: Path,
) -> ProfileReport:
    total_seconds = getattr(profile_stats, "total_tt", None)
    if not isinstance(total_seconds, (int, float)) or total_seconds <= 0:
        raise ValueError("profile total time is unavailable")
    total_time_ns = round(float(total_seconds) * 1_000_000_000)
    rows = extract_profile_rows(profile_stats, repository_root=repository_root)
    try:
        decision = select_optimization_target(
            rows,
            total_time_ns=total_time_ns,
            total_allocation_bytes=0,
        )
    except ValueError as error:
        if str(error) != "profile contains no eligible optimization target":
            raise
        return ProfileReport(
            total_time_ns=total_time_ns,
            project_row_count=len(rows),
            excluded_count=sum(not row.eligible for row in rows),
            target_status="no_eligible_target",
            eligible_count=0,
        )
    selected = next(row for row in rows if row.symbol == decision.symbol)
    return ProfileReport(
        total_time_ns=total_time_ns,
        project_row_count=len(rows),
        excluded_count=sum(not row.eligible for row in rows),
        target_status="selected",
        eligible_count=decision.eligible_count,
        symbol=decision.symbol,
        relative_path=decision.relative_path,
        self_time_ns=selected.self_time_ns,
        cumulative_time_ns=decision.cumulative_time_ns,
        allocation_bytes=decision.allocation_bytes,
        time_share_ppm=decision.time_share_ppm,
        allocation_share_ppm=decision.allocation_share_ppm,
    )


def _profile_exclusion(
    relative_path: str,
    function_name: str,
) -> Literal["inclusive_wrapper", "security_invariant", "generated_wrapper"] | None:
    if function_name.startswith("<") or function_name in {"<lambda>", "<module>"}:
        return "generated_wrapper"
    if relative_path == "src/zotero_arxiv_daily/pipeline/daily.py":
        return "inclusive_wrapper"
    if (
        relative_path
        == "src/zotero_arxiv_daily/observability/collector.py"
        and function_name == "observe"
    ):
        return "inclusive_wrapper"
    if (
        relative_path == "src/zotero_arxiv_daily/pipeline/artifacts.py"
        and function_name == "audit"
    ):
        return "security_invariant"
    return None


class BenchmarkReport(StrictModel):
    report_version: Literal["stage9-benchmark-v1"] = REPORT_VERSION
    fixture_identity_hash: str
    code_identity_hash: str
    python_version: str
    os_family: Literal["windows", "linux", "macos", "other"]
    repetition_count: int = Field(ge=9)
    duration_observations_ns: tuple[int, ...]
    median_duration_ns: int = Field(ge=0)
    p95_duration_ns: int = Field(ge=0)
    q1_duration_ns: int = Field(ge=0)
    q3_duration_ns: int = Field(ge=0)
    peak_traced_allocation_bytes: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    selected_for_llm_count: int = Field(ge=0)
    selected_for_analysis_count: int = Field(ge=0)
    successful_analysis_call_count: int = Field(ge=0)
    analysis_attempt_count: int = Field(ge=0)
    configured_output_token_count: int = Field(ge=0)
    network_call_count: int = Field(ge=0)
    paid_call_count: int = Field(ge=0)
    viewer_published_count: int = Field(ge=0)
    viewer_artifact_hash: str
    selection_labels_matched: bool
    quality: QualityEvaluation
    budget_passed: bool

    @field_validator("fixture_identity_hash", "code_identity_hash", "viewer_artifact_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in _SHA256 for character in value):
            raise ValueError("identity must be a lowercase SHA-256 digest")
        return value

    @field_validator(
        "repetition_count",
        "duration_observations_ns",
        "median_duration_ns",
        "p95_duration_ns",
        "q1_duration_ns",
        "q3_duration_ns",
        "peak_traced_allocation_bytes",
        "candidate_count",
        "selected_for_llm_count",
        "selected_for_analysis_count",
        "successful_analysis_call_count",
        "analysis_attempt_count",
        "configured_output_token_count",
        "network_call_count",
        "paid_call_count",
        "viewer_published_count",
        mode="before",
    )
    @classmethod
    def reject_boolean_integer_fields(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("benchmark integer fields must not be booleans")
        if isinstance(value, (tuple, list)) and any(isinstance(item, bool) for item in value):
            raise ValueError("benchmark observations must not contain booleans")
        return value

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        values = self.duration_observations_ns
        if len(values) != self.repetition_count or values != tuple(sorted(values)):
            raise ValueError("duration observations must be sorted and complete")
        if self.median_duration_ns != percentile_nearest_rank(values, 50):
            raise ValueError("median duration differs from observations")
        if self.p95_duration_ns != percentile_nearest_rank(values, 95):
            raise ValueError("p95 duration differs from observations")
        if self.q1_duration_ns != percentile_nearest_rank(values, 25):
            raise ValueError("q1 duration differs from observations")
        if self.q3_duration_ns != percentile_nearest_rank(values, 75):
            raise ValueError("q3 duration differs from observations")
        expected = (
            self.candidate_count == 30
            and self.selected_for_llm_count == 15
            and self.selected_for_analysis_count == 5
            and self.successful_analysis_call_count <= 5
            and self.analysis_attempt_count <= 15
            and self.configured_output_token_count <= 40_960
            and self.network_call_count == 0
            and self.paid_call_count == 0
            and self.median_duration_ns <= 2_000_000_000
            and self.p95_duration_ns <= 3_000_000_000
            and self.peak_traced_allocation_bytes <= 256 * 1024 * 1024
            and self.quality.budget_passed
            and self.viewer_published_count == 5
            and self.selection_labels_matched
        )
        if self.budget_passed != expected:
            raise ValueError(f"benchmark budget_passed must be {expected}")
        return self

    def to_canonical_json(self) -> str:
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )


def percentile_nearest_rank(values: tuple[int, ...], percentile: int) -> int:
    if not values or not 0 < percentile <= 100:
        raise ValueError("percentile requires observations and a valid percentile")
    ordered = tuple(sorted(values))
    index = max(math.ceil(percentile * len(ordered) / 100) - 1, 0)
    return ordered[index]


class _FixtureEmbeddingProvider:
    identity = EmbeddingIdentity.from_settings(
        provider="stage9-fixture",
        implementation_version="1",
        model="integer-vector-v1",
        task="retrieval",
        settings={},
        dimension=2,
        dtype="float64",
    )

    def __init__(self, vectors: dict[str, tuple[float, float]]) -> None:
        self._vectors = vectors

    def encode(self, texts):
        return np.asarray([self._vectors[text] for text in texts], dtype=np.float64)


def _sha256_files(
    paths: tuple[Path, ...],
    *,
    relative_to: Path | None = None,
) -> str:
    digest = hashlib.sha256()
    root = Path(relative_to).resolve() if relative_to is not None else None

    def label(path: Path) -> str:
        if root is None:
            return path.name
        return path.resolve().relative_to(root).as_posix()

    ordered = sorted(paths, key=label)
    for path in ordered:
        digest.update(label(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _platform_family() -> str:
    name = platform.system().casefold()
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(
        name, "other"
    )


def _benchmark_code_paths(source_root: Path) -> tuple[Path, ...]:
    root = Path(source_root)
    if root.name != "zotero_arxiv_daily" or not root.is_dir():
        raise ValueError("benchmark source root is invalid")
    paths = tuple(
        sorted(
            (path for path in root.rglob("*.py") if "__pycache__" not in path.parts),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )
    if not paths:
        raise ValueError("benchmark source identity is empty")
    return paths


class _NoWriteCache:
    def read(self, identity):
        return None

    def write(self, identity, validated):
        return None


def _claim_payloads(value: object) -> tuple[dict[str, object], ...]:
    found: list[dict[str, object]] = []

    def visit(item: object) -> None:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="python")
        if isinstance(item, dict):
            if {"claim_id", "kind", "evidence_ids"}.issubset(item):
                found.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(found)


@dataclass(frozen=True)
class _PreparedFixture:
    now: datetime
    interest: InterestPaper
    papers: tuple[CandidatePaper, ...]
    vectors: dict[str, tuple[float, float]]
    synthetic_by_paper: dict[str, str]
    offline_by_paper: dict[str, tuple[object, object, object]]
    expected_ranked: tuple[str, ...]
    expected_analysis: tuple[str, ...]
    analysis_expectations: dict[str, dict[str, object]]
    fixture_identity_hash: str
    config_hash: str


def _prepare_fixture(fixture_root: Path, daily_fixture: Path) -> _PreparedFixture:
    candidates_payload = json.loads((fixture_root / "candidates.json").read_text("utf-8"))
    labels_payload = json.loads(
        (fixture_root / "quality-labels.json").read_text("utf-8")
    )
    analyses_payload = json.loads((fixture_root / "analyses.json").read_text("utf-8"))
    golden = json.loads(Path(daily_fixture).read_text(encoding="utf-8"))
    now = datetime(2026, 7, 26, tzinfo=UTC)
    interest = InterestPaper(
        paper_id="zotero:benchmark-memory-only",
        title="benchmark-interest",
        abstract="benchmark-interest",
        collection_paths=("PaperDaily/00-Seeds/Benchmark",),
        added_at=now,
    )
    papers: list[CandidatePaper] = []
    vectors = {"benchmark-interest\n\nbenchmark-interest": (1.0, 0.0)}
    synthetic_by_paper: dict[str, str] = {}
    offline_by_paper: dict[str, tuple[object, object, object]] = {}
    for index, record in enumerate(candidates_payload["records"], start=1):
        identifier = f"9901.{index:05d}"
        cloned = dict(golden)
        cloned.update(
            {
                "run_id": "stage9-benchmark",
                "paper_id": f"arxiv:{identifier}",
                "arxiv_id": identifier,
                "english_title": f"Synthetic Benchmark {index:02d}",
                "arxiv_url": f"https://arxiv.org/abs/{identifier}",
                "pdf_url": f"https://arxiv.org/pdf/{identifier}",
            }
        )
        paper, document, packet, analysis = _offline_inputs(cloned)
        papers.append(paper)
        vectors[f"{paper.title}\n\n{paper.abstract}"] = (
            float(record["feature_x"]),
            float(record["feature_y"]),
        )
        synthetic_by_paper[paper.paper_id] = record["synthetic_id"]
        offline_by_paper[paper.paper_id] = (document, packet, analysis)
    return _PreparedFixture(
        now=now,
        interest=interest,
        papers=tuple(papers),
        vectors=vectors,
        synthetic_by_paper=synthetic_by_paper,
        offline_by_paper=offline_by_paper,
        expected_ranked=tuple(labels_payload["ranked_ids"]),
        expected_analysis=tuple(labels_payload["analysis_ids"]),
        analysis_expectations={
            record["synthetic_id"]: record for record in analyses_payload["records"]
        },
        fixture_identity_hash=_sha256_files(
            (
                *tuple(path for path in fixture_root.iterdir() if path.is_file()),
                Path(daily_fixture),
            )
        ),
        config_hash=hashlib.sha256(Path(daily_fixture).read_bytes()).hexdigest(),
    )


def _fixture_execution(
    prepared: _PreparedFixture,
    run_root: Path,
    *,
    parallel_writes: bool = True,
) -> tuple[
    QualityEvaluation,
    object,
    RunMetrics,
    int,
    str,
    bool,
]:
    ranked = CandidateRanker(_FixtureEmbeddingProvider(prepared.vectors)).rank(
        prepared.papers,
        (prepared.interest,),
        RankingLimits(candidate_pool_size=30, llm_rerank_limit=15, full_analysis_limit=5),
    )
    actual_ranked = tuple(
        prepared.synthetic_by_paper[paper.paper_id] for paper in ranked.candidates[:15]
    )
    actual_analysis = tuple(
        prepared.synthetic_by_paper[paper_id]
        for paper_id in ranked.selected_for_full_analysis
    )
    selection_labels_matched = (
        actual_ranked == prepared.expected_ranked
        and actual_analysis == prepared.expected_analysis
    )
    relevant = set(prepared.expected_ranked)
    ranking_labels = tuple(
        RankedLabel(
            item_id=prepared.synthetic_by_paper[paper.paper_id],
            rank=index,
            relevant=prepared.synthetic_by_paper[paper.paper_id] in relevant,
        )
        for index, paper in enumerate(ranked.candidates, start=1)
    )

    candidate_batch = CandidateBatch(
        run_id="stage9-benchmark",
        created_at=prepared.now,
        retrieved_at=prepared.now,
        categories=("cs.AI",),
        config_hash=prepared.config_hash,
        interest_corpus_fingerprint=hashlib.sha256(b"stage9-interest").hexdigest(),
        candidates=ranked.candidates,
        rankings=ranked.rankings,
        selected_for_llm=ranked.selected_for_llm,
        selected_for_full_analysis=ranked.selected_for_full_analysis,
        counts=CandidateCounts(
            retrieved=len(prepared.papers),
            deduplicated=len(prepared.papers),
            invalid=0,
            excluded=0,
        ),
        limits=CandidateSelectionLimits(
            candidate_pool_size=30,
            llm_rerank_limit=15,
            full_analysis_limit=5,
        ),
    )
    selected = tuple(ranked.selected_for_full_analysis)
    document_batch = DocumentBatchResult(
        run_id=candidate_batch.run_id,
        created_at=prepared.now,
        results=tuple(
            PaperDocumentResult(
                paper_id=paper_id,
                status="success",
                document=prepared.offline_by_paper[paper_id][0],
                processing_seconds=0,
            )
            for paper_id in selected
        ),
    )
    packets = tuple(
        prepared.offline_by_paper[paper_id][1] for paper_id in selected
    )
    analysis_batch = AnalysisBatchResult(
        run_id=candidate_batch.run_id,
        created_at=prepared.now,
        results=tuple(
            prepared.offline_by_paper[paper_id][2] for paper_id in selected
        ),
        expensive_call_count=0,
        cache_hit_count=0,
    )
    run_root.mkdir(parents=True, exist_ok=True)
    viewer_root = run_root / "viewer"
    validation_holder: list[Any] = []

    def validation_runner(candidates, documents, output):
        validated = build_validation_batch(
            candidates,
            documents,
            output.packets,
            output.batch,
            ValidationSettings(),
            ValidationDependencies(
                cache=_NoWriteCache(),
                clock=lambda: prepared.now,
            ),
        )
        validation_holder.append(validated)
        return validated

    viewer = StaticViewerBuilder(
        ViewerSettings(
            output_root=viewer_root,
            evidence_roots=(),
            max_papers=5,
        ),
        parallel_writes=parallel_writes,
    )
    settings = DailySettings(
        run_id="stage9-benchmark",
        trigger="local",
        config_hash=prepared.config_hash,
        dry_run=True,
        send_feishu=False,
        viewer_output=viewer_root,
    )
    clock_values = iter(
        prepared.now + timedelta(seconds=index) for index in range(100)
    )
    metrics_session = MetricsSession(
        run_id=settings.run_id,
        trigger=settings.trigger,
        config_hash=settings.config_hash,
    )
    dependencies = DailyDependencies(
        candidate_runner=lambda: candidate_batch,
        document_runner=lambda _: document_batch,
        analysis_runner=lambda _candidates, _documents: AnalysisStageOutput(
            batch=analysis_batch,
            packets=packets,
        ),
        validation_runner=validation_runner,
        viewer_runner=lambda batch: viewer.build(
            batch.results,
            batch_label="stage9-benchmark",
        ),
        prepare_delivery=lambda batch: PreparedDelivery(
            idempotency_key=hashlib.sha256(b"stage9-benchmark-preview").hexdigest(),
            paper_count=len(batch.results),
        ),
        send_delivery=lambda _delivery: (_ for _ in ()).throw(
            AssertionError("benchmark send is forbidden")
        ),
        manifest_store=ManifestStore(run_root, run_root / "run-manifest.json"),
        artifact_auditor=ArtifactAuditor(run_root),
        delivery_ledger=DeliveryLedger(run_root / "delivery-ledger.json"),
        clock=lambda: next(clock_values),
        sleep=lambda _seconds: None,
        metrics_session=metrics_session,
        metrics_writer=MetricsWriter(run_root),
    )
    manifest = run_daily(settings, dependencies)
    if len(validation_holder) != 1:
        raise RuntimeError("benchmark validation path was not executed exactly once")
    validation = validation_holder[0]
    if len(validation.results) != 5:
        raise RuntimeError("benchmark validation count differs from selection")
    metrics_path = run_root / "run-metrics.json"
    metrics = RunMetrics.model_validate_json(metrics_path.read_bytes())
    if (
        manifest.metrics.status != "success"
        or manifest.metrics.sidecar_hash
        != hashlib.sha256(metrics_path.read_bytes()).hexdigest()
        or manifest.artifact_hash is None
    ):
        raise RuntimeError("benchmark metrics or manifest path is incomplete")

    evidence: list[EvidenceLabel] = []
    claims: list[ClaimLabel] = []
    fields: list[RequiredFieldLabel] = []
    analysis_by_id = {result.paper_id: result for result in analysis_batch.results}
    validation_by_id = {result.paper_id: result for result in validation.results}
    for paper_id in selected:
        synthetic_id = prepared.synthetic_by_paper[paper_id]
        expectation = prepared.analysis_expectations[synthetic_id]
        source = analysis_by_id[paper_id].analysis
        validated_result = validation_by_id[paper_id]
        validated = (
            validated_result.validated.analysis
            if validated_result.validated is not None
            else None
        )
        source_claims = _claim_payloads(source)
        validated_claims = {
            str(item["claim_id"]): item for item in _claim_payloads(validated)
        }
        expected_evidence_ids = tuple(
            sorted(
                {
                    str(evidence_id)
                    for claim in source_claims
                    for evidence_id in claim["evidence_ids"]
                }
            )
        )
        if len(expected_evidence_ids) != expectation["expected_evidence_count"]:
            raise RuntimeError("benchmark evidence expectation differs from Stage 4 input")
        if tuple(claim["kind"] for claim in source_claims) != tuple(
            expectation["expected_claim_kinds"]
        ):
            raise RuntimeError("benchmark claim expectation differs from Stage 4 input")
        accepted_evidence_ids = {
            str(evidence_id)
            for claim in validated_claims.values()
            for evidence_id in claim["evidence_ids"]
        }
        for index, evidence_id in enumerate(expected_evidence_ids):
            evidence.append(
                EvidenceLabel(
                    evidence_id=f"{synthetic_id}:e:{index}",
                    expected=True,
                    accepted=evidence_id in accepted_evidence_ids,
                )
            )
        for index, claim in enumerate(source_claims):
            accepted = str(claim["claim_id"]) in validated_claims
            claims.append(
                ClaimLabel(
                    claim_id=f"{synthetic_id}:c:{index}",
                    accepted=accepted,
                    supported=accepted and bool(
                        validated_claims[str(claim["claim_id"])]["evidence_ids"]
                    ),
                )
            )
        for field_name in expectation["required_fields"]:
            value = getattr(validated, field_name, None) if validated is not None else None
            fields.append(
                RequiredFieldLabel(
                    field_name=f"{synthetic_id}:{field_name}",
                    present=bool(value),
                )
            )
    quality = evaluate_quality(
        rankings=ranking_labels,
        evidence=tuple(evidence),
        claims=tuple(claims),
        required_fields=tuple(fields),
        fixture_identity_hash=prepared.fixture_identity_hash,
    )
    return (
        quality,
        ranked,
        metrics,
        manifest.counts.published_count,
        manifest.artifact_hash,
        selection_labels_matched,
    )


def _execution_equivalence_hash(execution: tuple[object, ...]) -> str:
    (
        quality,
        ranked,
        metrics,
        viewer_published_count,
        viewer_artifact_hash,
        selection_labels_matched,
    ) = execution
    metric_payload = {
        "artifact_hash": metrics.artifact_hash,
        "stages": [
            stage.model_dump(mode="json", exclude={"duration_ns"})
            for stage in metrics.stages
        ],
        "byte_count": metrics.byte_count,
        "cache_hit_count": metrics.cache_hit_count,
        "retry_count": metrics.retry_count,
        "partial_failure_count": metrics.partial_failure_count,
        "model_usage": metrics.model_usage.model_dump(mode="json"),
        "budget": metrics.budget.model_dump(
            mode="json",
            exclude={"peak_traced_allocation_bytes"},
        ),
    }
    payload = {
        "quality": quality.model_dump(mode="json"),
        "ranked": [paper.paper_id for paper in ranked.candidates],
        "selected_for_llm": list(ranked.selected_for_llm),
        "selected_for_analysis": list(ranked.selected_for_full_analysis),
        "metrics": metric_payload,
        "viewer_published_count": viewer_published_count,
        "viewer_artifact_hash": viewer_artifact_hash,
        "selection_labels_matched": selection_labels_matched,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def run_stage9_paired_comparison(
    *,
    fixture_root: Path,
    daily_fixture: Path,
    work_root: Path,
    pairs: int = 9,
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    variant_runner: Callable[[_PreparedFixture, Path, bool], str] | None = None,
    peak_sampler: Callable[[], int] = default_peak_memory_sampler,
) -> OptimizationComparison:
    if isinstance(pairs, bool) or not 9 <= pairs <= 99:
        raise ValueError("pairs must be between 9 and 99")
    tracing_was_active = tracemalloc.is_tracing()
    root = Path(work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        prepared = _prepare_fixture(Path(fixture_root), Path(daily_fixture))

        def default_variant(
            fixture: _PreparedFixture,
            output: Path,
            parallel_writes: bool,
        ) -> str:
            return _execution_equivalence_hash(
                _fixture_execution(
                    fixture,
                    output,
                    parallel_writes=parallel_writes,
                )
            )

        execute = variant_runner or default_variant
        baseline_warm = execute(prepared, root / "warmup-baseline", False)
        optimized_warm = execute(prepared, root / "warmup-optimized", True)
        if baseline_warm != optimized_warm:
            raise ValueError("optimization equivalence differs during warm-up")
    except BaseException:
        _restore_tracemalloc_state(tracing_was_active)
        raise

    baseline: list[int] = []
    optimized: list[int] = []
    baseline_peaks: list[int] = []
    optimized_peaks: list[int] = []
    identities = {baseline_warm}
    def measure(parallel: bool, output: Path) -> None:
        if tracemalloc.is_tracing():
            tracemalloc.reset_peak()
        else:
            tracemalloc.start()
        started = monotonic_ns()
        identity = execute(prepared, output, parallel)
        completed = monotonic_ns()
        peak = peak_sampler()
        if (
            isinstance(started, bool)
            or isinstance(completed, bool)
            or not isinstance(started, int)
            or not isinstance(completed, int)
            or completed < started
            or isinstance(peak, bool)
            or not isinstance(peak, int)
            or peak < 0
        ):
            raise ValueError("comparison clock or peak sample is invalid")
        identities.add(identity)
        duration = completed - started
        if parallel:
            optimized.append(duration)
            optimized_peaks.append(peak)
        else:
            baseline.append(duration)
            baseline_peaks.append(peak)

    try:
        for index in range(pairs):
            order = (False, True) if index % 2 == 0 else (True, False)
            for parallel in order:
                label = "optimized" if parallel else "baseline"
                measure(
                    parallel,
                    root / f"pair-{index + 1:02d}-{label}",
                )
        if len(identities) != 1:
            raise ValueError("optimization equivalence differs")
        return build_optimization_comparison(
            baseline_observations_ns=tuple(baseline),
            optimized_observations_ns=tuple(optimized),
            baseline_peak_bytes=max(baseline_peaks),
            optimized_peak_bytes=max(optimized_peaks),
            equivalence_hash=identities.pop(),
        )
    finally:
        _restore_tracemalloc_state(tracing_was_active)


def _restore_tracemalloc_state(was_active: bool) -> None:
    is_active = tracemalloc.is_tracing()
    if was_active and not is_active:
        tracemalloc.start()
    elif not was_active and is_active:
        tracemalloc.stop()


def run_stage9_benchmark(
    *,
    fixture_root: Path,
    daily_fixture: Path,
    work_root: Path,
    repetitions: int = 9,
    steady_state_profiler: object | None = None,
    boundary_counts: BenchmarkBoundaryCounts | None = None,
) -> BenchmarkReport:
    if isinstance(repetitions, bool) or repetitions < 9 or repetitions > 99:
        raise ValueError("repetitions must be between 9 and 99")
    fixture_root = Path(fixture_root)
    daily_fixture = Path(daily_fixture)
    work_root = Path(work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    observed_boundaries = boundary_counts or BenchmarkBoundaryCounts()
    if (
        isinstance(observed_boundaries.network_call_count, bool)
        or isinstance(observed_boundaries.paid_call_count, bool)
        or observed_boundaries.network_call_count < 0
        or observed_boundaries.paid_call_count < 0
    ):
        raise ValueError("benchmark boundary counts are invalid")

    tracing_was_active = tracemalloc.is_tracing()
    observations: list[int] = []
    last_execution = None
    profiler_enabled = False
    try:
        prepared = _prepare_fixture(fixture_root, daily_fixture)
        _fixture_execution(prepared, work_root / "warmup")
        if not tracemalloc.is_tracing():
            tracemalloc.start()
        tracemalloc.reset_peak()
        if steady_state_profiler is not None:
            getattr(steady_state_profiler, "enable")()
            profiler_enabled = True
        for index in range(1, repetitions + 1):
            started = time.perf_counter_ns()
            last_execution = _fixture_execution(
                prepared,
                work_root / f"run-{index:02d}",
            )
            observations.append(time.perf_counter_ns() - started)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        try:
            if profiler_enabled:
                getattr(steady_state_profiler, "disable")()
        finally:
            _restore_tracemalloc_state(tracing_was_active)
    if last_execution is None:
        raise RuntimeError("benchmark viewer artifact is unavailable")
    (
        quality,
        ranked,
        run_metrics,
        viewer_published_count,
        viewer_artifact_hash,
        selection_labels_matched,
    ) = last_execution
    ordered = tuple(sorted(observations))
    source_root = Path(__file__).parents[1]
    code_hash = _sha256_files(
        _benchmark_code_paths(source_root),
        relative_to=source_root,
    )
    successful_calls = run_metrics.model_usage.successful_call_count
    analysis_attempts = run_metrics.model_usage.attempt_count
    configured_output_tokens = (
        run_metrics.model_usage.configured_output_token_count
    )
    budget_passed = (
        len(ordered) >= 9
        and len(ranked.candidates) == 30
        and len(ranked.selected_for_llm) == 15
        and len(ranked.selected_for_full_analysis) == 5
        and successful_calls <= 5
        and analysis_attempts <= 15
        and configured_output_tokens <= 40_960
        and run_metrics.budget.offline_network_call_count == 0
        and run_metrics.budget.offline_paid_call_count == 0
        and observed_boundaries.network_call_count == 0
        and observed_boundaries.paid_call_count == 0
        and percentile_nearest_rank(ordered, 50) <= 2_000_000_000
        and percentile_nearest_rank(ordered, 95) <= 3_000_000_000
        and peak <= 256 * 1024 * 1024
        and quality.budget_passed
        and selection_labels_matched
    )
    return BenchmarkReport(
        fixture_identity_hash=quality.fixture_identity_hash,
        code_identity_hash=code_hash,
        python_version=f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
        os_family=_platform_family(),
        repetition_count=repetitions,
        duration_observations_ns=ordered,
        median_duration_ns=percentile_nearest_rank(ordered, 50),
        p95_duration_ns=percentile_nearest_rank(ordered, 95),
        q1_duration_ns=percentile_nearest_rank(ordered, 25),
        q3_duration_ns=percentile_nearest_rank(ordered, 75),
        peak_traced_allocation_bytes=peak,
        candidate_count=len(ranked.candidates),
        selected_for_llm_count=len(ranked.selected_for_llm),
        selected_for_analysis_count=len(ranked.selected_for_full_analysis),
        successful_analysis_call_count=successful_calls,
        analysis_attempt_count=analysis_attempts,
        configured_output_token_count=configured_output_tokens,
        network_call_count=observed_boundaries.network_call_count,
        paid_call_count=observed_boundaries.paid_call_count,
        viewer_published_count=viewer_published_count,
        viewer_artifact_hash=viewer_artifact_hash,
        selection_labels_matched=selection_labels_matched,
        quality=quality,
        budget_passed=budget_passed,
    )
