from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator

from zotero_arxiv_daily.analysis.document_schemas import (
    BoundingBox,
    DocumentBatchResult,
    DocumentBlock,
    DocumentGraph,
    DocumentPage,
    PaperDocumentResult,
    PdfArtifact,
    SectionNode,
    SourceMapping,
    VisualArtifact,
    VisualRegion,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    AblationRecord,
    AnalysisBatchResult,
    ClaimRecord,
    EvidencePacket,
    GenerationMetadata,
    MethodModule,
    PaperAnalysis,
    PaperAnalysisResult,
    PaperLinks,
    ParameterRecord,
    SupportingVisual,
)
from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidatePaper,
    CandidateSelectionLimits,
    RankingModelVersions,
    RankingRecord,
    StrictModel,
)
from zotero_arxiv_daily.analysis.validation_cache import (
    ValidationCache,
    build_validation_cache_identity,
)
from zotero_arxiv_daily.analysis.validation_schemas import (
    VALIDATION_SCHEMA_VERSION,
    VALIDATOR_VERSION,
    ValidationBatchResult,
    ValidationIssue,
    ValidationPaperResult,
)
from zotero_arxiv_daily.analysis.validator import validate_paper
from zotero_arxiv_daily.documents.evidence import (
    EvidenceBuildSettings,
    build_evidence_packet,
)


