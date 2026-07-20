from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from zotero_arxiv_daily.analysis.cache import (
    AnalysisCache,
    build_analysis_cache_identity,
)
from zotero_arxiv_daily.analysis.client import (
    AnalysisClientError,
    StructuredAnalysisClient,
)
from zotero_arxiv_daily.analysis.document_schemas import DocumentGraph
from zotero_arxiv_daily.analysis.paper_schemas import (
    ANALYSIS_SCHEMA_VERSION,
    AnalysisIssue,
    ClaimRecord,
    EvidenceCandidate,
    EvidencePacket,
    GenerationMetadata,
    PaperAnalysis,
    PaperAnalysisDraft,
    PaperAnalysisResult,
    PaperLinks,
    SupportingVisual,
)
from zotero_arxiv_daily.analysis.prompts.stage3_v1 import (
    PROMPT_VERSION,
    build_analysis_request,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper, StrictModel
from zotero_arxiv_daily.documents.evidence import (
    EvidenceBuildSettings,
    build_evidence_packet,
)


class AnalysisSettings(StrictModel):
    evidence: EvidenceBuildSettings = EvidenceBuildSettings()
    prompt_version: Literal["stage3-v1"] = PROMPT_VERSION
    schema_version: Literal["1.0"] = ANALYSIS_SCHEMA_VERSION
    config_version: str = "1"
    max_output_tokens: int = Field(default=8_192, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff_seconds: float = Field(default=1, ge=0, le=60)
    max_retry_after_seconds: float = Field(default=60, ge=0, le=300)

    @property
    def generation_identity(self) -> str:
        payload = json.dumps(
            {
                "config_version": self.config_version,
                "max_output_tokens": self.max_output_tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass
class AnalysisDependencies:
    client: StructuredAnalysisClient
    cache: AnalysisCache
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]


class _AnalysisProtocolError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def analyze_paper(
    paper: CandidatePaper,
    document: DocumentGraph,
    settings: AnalysisSettings,
    dependencies: AnalysisDependencies,
) -> PaperAnalysisResult:
    started = time.perf_counter()
    try:
        packet = build_evidence_packet(paper.paper_id, document, settings.evidence)
        if not packet.candidates:
            raise _AnalysisProtocolError("evidence_packet_empty")
        if not any(not candidate.abstract_only for candidate in packet.candidates):
            raise _AnalysisProtocolError("evidence_packet_non_abstract_empty")
        identity = build_analysis_cache_identity(
            paper,
            document,
            packet,
            prompt_version=settings.prompt_version,
            analysis_schema_version=settings.schema_version,
            model_identity=dependencies.client.model_identity,
            generation_identity=settings.generation_identity,
        )
        cached = dependencies.cache.read(identity)
        if cached is not None:
            return _analysis_result(
                cached,
                processing_seconds=time.perf_counter() - started,
                cache_hit=True,
            )

        request = build_analysis_request(
            paper, packet, max_output_tokens=settings.max_output_tokens
        )
        raw = _generate_with_bounded_retry(request, settings, dependencies)
        draft = _parse_draft(raw)
        analysis = _materialize_and_check(
            paper,
            packet,
            draft,
            model_identity=dependencies.client.model_identity,
            prompt_version=settings.prompt_version,
            cache_key=identity.cache_key,
            generated_at=dependencies.clock(),
        )
        dependencies.cache.write(identity, analysis)
        return _analysis_result(
            analysis,
            processing_seconds=time.perf_counter() - started,
            cache_hit=False,
        )
    except AnalysisClientError as exc:
        return _failed_result(paper.paper_id, exc.code, started)
    except _AnalysisProtocolError as exc:
        return _failed_result(paper.paper_id, exc.code, started)
    except Exception:
        return _failed_result(paper.paper_id, "analysis_failed", started)


def _generate_with_bounded_retry(
    request,
    settings: AnalysisSettings,
    dependencies: AnalysisDependencies,
) -> str:
    for attempt in range(1, settings.max_attempts + 1):
        try:
            return dependencies.client.generate(request)
        except AnalysisClientError as exc:
            if not exc.retryable:
                raise
            if attempt == settings.max_attempts:
                raise AnalysisClientError(
                    "analysis_retry_exhausted", retryable=False
                ) from None
            proposed = (
                exc.retry_after_seconds
                if exc.retry_after_seconds is not None
                else settings.backoff_seconds * (2 ** (attempt - 1))
            )
            delay = min(max(proposed, 0), settings.max_retry_after_seconds)
            dependencies.sleep(delay)
    raise AnalysisClientError("analysis_retry_exhausted", retryable=False)


def _parse_draft(raw: str) -> PaperAnalysisDraft:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise _AnalysisProtocolError("analysis_malformed_json") from None
    try:
        return PaperAnalysisDraft.model_validate(payload)
    except ValidationError:
        raise _AnalysisProtocolError("analysis_schema_invalid") from None


def _materialize_and_check(
    paper: CandidatePaper,
    packet: EvidencePacket,
    draft: PaperAnalysisDraft,
    *,
    model_identity: str,
    prompt_version: str,
    cache_key: str,
    generated_at: datetime,
) -> PaperAnalysis:
    expected_links = PaperLinks(
        pdf_url=paper.pdf_url,
        arxiv_url=paper.arxiv_url,
        code_url=paper.code_url,
    )
    if (
        draft.paper_id != paper.paper_id
        or draft.english_title != paper.title
        or draft.links != expected_links
    ):
        raise _AnalysisProtocolError("analysis_metadata_mismatch")

    candidates = {item.evidence_id: item for item in packet.candidates}
    referenced_ids = _all_referenced_evidence_ids(draft)
    if not referenced_ids.issubset(candidates):
        raise _AnalysisProtocolError("analysis_unknown_evidence")

    claim_ids = tuple(claim.claim_id for claim in _all_claims(draft))
    if len(claim_ids) != len(set(claim_ids)):
        raise _AnalysisProtocolError("analysis_duplicate_claim_id")

    insight_ids = {insight.claim_id for insight in draft.insights}
    for insight in draft.insights:
        if not insight.evidence_ids or not any(
            not candidates[evidence_id].abstract_only
            for evidence_id in insight.evidence_ids
        ):
            raise _AnalysisProtocolError("analysis_abstract_only_insight")

    materialized_visuals: list[SupportingVisual] = []
    for visual in draft.supporting_visuals:
        candidate = candidates.get(visual.evidence_id)
        if candidate is None:
            raise _AnalysisProtocolError("analysis_unknown_evidence")
        if candidate.kind == "text" or candidate.visual_id is None:
            raise _AnalysisProtocolError("analysis_visual_provenance_mismatch")
        if not set(visual.insight_ids).issubset(insight_ids):
            raise _AnalysisProtocolError("analysis_unknown_insight")
        materialized_visuals.append(
            SupportingVisual(
                evidence_id=candidate.evidence_id,
                insight_ids=visual.insight_ids,
                support_explanation=visual.support_explanation,
                visual_id=candidate.visual_id,
                kind=candidate.kind,
                label=candidate.label,
                caption=candidate.caption,
                pdf_page=candidate.pdf_page,
                section_id=candidate.section_id,
                section_title=candidate.section_title,
                section_path=candidate.section_path,
                regions=candidate.regions,
                confidence=candidate.confidence,
            )
        )

    for ablation in draft.ablations:
        if any(
            candidates[evidence_id].kind == "text"
            for evidence_id in ablation.visual_evidence_ids
        ):
            raise _AnalysisProtocolError("analysis_ablation_requires_visual")

    ablation_ids = {ablation.ablation_id for ablation in draft.ablations}
    if any(
        not set(parameter.ablation_ids).issubset(ablation_ids)
        for parameter in draft.parameters
    ):
        raise _AnalysisProtocolError("analysis_unknown_ablation")

    return PaperAnalysis(
        paper_id=draft.paper_id,
        english_title=draft.english_title,
        chinese_title=draft.chinese_title,
        recommendation_reason=draft.recommendation_reason,
        research_problem=draft.research_problem,
        insights=draft.insights,
        supporting_visuals=tuple(materialized_visuals),
        insight_formation_logic=draft.insight_formation_logic,
        method_overview=draft.method_overview,
        method_modules=draft.method_modules,
        differences_from_prior_work=draft.differences_from_prior_work,
        parameters=draft.parameters,
        ablations=draft.ablations,
        experimental_conclusions=draft.experimental_conclusions,
        limitations=draft.limitations,
        links=draft.links,
        evidence_candidates=packet.candidates,
        generation=GenerationMetadata(
            model_identity=model_identity,
            prompt_version=prompt_version,
            schema_version=ANALYSIS_SCHEMA_VERSION,
            cache_key=cache_key,
            generated_at=generated_at,
        ),
    )


def _all_claims(draft: PaperAnalysisDraft) -> tuple[ClaimRecord, ...]:
    optional_claims = (
        draft.chinese_title,
        draft.recommendation_reason,
        draft.research_problem,
        draft.insight_formation_logic,
        draft.method_overview,
        draft.differences_from_prior_work,
    )
    return (
        *(claim for claim in optional_claims if claim is not None),
        *draft.insights,
        *(visual.support_explanation for visual in draft.supporting_visuals),
        *(module.purpose for module in draft.method_modules),
        *(parameter.role for parameter in draft.parameters),
        *(ablation.conclusion for ablation in draft.ablations),
        *draft.experimental_conclusions,
        *draft.limitations,
    )


def _all_referenced_evidence_ids(draft: PaperAnalysisDraft) -> set[str]:
    evidence_ids = {
        evidence_id
        for claim in _all_claims(draft)
        for evidence_id in claim.evidence_ids
    }
    evidence_ids.update(
        evidence_id
        for parameter in draft.parameters
        for evidence_id in parameter.evidence_ids
    )
    evidence_ids.update(
        evidence_id
        for ablation in draft.ablations
        for evidence_id in ablation.visual_evidence_ids
    )
    evidence_ids.update(visual.evidence_id for visual in draft.supporting_visuals)
    return evidence_ids


def _failed_result(
    paper_id: str, code: str, started: float
) -> PaperAnalysisResult:
    return PaperAnalysisResult(
        paper_id=paper_id,
        status="failed",
        analysis=None,
        issues=(
            AnalysisIssue(
                code=code,
                severity="error",
                message="Stage 3 analysis failed within the configured safety policy",
            ),
        ),
        processing_seconds=time.perf_counter() - started,
    )


def _analysis_result(
    analysis: PaperAnalysis, *, processing_seconds: float, cache_hit: bool
) -> PaperAnalysisResult:
    if analysis.insights:
        return PaperAnalysisResult.success(
            analysis,
            processing_seconds=processing_seconds,
            cache_hit=cache_hit,
        )
    return PaperAnalysisResult(
        paper_id=analysis.paper_id,
        status="partial",
        analysis=analysis,
        issues=(
            AnalysisIssue(
                code="analysis_insight_not_provided",
                severity="warning",
                message="The paper analysis did not provide a supported core Insight",
            ),
        ),
        processing_seconds=processing_seconds,
        cache_hit=cache_hit,
    )
