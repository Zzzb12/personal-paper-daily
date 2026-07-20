from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import BoundingBox, SourceMapping
from zotero_arxiv_daily.analysis.schemas import StrictModel, validate_run_id_value


ANALYSIS_SCHEMA_VERSION = "1.0"
EVIDENCE_PACKET_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ClaimSource = Literal["author_statement", "system_summary", "system_inference"]
ClaimKind = Literal[
    "title",
    "recommendation",
    "problem",
    "insight",
    "insight_logic",
    "method",
    "difference",
    "parameter",
    "ablation",
    "result",
    "limitation",
    "support",
]


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("value must not be blank")
    return normalized


def _optional_text(value: str | None) -> str | None:
    return _non_empty(value) if value is not None else None


def _sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


class AnalysisIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    evidence_id: str | None = None

    @field_validator("code", "message")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)


class EvidenceRegion(StrictModel):
    pdf_page: int = Field(ge=1)
    bbox: BoundingBox
    image_path: Path | None
    source_mapping: SourceMapping
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_source_mapping(self) -> Self:
        if (
            self.source_mapping.pdf_page != self.pdf_page
            or self.source_mapping.bbox != self.bbox
        ):
            raise ValueError("evidence region must preserve its source page and bbox")
        return self


class EvidenceCandidate(StrictModel):
    evidence_id: str
    paper_id: str
    kind: Literal["text", "figure", "table"]
    pdf_page: int = Field(ge=1)
    section_id: str | None
    section_title: str | None
    section_path: tuple[str, ...]
    block_ids: tuple[str, ...]
    evidence_text: str | None
    visual_id: str | None
    label: str | None
    caption: str | None
    regions: tuple[EvidenceRegion, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    abstract_only: bool = False

    @field_validator("evidence_id", "paper_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("section_id", "section_title", "evidence_text", "visual_id", "label", "caption")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("section_path", "block_ids")
    @classmethod
    def normalize_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_non_empty(item) for item in value)

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if self.regions[0].pdf_page != self.pdf_page:
            raise ValueError("candidate page must match its first evidence region")
        if self.kind == "text":
            if self.evidence_text is None:
                raise ValueError("text evidence requires evidence_text")
            if self.visual_id is not None or self.label is not None or self.caption is not None:
                raise ValueError("text evidence must not contain visual identity")
        elif self.visual_id is None:
            raise ValueError("visual evidence requires visual_id")
        return self

    @property
    def image_paths(self) -> tuple[Path, ...]:
        return tuple(region.image_path for region in self.regions if region.image_path is not None)


class EvidencePacket(StrictModel):
    schema_version: Literal["1.0"] = EVIDENCE_PACKET_VERSION
    paper_id: str
    document_fingerprint: str
    builder_version: str
    candidates: tuple[EvidenceCandidate, ...]
    packet_fingerprint: str

    @field_validator("paper_id", "builder_version")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("document_fingerprint", "packet_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return _sha256(value)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        ids = tuple(candidate.evidence_id for candidate in self.candidates)
        if len(ids) != len(set(ids)):
            raise ValueError("evidence candidate IDs must be unique")
        if any(candidate.paper_id != self.paper_id for candidate in self.candidates):
            raise ValueError("evidence candidate paper_id must match the packet")
        return self


class ClaimRecord(StrictModel):
    claim_id: str
    kind: ClaimKind
    text_zh: str
    source_type: ClaimSource
    inferred: bool
    evidence_ids: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)

    @field_validator("claim_id", "text_zh")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_non_empty(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("claim evidence IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_inference(self) -> Self:
        expected = self.source_type == "system_inference"
        if self.inferred != expected:
            raise ValueError(
                "system_inference requires inferred=true and other sources require inferred=false"
            )
        return self


class DraftSupportingVisual(StrictModel):
    evidence_id: str
    insight_ids: tuple[str, ...]
    support_explanation: ClaimRecord

    @field_validator("evidence_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if not self.insight_ids:
            raise ValueError("supporting visual requires at least one insight ID")
        if self.support_explanation.kind != "support":
            raise ValueError("support explanation must use kind=support")
        if self.evidence_id not in self.support_explanation.evidence_ids:
            raise ValueError("support explanation must cite the visual evidence ID")
        return self


class MethodModule(StrictModel):
    name: str
    purpose: ClaimRecord

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_purpose(self) -> Self:
        if self.purpose.kind != "method":
            raise ValueError("method module purpose must use kind=method")
        return self


class ParameterRecord(StrictModel):
    name: str
    symbol: str | None
    role: ClaimRecord
    final_value: str | None
    selection_method: Literal["fixed", "empirical", "search", "unclear"] | None
    per_model_tuning: bool | None
    evidence_ids: tuple[str, ...]
    ablation_ids: tuple[str, ...]
    source_type: ClaimSource
    inferred: bool
    confidence: float = Field(ge=0, le=1)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("symbol", "final_value")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("evidence_ids", "ablation_ids")
    @classmethod
    def normalize_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_non_empty(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("parameter references must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_parameter(self) -> Self:
        expected = self.source_type == "system_inference"
        if self.inferred != expected:
            raise ValueError(
                "system_inference requires inferred=true and other sources require inferred=false"
            )
        if self.role.kind != "parameter":
            raise ValueError("parameter role must use kind=parameter")
        return self


class AblationRecord(StrictModel):
    ablation_id: str
    parameter_names: tuple[str, ...]
    conclusion: ClaimRecord
    visual_evidence_ids: tuple[str, ...]

    @field_validator("ablation_id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_ablation(self) -> Self:
        if self.conclusion.kind != "ablation":
            raise ValueError("ablation conclusion must use kind=ablation")
        if not self.visual_evidence_ids:
            raise ValueError("ablation requires visual evidence IDs")
        return self


class PaperLinks(StrictModel):
    pdf_url: str
    arxiv_url: str
    code_url: str | None

    @field_validator("pdf_url", "arxiv_url")
    @classmethod
    def normalize_required_url(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("code_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        return _optional_text(value)


class PaperAnalysisDraft(StrictModel):
    schema_version: Literal["1.0"] = ANALYSIS_SCHEMA_VERSION
    paper_id: str
    english_title: str
    chinese_title: ClaimRecord | None
    recommendation_reason: ClaimRecord | None
    research_problem: ClaimRecord | None
    insights: tuple[ClaimRecord, ...]
    supporting_visuals: tuple[DraftSupportingVisual, ...] = Field(max_length=3)
    insight_formation_logic: ClaimRecord | None
    method_overview: ClaimRecord | None
    method_modules: tuple[MethodModule, ...]
    differences_from_prior_work: ClaimRecord | None
    parameters: tuple[ParameterRecord, ...]
    ablations: tuple[AblationRecord, ...]
    experimental_conclusions: tuple[ClaimRecord, ...]
    limitations: tuple[ClaimRecord, ...]
    links: PaperLinks

    @field_validator("paper_id", "english_title")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)


class SupportingVisual(StrictModel):
    evidence_id: str
    insight_ids: tuple[str, ...]
    support_explanation: ClaimRecord
    visual_id: str
    kind: Literal["figure", "table"]
    label: str | None
    caption: str | None
    pdf_page: int = Field(ge=1)
    section_id: str | None
    section_title: str | None
    section_path: tuple[str, ...]
    regions: tuple[EvidenceRegion, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_id", "visual_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if self.regions[0].pdf_page != self.pdf_page:
            raise ValueError("visual page must match its first region")
        if self.evidence_id not in self.support_explanation.evidence_ids:
            raise ValueError("support explanation must cite the visual evidence ID")
        return self

    @property
    def image_paths(self) -> tuple[Path, ...]:
        return tuple(region.image_path for region in self.regions if region.image_path is not None)


class GenerationMetadata(StrictModel):
    model_identity: str
    prompt_version: str
    schema_version: str
    cache_key: str
    generated_at: datetime

    @field_validator("model_identity", "prompt_version", "schema_version")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("cache_key")
    @classmethod
    def validate_cache_key(cls, value: str) -> str:
        return _sha256(value)

    @field_validator("generated_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        return _aware(value)


class PaperAnalysis(StrictModel):
    schema_version: Literal["1.0"] = ANALYSIS_SCHEMA_VERSION
    paper_id: str
    english_title: str
    chinese_title: ClaimRecord | None
    recommendation_reason: ClaimRecord | None
    research_problem: ClaimRecord | None
    insights: tuple[ClaimRecord, ...]
    supporting_visuals: tuple[SupportingVisual, ...] = Field(max_length=3)
    insight_formation_logic: ClaimRecord | None
    method_overview: ClaimRecord | None
    method_modules: tuple[MethodModule, ...]
    differences_from_prior_work: ClaimRecord | None
    parameters: tuple[ParameterRecord, ...]
    ablations: tuple[AblationRecord, ...]
    experimental_conclusions: tuple[ClaimRecord, ...]
    limitations: tuple[ClaimRecord, ...]
    links: PaperLinks
    evidence_candidates: tuple[EvidenceCandidate, ...]
    generation: GenerationMetadata

    @field_validator("paper_id", "english_title")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        return _non_empty(value)


class PaperAnalysisResult(StrictModel):
    paper_id: str
    status: Literal["success", "partial", "failed", "skipped"]
    analysis: PaperAnalysis | None
    issues: tuple[AnalysisIssue, ...] = ()
    processing_seconds: float = Field(ge=0)
    cache_hit: bool = False

    @field_validator("paper_id")
    @classmethod
    def normalize_paper_id(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status in {"success", "partial"} and self.analysis is None:
            raise ValueError("successful or partial results require an analysis")
        if self.status in {"failed", "skipped"} and self.analysis is not None:
            raise ValueError("failed or skipped results must not contain an analysis")
        if self.analysis is not None and self.analysis.paper_id != self.paper_id:
            raise ValueError("analysis paper_id must match result paper_id")
        return self

    @classmethod
    def success(
        cls, analysis: PaperAnalysis, *, processing_seconds: float, cache_hit: bool
    ) -> PaperAnalysisResult:
        return cls(
            paper_id=analysis.paper_id,
            status="success",
            analysis=analysis,
            processing_seconds=processing_seconds,
            cache_hit=cache_hit,
        )


class AnalysisBatchResult(StrictModel):
    schema_version: Literal["1.0"] = ANALYSIS_SCHEMA_VERSION
    run_id: str
    created_at: datetime
    results: tuple[PaperAnalysisResult, ...]
    expensive_call_count: int = Field(ge=0)
    cache_hit_count: int = Field(ge=0)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        return validate_run_id_value(value)

    @field_validator("created_at")
    @classmethod
    def require_aware_datetime(cls, value: datetime) -> datetime:
        return _aware(value)

    @model_validator(mode="after")
    def validate_batch(self) -> Self:
        if len(self.results) > 5:
            raise ValueError("analysis batch may contain at most five paper results")
        ids = tuple(result.paper_id for result in self.results)
        if len(ids) != len(set(ids)):
            raise ValueError("analysis batch paper IDs must be unique")
        if self.cache_hit_count > len(self.results):
            raise ValueError("cache hit count cannot exceed result count")
        if self.expensive_call_count > len(self.results):
            raise ValueError("expensive call count cannot exceed result count")
        return self