class ValidationSettings(StrictModel):
    schema_version: str = VALIDATION_SCHEMA_VERSION
    validator_version: str = VALIDATOR_VERSION
    max_papers: int = Field(default=5, ge=1, le=5)

    @field_validator("schema_version", "validator_version")
    @classmethod
    def normalize_version(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("validation version must not be blank")
        return normalized


@dataclass
class ValidationDependencies:
    cache: ValidationCache
    clock: Callable[[], datetime]


def build_validation_batch(
    candidates: CandidateBatch,
    documents: DocumentBatchResult,
    packets: tuple[EvidencePacket, ...],
    analyses: AnalysisBatchResult,
    settings: ValidationSettings,
    dependencies: ValidationDependencies,
) -> ValidationBatchResult:
    if len({candidates.run_id, documents.run_id, analyses.run_id}) != 1:
        raise ValueError("candidate, document, and analysis run_id values must match")

    paper_by_id = {paper.paper_id: paper for paper in candidates.candidates}
    document_by_id, duplicate_documents = _unique_index(documents.results)
    packet_by_id, duplicate_packets = _unique_index(packets)
    analysis_by_id, duplicate_analyses = _unique_index(analyses.results)
    results: list[ValidationPaperResult] = []

    for paper_id in candidates.selected_for_full_analysis[: settings.max_papers]:
        if paper_id in duplicate_documents | duplicate_packets | duplicate_analyses:
            results.append(
                _unavailable(
                    paper_id,
                    "failed",
                    "validation_duplicate_input",
                    settings.validator_version,
                )
            )
            continue
        paper = paper_by_id.get(paper_id)
        document_result = document_by_id.get(paper_id)
        packet = packet_by_id.get(paper_id)
        analysis_result = analysis_by_id.get(paper_id)
        if paper is None:
            results.append(
                _unavailable(
                    paper_id,
                    "failed",
                    "validation_candidate_missing",
                    settings.validator_version,
                )
            )
            continue
        if document_result is None or packet is None or analysis_result is None:
            results.append(
                _unavailable(
                    paper_id,
                    "skipped",
                    "validation_input_missing",
                    settings.validator_version,
                )
            )
            continue
        if document_result.document is None or document_result.status in {"failed", "skipped"}:
            results.append(
                _unavailable(
                    paper_id,
                    "failed",
                    "validation_document_unavailable",
                    settings.validator_version,
                )
            )
            continue

        result = _validate_with_cache(
            paper,
            document_result.document,
            packet,
            analysis_result,
            settings,
            dependencies,
        )
        results.append(result)

    return ValidationBatchResult(
        validator_version=settings.validator_version,
        run_id=candidates.run_id,
        created_at=dependencies.clock(),
        results=tuple(results),
        cache_hit_count=sum(result.cache_hit for result in results),
    )


def _validate_with_cache(
    paper: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    analysis_result: PaperAnalysisResult,
    settings: ValidationSettings,
    dependencies: ValidationDependencies,
) -> ValidationPaperResult:
    if analysis_result.analysis is None:
        return validate_paper(
            paper,
            document,
            packet,
            analysis_result,
            validator_version=settings.validator_version,
        )
    identity = build_validation_cache_identity(
        paper,
        document,
        packet,
        analysis_result.analysis,
        analysis_result.status,
        validator_version=settings.validator_version,
        validation_schema_version=settings.schema_version,
    )
    cached = dependencies.cache.read(identity)
    if cached is not None:
        status = {
            "valid": "validated",
            "partial": "partial",
            "invalid": "invalid",
        }[cached.report.status]
        return ValidationPaperResult(
            paper_id=paper.paper_id,
            status=status,
            validated=cached,
            issues=cached.report.issues,
            processing_seconds=0,
            cache_hit=True,
            validator_version=settings.validator_version,
        )
    result = validate_paper(
        paper,
        document,
        packet,
        analysis_result,
        validator_version=settings.validator_version,
    )
    if result.status == "validated" and result.validated is not None:
        dependencies.cache.write(identity, result.validated)
    return result


def _unique_index(items) -> tuple[dict[str, Any], set[str]]:
    index: dict[str, Any] = {}
    duplicates: set[str] = set()
    for item in items:
        paper_id = item.paper_id
        if paper_id in index:
            duplicates.add(paper_id)
        else:
            index[paper_id] = item
    return index, duplicates


def _unavailable(
    paper_id: str,
    status: str,
    code: str,
    validator_version: str,
) -> ValidationPaperResult:
    issue = ValidationIssue(
        code=code,
        severity="error" if status == "failed" else "warning",
        paper_id=paper_id,
        field_path="validation_inputs",
        message="Required Stage 4 validation input is unavailable or ambiguous",
    )
    return ValidationPaperResult(
        paper_id=paper_id,
        status=status,
        validated=None,
        issues=(issue,),
        processing_seconds=0,
        validator_version=validator_version,
    )


def run_offline_fixture(
    fixture_path: Path,
    *,
    dry_run: bool,
    cache_root: Path,
) -> ValidationBatchResult:
    if not dry_run:
        raise ValueError("offline fixture execution requires dry_run=true")
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    candidate, document, packet, analysis_result = _offline_inputs(fixture)
    now = _fixture_time(fixture)
    candidate_batch = _offline_candidate_batch(fixture, candidate, now)
    document_batch = DocumentBatchResult(
        run_id=fixture["run_id"],
        created_at=now,
        results=(
            PaperDocumentResult(
                paper_id=candidate.paper_id,
                status="success",
                document=document,
                processing_seconds=0,
            ),
        ),
    )
    analysis_batch = AnalysisBatchResult(
        run_id=fixture["run_id"],
        created_at=now,
        results=(analysis_result,),
        expensive_call_count=0,
        cache_hit_count=0,
    )
    del cache_root
    return build_validation_batch(
        candidate_batch,
        document_batch,
        (packet,),
        analysis_batch,
        ValidationSettings(),
        ValidationDependencies(cache=_NoWriteCache(), clock=lambda: now),
    )


class _NoWriteCache:
    def read(self, identity):
        return None

    def write(self, identity, validated):
        return None


def _offline_inputs(fixture: dict[str, Any]):
    now = _fixture_time(fixture)
    candidate = CandidatePaper(
        paper_id=fixture["paper_id"],
        arxiv_id=fixture["arxiv_id"],
        version=1,
        title=fixture["english_title"],
        authors=("Synthetic Author",),
        abstract="Artificial offline fixture abstract.",
        categories=("cs.CV",),
        primary_category="cs.CV",
        published_at=now,
        updated_at=now,
        arxiv_url=fixture["arxiv_url"],
        pdf_url=fixture["pdf_url"],
        code_url=None,
    )
    heading_box = BoundingBox(left=10, top=10, right=90, bottom=20)
    text_box = BoundingBox(left=10, top=25, right=90, bottom=45)
    caption_box = BoundingBox(left=10, top=50, right=90, bottom=60)
    visual_box = BoundingBox(left=10, top=65, right=90, bottom=95)

    def mapping(source_id: str, box: BoundingBox) -> SourceMapping:
        return SourceMapping(
            parser="fixture",
            parser_version="1",
            source_item_id=source_id,
            pdf_page=1,
            bbox=box,
            mapping_method="fixture",
            confidence=1,
        )

    blocks = (
        DocumentBlock(
            block_id="heading-method",
            block_type="heading",
            text=fixture["section_title"],
            pdf_page=1,
            bbox=heading_box,
            reading_order=0,
            section_id="section-method",
            source_mapping=mapping("heading-method", heading_box),
        ),
        DocumentBlock(
            block_id="text-method",
            block_type="text",
            text=fixture["method_text"],
            pdf_page=1,
            bbox=text_box,
            reading_order=1,
            section_id="section-method",
            source_mapping=mapping("text-method", text_box),
        ),
        DocumentBlock(
            block_id="caption-table-1",
            block_type="caption",
            text=fixture["table_caption"],
            pdf_page=1,
            bbox=caption_box,
            reading_order=2,
            section_id="section-method",
            source_mapping=mapping("caption-table-1", caption_box),
        ),
    )
    document = DocumentGraph(
        parser="fixture",
        parser_version="1",
        mapper_version="1",
        config_version="1",
        content_fingerprint=fixture["content_fingerprint"],
        evidence_root=Path("cache/documents/evidence"),
        pdf=PdfArtifact(
            source_url=candidate.pdf_url,
            local_path=Path("cache/documents/offline-stage4.pdf"),
            sha256=fixture["pdf_sha256"],
            byte_size=1,
            content_type="application/pdf",
            page_count=1,
            downloaded_at=now,
            cache_hit=False,
        ),
        pages=(
            DocumentPage(
                pdf_page=1,
                width=100,
                height=100,
                text_layer_status="present",
                block_ids=tuple(block.block_id for block in blocks),
            ),
        ),
        blocks=blocks,
        sections=(
            SectionNode(
                section_id="section-method",
                title=fixture["section_title"],
                level=1,
                parent_id=None,
                block_ids=tuple(block.block_id for block in blocks),
                start_pdf_page=1,
                end_pdf_page=1,
                source_mapping=mapping("heading-method", heading_box),
                confidence=1,
            ),
        ),
        visuals=(
            VisualArtifact(
                visual_id="table-1",
                kind="table",
                label=fixture["table_label"],
                caption=fixture["table_caption"],
                caption_block_ids=("caption-table-1",),
                section_id="section-method",
                regions=(
                    VisualRegion(
                        pdf_page=1,
                        bbox=visual_box,
                        image_path=Path("cache/documents/evidence/table-1.png"),
                        source_mapping=mapping("table-1", visual_box),
                        confidence=1,
                    ),
                ),
                confidence=1,
            ),
        ),
    )
    packet = build_evidence_packet(
        candidate.paper_id,
        document,
        EvidenceBuildSettings(max_visuals=3),
    )
    text = next(item for item in packet.candidates if item.kind == "text")
    visual = next(item for item in packet.candidates if item.kind == "table")

    def claim(claim_id: str, kind: str, text_zh: str, evidence_ids):
        return ClaimRecord(
            claim_id=claim_id,
            kind=kind,
            text_zh=text_zh,
            source_type="system_summary",
            inferred=False,
            evidence_ids=tuple(evidence_ids),
            confidence=1,
        )

    insight = claim(
        "insight-1",
        "insight",
        fixture["insight_zh"],
        (text.evidence_id, visual.evidence_id),
    )
    support = claim(
        "support-1",
        "support",
        fixture["support_zh"],
        (visual.evidence_id,),
    )
    analysis = PaperAnalysis(
        paper_id=candidate.paper_id,
        english_title=candidate.title,
        chinese_title=None,
        recommendation_reason=None,
        research_problem=None,
        insights=(insight,),
        supporting_visuals=(
            SupportingVisual(
                evidence_id=visual.evidence_id,
                insight_ids=(insight.claim_id,),
                support_explanation=support,
                visual_id=visual.visual_id,
                kind=visual.kind,
                label=visual.label,
                caption=visual.caption,
                pdf_page=visual.pdf_page,
                section_id=visual.section_id,
                section_title=visual.section_title,
                section_path=visual.section_path,
                regions=visual.regions,
                confidence=visual.confidence,
            ),
        ),
        insight_formation_logic=None,
        method_overview=claim(
            "method-1", "method", "人工方法描述。", (text.evidence_id,)
        ),
        method_modules=(
            MethodModule(
                name="Synthetic module",
                purpose=claim(
                    "module-1", "method", "人工模块作用。", (text.evidence_id,)
                ),
            ),
        ),
        differences_from_prior_work=None,
        parameters=(
            ParameterRecord(
                name=fixture["parameter_name"],
                symbol=None,
                role=claim(
                    "parameter-role",
                    "parameter",
                    "人工参数作用。",
                    (text.evidence_id,),
                ),
                final_value=None,
                selection_method=None,
                per_model_tuning=None,
                evidence_ids=(text.evidence_id,),
                ablation_ids=("ablation-1",),
                source_type="author_statement",
                inferred=False,
                confidence=1,
            ),
        ),
        ablations=(
            AblationRecord(
                ablation_id="ablation-1",
                parameter_names=(fixture["parameter_name"],),
                conclusion=claim(
                    "ablation-claim",
                    "ablation",
                    fixture["ablation_zh"],
                    (visual.evidence_id,),
                ),
                visual_evidence_ids=(visual.evidence_id,),
            ),
        ),
        experimental_conclusions=(),
        limitations=(),
        links=PaperLinks(
            pdf_url=candidate.pdf_url,
            arxiv_url=candidate.arxiv_url,
            code_url=None,
        ),
        evidence_candidates=packet.candidates,
        generation=GenerationMetadata(
            model_identity="fake:offline-stage4",
            prompt_version="stage3-v1",
            schema_version="1.0",
            cache_key="d" * 64,
            generated_at=now,
        ),
    )
    return (
        candidate,
        document,
        packet,
        PaperAnalysisResult(
            paper_id=candidate.paper_id,
            status="success",
            analysis=analysis,
            processing_seconds=0,
        ),
    )


def _offline_candidate_batch(
    fixture: dict[str, Any],
    candidate: CandidatePaper,
    now: datetime,
) -> CandidateBatch:
    versions = RankingModelVersions(
        provider="offline-fixture",
        model="fixture-v1",
        task="retrieval",
        scorer="cosine",
        embedding_identity_hash="e" * 64,
    )
    return CandidateBatch(
        run_id=fixture["run_id"],
        created_at=now,
        retrieved_at=now,
        categories=candidate.categories,
        config_hash="f" * 64,
        interest_corpus_fingerprint="c" * 64,
        candidates=(candidate,),
        rankings=(
            RankingRecord(
                paper_id=candidate.paper_id,
                embedding_score=1,
                final_score=1,
                rank=1,
                reason="artificial offline fixture",
                model_versions=versions,
            ),
        ),
        selected_for_llm=(candidate.paper_id,),
        selected_for_full_analysis=(candidate.paper_id,),
        counts=CandidateCounts(retrieved=1, deduplicated=1, invalid=0, excluded=0),
        limits=CandidateSelectionLimits(),
    )


def _fixture_time(fixture: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(fixture["now"]).replace("Z", "+00:00")).astimezone(UTC)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 4 validation pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=Path("cache/validation"))
    args = parser.parse_args(argv)
    result = run_offline_fixture(
        args.offline_fixture,
        dry_run=args.dry_run,
        cache_root=args.cache_root,
    )
    counts = {
        "valid": sum(item.status == "validated" for item in result.results),
        "partial": sum(item.status == "partial" for item in result.results),
        "invalid": sum(item.status == "invalid" for item in result.results),
        "failed": sum(item.status == "failed" for item in result.results),
        "skipped": sum(item.status == "skipped" for item in result.results),
    }
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "validator_version": result.validator_version,
                **counts,
                "eligible": sum(
                    item.validated is not None
                    and item.validated.report.publication_eligibility == "eligible"
                    for item in result.results
                ),
                "cache_hit_count": result.cache_hit_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
