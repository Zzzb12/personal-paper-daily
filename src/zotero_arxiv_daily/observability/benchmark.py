from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import time
import tracemalloc
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Self

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
from zotero_arxiv_daily.observability.quality import (
    ClaimLabel,
    EvidenceLabel,
    QualityEvaluation,
    RankedLabel,
    RequiredFieldLabel,
    evaluate_quality,
)
from zotero_arxiv_daily.pipeline.artifacts import ArtifactAuditor
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
    profile_version: Literal["stage9-profile-v2"] = "stage9-profile-v2"
    total_time_ns: int = Field(gt=0)
    project_row_count: int = Field(ge=1)
    excluded_count: int = Field(ge=0)
    eligible_count: int = Field(ge=1)
    symbol: str
    relative_path: str
    self_time_ns: int = Field(ge=0)
    cumulative_time_ns: int = Field(ge=0)
    allocation_bytes: int = Field(ge=0)
    time_share_ppm: int = Field(ge=200_000, le=1_000_000)
    allocation_share_ppm: int = Field(ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        ProfileRow(
            symbol=self.symbol,
            relative_path=self.relative_path,
            self_time_ns=self.self_time_ns,
            cumulative_time_ns=self.cumulative_time_ns,
            allocation_bytes=self.allocation_bytes,
        )
        if self.excluded_count >= self.project_row_count:
            raise ValueError("profile must retain eligible project rows")
        if self.cumulative_time_ns > self.total_time_ns:
            raise ValueError("profile target exceeds total time")
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
    decision = select_optimization_target(
        rows,
        total_time_ns=total_time_ns,
        total_allocation_bytes=0,
    )
    selected = next(row for row in rows if row.symbol == decision.symbol)
    return ProfileReport(
        total_time_ns=total_time_ns,
        project_row_count=len(rows),
        excluded_count=sum(not row.eligible for row in rows),
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
            tuple(path for path in fixture_root.iterdir() if path.is_file())
        ),
        config_hash=hashlib.sha256(Path(daily_fixture).read_bytes()).hexdigest(),
    )


def _fixture_execution(
    prepared: _PreparedFixture,
    run_root: Path,
) -> tuple[
    QualityEvaluation,
    object,
    int,
    int,
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
    validation = build_validation_batch(
        candidate_batch,
        document_batch,
        packets,
        analysis_batch,
        ValidationSettings(),
        ValidationDependencies(cache=_NoWriteCache(), clock=lambda: prepared.now),
    )
    if len(validation.results) != 5:
        raise RuntimeError("benchmark validation count differs from selection")

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
    run_root.mkdir(parents=True, exist_ok=True)
    viewer_root = run_root / "viewer"
    build = StaticViewerBuilder(
        ViewerSettings(
            output_root=viewer_root,
            evidence_roots=(),
            max_papers=5,
        )
    ).build(
        validation.results,
        batch_label="stage9-benchmark",
    )
    audit = ArtifactAuditor(run_root).audit(viewer_root)
    if build != audit.build_manifest:
        raise RuntimeError("benchmark viewer audit differs from build")
    return (
        quality,
        ranked,
        analysis_batch.expensive_call_count,
        0,
        build.published_count,
        audit.artifact_hash,
        selection_labels_matched,
    )


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

    prepared = _prepare_fixture(fixture_root, daily_fixture)
    _fixture_execution(prepared, work_root / "warmup")
    observations: list[int] = []
    last_execution = None
    tracemalloc.start()
    tracemalloc.reset_peak()
    if steady_state_profiler is not None:
        getattr(steady_state_profiler, "enable")()
    try:
        for index in range(1, repetitions + 1):
            started = time.perf_counter_ns()
            last_execution = _fixture_execution(
                prepared,
                work_root / f"run-{index:02d}",
            )
            observations.append(time.perf_counter_ns() - started)
    finally:
        if steady_state_profiler is not None:
            getattr(steady_state_profiler, "disable")()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    if last_execution is None:
        raise RuntimeError("benchmark viewer artifact is unavailable")
    (
        quality,
        ranked,
        successful_calls,
        configured_output_tokens,
        viewer_published_count,
        viewer_artifact_hash,
        selection_labels_matched,
    ) = last_execution
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
        and len(ranked.candidates) == 30
        and len(ranked.selected_for_llm) == 15
        and len(ranked.selected_for_full_analysis) == 5
        and successful_calls <= 5
        and configured_output_tokens <= 40_960
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
        analysis_attempt_count=successful_calls,
        configured_output_token_count=configured_output_tokens,
        network_call_count=observed_boundaries.network_call_count,
        paid_call_count=observed_boundaries.paid_call_count,
        viewer_published_count=viewer_published_count,
        viewer_artifact_hash=viewer_artifact_hash,
        selection_labels_matched=selection_labels_matched,
        quality=quality,
        budget_passed=budget_passed,
    )
