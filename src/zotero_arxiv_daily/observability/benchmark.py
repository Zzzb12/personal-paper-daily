from __future__ import annotations

import hashlib
import json
import math
import platform
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

import numpy as np
from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.schemas import CandidatePaper, InterestPaper, StrictModel
from zotero_arxiv_daily.candidates.ranking import (
    CandidateRanker,
    EmbeddingIdentity,
    RankingLimits,
)
from zotero_arxiv_daily.observability.quality import (
    ClaimLabel,
    EvidenceLabel,
    QualityEvaluation,
    RankedLabel,
    RequiredFieldLabel,
    evaluate_quality,
)
from zotero_arxiv_daily.pipeline.daily import (
    DailyFactoryContext,
    DailySettings,
    build_offline_daily_dependencies,
    run_daily,
)


REPORT_VERSION = "stage9-benchmark-v1"
_SHA256 = "0123456789abcdef"


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
            and self.viewer_published_count == 1
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


def _sha256_files(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _platform_family() -> str:
    name = platform.system().casefold()
    return {"windows": "windows", "linux": "linux", "darwin": "macos"}.get(
        name, "other"
    )


def _ranking_and_quality(fixture_root: Path) -> QualityEvaluation:
    candidates_payload = json.loads((fixture_root / "candidates.json").read_text("utf-8"))
    analyses_payload = json.loads((fixture_root / "analyses.json").read_text("utf-8"))
    records = candidates_payload["records"]
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
    relevance: dict[str, bool] = {}
    for index, record in enumerate(records, start=1):
        identifier = f"9901.{index:05d}"
        paper_id = f"arxiv:{identifier}"
        title = f"benchmark-{index:02d}"
        abstract = f"feature-{record['feature_x']}-{record['feature_y']}"
        paper = CandidatePaper(
            paper_id=paper_id,
            arxiv_id=identifier,
            version=1,
            title=title,
            authors=("benchmark",),
            abstract=abstract,
            categories=("cs.AI",),
            primary_category="cs.AI",
            published_at=now,
            updated_at=now,
            arxiv_url=f"https://arxiv.org/abs/{identifier}",
            pdf_url=f"https://arxiv.org/pdf/{identifier}",
        )
        papers.append(paper)
        vectors[f"{title}\n\n{abstract}"] = (
            float(record["feature_x"]),
            float(record["feature_y"]),
        )
        synthetic_by_paper[paper_id] = record["synthetic_id"]
        relevance[record["synthetic_id"]] = record["relevant"]
    ranked = CandidateRanker(_FixtureEmbeddingProvider(vectors)).rank(
        tuple(papers),
        (interest,),
        RankingLimits(candidate_pool_size=30, llm_rerank_limit=15, full_analysis_limit=5),
    )
    ranking_labels = tuple(
        RankedLabel(
            item_id=synthetic_by_paper[paper.paper_id],
            rank=index,
            relevant=relevance[synthetic_by_paper[paper.paper_id]],
        )
        for index, paper in enumerate(ranked.candidates, start=1)
    )
    evidence: list[EvidenceLabel] = []
    claims: list[ClaimLabel] = []
    fields: list[RequiredFieldLabel] = []
    for paper_index, record in enumerate(analyses_payload["records"], start=1):
        for index in range(record["expected_evidence_count"]):
            evidence.append(
                EvidenceLabel(
                    evidence_id=f"e-{paper_index}-{index}",
                    expected=True,
                    accepted=index < record["accepted_evidence_count"],
                )
            )
        for index in range(record["accepted_claim_count"]):
            claims.append(
                ClaimLabel(
                    claim_id=f"c-{paper_index}-{index}",
                    accepted=True,
                    supported=index < record["supported_claim_count"],
                )
            )
        for index in range(record["required_field_count"]):
            fields.append(
                RequiredFieldLabel(
                    field_name=f"f-{paper_index}-{index}",
                    present=index < record["present_required_field_count"],
                )
            )
    return evaluate_quality(
        rankings=ranking_labels,
        evidence=tuple(evidence),
        claims=tuple(claims),
        required_fields=tuple(fields),
        fixture_identity_hash=_sha256_files(
            tuple(path for path in fixture_root.iterdir() if path.is_file())
        ),
    )


def _run_daily_fixture(daily_fixture: Path, run_root: Path, run_number: int):
    run_root.mkdir(parents=True, exist_ok=True)
    settings = DailySettings(
        run_id=f"stage9-benchmark-{run_number:02d}",
        trigger="local",
        config_hash=hashlib.sha256(daily_fixture.read_bytes()).hexdigest(),
        dry_run=True,
        send_feishu=False,
        viewer_output=run_root / "viewer",
    )
    context = DailyFactoryContext(
        settings=settings,
        config_dir=daily_fixture.parents[3] / "config",
        run_root=run_root,
        manifest_output=run_root / "run-manifest.json",
        offline_fixture=daily_fixture,
        environment={},
    )
    dependencies = build_offline_daily_dependencies(context)
    try:
        return run_daily(settings, dependencies)
    finally:
        dependencies.close()


def run_stage9_benchmark(
    *,
    fixture_root: Path,
    daily_fixture: Path,
    work_root: Path,
    repetitions: int = 9,
) -> BenchmarkReport:
    if isinstance(repetitions, bool) or repetitions < 9 or repetitions > 99:
        raise ValueError("repetitions must be between 9 and 99")
    fixture_root = Path(fixture_root)
    daily_fixture = Path(daily_fixture)
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    _ranking_and_quality(fixture_root)
    _run_daily_fixture(daily_fixture, work_root / "warmup", 0)
    observations: list[int] = []
    last_manifest = None
    tracemalloc.start()
    tracemalloc.reset_peak()
    for index in range(1, repetitions + 1):
        started = time.perf_counter_ns()
        quality = _ranking_and_quality(fixture_root)
        last_manifest = _run_daily_fixture(
            daily_fixture, work_root / f"run-{index:02d}", index
        )
        observations.append(time.perf_counter_ns() - started)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    if last_manifest is None or last_manifest.artifact_hash is None:
        raise RuntimeError("benchmark viewer artifact is unavailable")
    ordered = tuple(sorted(observations))
    source_root = Path(__file__).parents[1]
    code_hash = _sha256_files(
        (
            source_root / "candidates" / "ranking.py",
            source_root / "observability" / "quality.py",
            source_root / "pipeline" / "daily.py",
        )
    )
    budget_passed = (
        len(ordered) >= 9
        and percentile_nearest_rank(ordered, 50) <= 2_000_000_000
        and percentile_nearest_rank(ordered, 95) <= 3_000_000_000
        and peak <= 256 * 1024 * 1024
        and quality.budget_passed
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
        candidate_count=30,
        selected_for_llm_count=15,
        selected_for_analysis_count=5,
        successful_analysis_call_count=0,
        analysis_attempt_count=0,
        configured_output_token_count=0,
        network_call_count=0,
        paid_call_count=0,
        viewer_published_count=last_manifest.counts.published_count,
        viewer_artifact_hash=last_manifest.artifact_hash,
        quality=quality,
        budget_passed=budget_passed,
    )

