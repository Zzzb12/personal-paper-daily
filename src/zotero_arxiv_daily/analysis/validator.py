from __future__ import annotations

import hashlib
import json
import time

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBlock,
    DocumentGraph,
    SectionNode,
    VisualArtifact,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    EvidenceCandidate,
    EvidencePacket,
    PaperAnalysis,
    PaperAnalysisResult,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper
from zotero_arxiv_daily.analysis.validation_schemas import (
    VALIDATOR_VERSION,
    ValidatedPaperAnalysis,
    ValidationIssue,
    ValidationPaperResult,
    ValidationReport,
)


_SAFE_MESSAGES = {
    "analysis_unavailable": "Stage 3 analysis is unavailable for validation",
    "paper_id_mismatch": "Validation inputs do not share the same paper identity",
    "candidate_title_mismatch": "Analysis English title does not match candidate identity",
    "candidate_link_mismatch": "Analysis links do not match candidate identity",
    "packet_document_mismatch": "Evidence packet does not match the document identity",
    "analysis_evidence_candidates_modified": "Saved analysis evidence candidates differ from the evidence packet",
    "evidence_source_missing": "Evidence source cannot be resolved in the document graph",
    "text_provenance_mismatch": "Text evidence provenance differs from the document graph",
    "visual_provenance_mismatch": "Figure or Table provenance differs from the document graph",
}


def validate_paper(
    candidate: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    analysis_result: PaperAnalysisResult,
    *,
    validator_version: str = VALIDATOR_VERSION,
) -> ValidationPaperResult:
    started = time.perf_counter()
    if analysis_result.analysis is None or analysis_result.status in {"failed", "skipped"}:
        status = "skipped" if analysis_result.status == "skipped" else "failed"
        issue = _issue(
            "analysis_unavailable",
            candidate.paper_id,
            field_path="analysis",
        )
        return ValidationPaperResult(
            paper_id=candidate.paper_id,
            status=status,
            validated=None,
            issues=(issue,),
            processing_seconds=time.perf_counter() - started,
            validator_version=validator_version,
        )

    analysis = analysis_result.analysis
    issues: list[ValidationIssue] = []
    _validate_identities(candidate, document, packet, analysis_result, analysis, issues)
    _validate_packet_against_document(document, packet, issues)
    if analysis.evidence_candidates != packet.candidates:
        issues.append(
            _issue(
                "analysis_evidence_candidates_modified",
                candidate.paper_id,
                field_path="evidence_candidates",
            )
        )

    report_status = (
        "invalid"
        if any(issue.severity == "error" for issue in issues)
        else "partial"
        if analysis_result.status == "partial"
        else "valid"
    )
    report = ValidationReport(
        validator_version=validator_version,
        paper_id=candidate.paper_id,
        status=report_status,
        publication_eligibility="eligible" if report_status == "valid" else "blocked",
        input_fingerprint=validation_input_fingerprint(
            candidate, document, packet, analysis
        ),
        claim_results=(),
        issues=tuple(issues),
    )
    validated = ValidatedPaperAnalysis(analysis=analysis, report=report)
    result_status = {
        "valid": "validated",
        "partial": "partial",
        "invalid": "invalid",
    }[report_status]
    return ValidationPaperResult(
        paper_id=candidate.paper_id,
        status=result_status,
        validated=validated,
        issues=report.issues,
        processing_seconds=time.perf_counter() - started,
        validator_version=validator_version,
    )


def validation_input_fingerprint(
    candidate: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    analysis: PaperAnalysis,
) -> str:
    payload = json.dumps(
        {
            "candidate": {
                "paper_id": candidate.paper_id,
                "english_title": candidate.title,
                "pdf_url": candidate.pdf_url,
                "arxiv_url": candidate.arxiv_url,
                "code_url": candidate.code_url,
            },
            "document": {
                "schema_version": document.schema_version,
                "parser": document.parser,
                "parser_version": document.parser_version,
                "mapper_version": document.mapper_version,
                "config_version": document.config_version,
                "pdf_sha256": document.pdf.sha256,
                "content_fingerprint": document.content_fingerprint,
            },
            "packet": packet.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_identities(
    candidate: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    analysis_result: PaperAnalysisResult,
    analysis: PaperAnalysis,
    issues: list[ValidationIssue],
) -> None:
    paper_ids = {
        candidate.paper_id,
        packet.paper_id,
        analysis_result.paper_id,
        analysis.paper_id,
        *(item.paper_id for item in packet.candidates),
        *(item.paper_id for item in analysis.evidence_candidates),
    }
    if len(paper_ids) != 1:
        issues.append(
            _issue("paper_id_mismatch", candidate.paper_id, field_path="paper_id")
        )
    if analysis.english_title != candidate.title:
        issues.append(
            _issue(
                "candidate_title_mismatch",
                candidate.paper_id,
                field_path="english_title",
            )
        )
    expected_links = (candidate.pdf_url, candidate.arxiv_url, candidate.code_url)
    actual_links = (
        analysis.links.pdf_url,
        analysis.links.arxiv_url,
        analysis.links.code_url,
    )
    if actual_links != expected_links:
        issues.append(
            _issue(
                "candidate_link_mismatch",
                candidate.paper_id,
                field_path="links",
            )
        )
    if (
        packet.document_fingerprint != document.content_fingerprint
        or document.pdf.source_url != candidate.pdf_url
    ):
        issues.append(
            _issue(
                "packet_document_mismatch",
                candidate.paper_id,
                field_path="document_fingerprint",
            )
        )


def _validate_packet_against_document(
    document: DocumentGraph,
    packet: EvidencePacket,
    issues: list[ValidationIssue],
) -> None:
    block_by_id = {block.block_id: block for block in document.blocks}
    section_by_id = {section.section_id: section for section in document.sections}
    visual_by_id = {visual.visual_id: visual for visual in document.visuals}
    section_paths = {
        section_id: _section_path(section, section_by_id)
        for section_id, section in section_by_id.items()
    }
    for index, candidate in enumerate(packet.candidates):
        field_path = f"evidence_candidates.{index}"
        if candidate.kind == "text":
            _validate_text_candidate(
                packet.paper_id,
                candidate,
                block_by_id,
                section_by_id,
                section_paths,
                field_path,
                issues,
            )
            continue
        visual = visual_by_id.get(candidate.visual_id or "")
        if visual is None:
            issues.append(
                _issue(
                    "evidence_source_missing",
                    packet.paper_id,
                    field_path=field_path,
                    evidence_id=candidate.evidence_id,
                    visual_id=candidate.visual_id,
                )
            )
            continue
        if not _visual_matches(
            candidate, visual, section_by_id, section_paths
        ):
            issues.append(
                _issue(
                    "visual_provenance_mismatch",
                    packet.paper_id,
                    field_path=field_path,
                    evidence_id=candidate.evidence_id,
                    visual_id=candidate.visual_id,
                )
            )


def _validate_text_candidate(
    paper_id: str,
    candidate: EvidenceCandidate,
    block_by_id: dict[str, DocumentBlock],
    section_by_id: dict[str, SectionNode],
    section_paths: dict[str, tuple[str, ...]],
    field_path: str,
    issues: list[ValidationIssue],
) -> None:
    if len(candidate.block_ids) != 1 or candidate.block_ids[0] not in block_by_id:
        issues.append(
            _issue(
                "evidence_source_missing",
                paper_id,
                field_path=field_path,
                evidence_id=candidate.evidence_id,
            )
        )
        return
    block = block_by_id[candidate.block_ids[0]]
    section = section_by_id.get(block.section_id or "")
    region = candidate.regions[0] if len(candidate.regions) == 1 else None
    expected_path = section_paths.get(block.section_id or "", ())
    matches = (
        candidate.pdf_page == block.pdf_page
        and candidate.section_id == block.section_id
        and candidate.section_title == (section.title if section else None)
        and candidate.section_path == expected_path
        and candidate.visual_id is None
        and candidate.label is None
        and candidate.caption is None
        and region is not None
        and region.pdf_page == block.pdf_page
        and region.bbox == block.bbox
        and region.image_path is None
        and region.source_mapping == block.source_mapping
        and region.confidence == block.source_mapping.confidence
        and candidate.confidence == block.source_mapping.confidence
        and candidate.abstract_only == _is_abstract_path(expected_path)
    )
    if not matches:
        issues.append(
            _issue(
                "text_provenance_mismatch",
                paper_id,
                field_path=field_path,
                evidence_id=candidate.evidence_id,
            )
        )


def _visual_matches(
    candidate: EvidenceCandidate,
    visual: VisualArtifact,
    section_by_id: dict[str, SectionNode],
    section_paths: dict[str, tuple[str, ...]],
) -> bool:
    section = section_by_id.get(visual.section_id or "")
    path = section_paths.get(visual.section_id or "", ())
    if (
        candidate.kind != visual.kind
        or candidate.visual_id != visual.visual_id
        or candidate.label != visual.label
        or candidate.caption != visual.caption
        or candidate.evidence_text != visual.caption
        or candidate.pdf_page != visual.regions[0].pdf_page
        or candidate.section_id != visual.section_id
        or candidate.section_title != (section.title if section else None)
        or candidate.section_path != path
        or candidate.block_ids != visual.caption_block_ids
        or candidate.confidence != visual.confidence
        or candidate.abstract_only != _is_abstract_path(path)
        or len(candidate.regions) != len(visual.regions)
    ):
        return False
    return all(
        evidence.pdf_page == source.pdf_page
        and evidence.bbox == source.bbox
        and evidence.image_path == source.image_path
        and evidence.source_mapping == source.source_mapping
        and evidence.confidence == source.confidence
        for evidence, source in zip(candidate.regions, visual.regions, strict=True)
    )


def _section_path(
    section: SectionNode,
    section_by_id: dict[str, SectionNode],
) -> tuple[str, ...]:
    path: list[str] = []
    current: SectionNode | None = section
    seen: set[str] = set()
    while current is not None:
        if current.section_id in seen:
            return ()
        seen.add(current.section_id)
        path.append(current.title)
        current = section_by_id.get(current.parent_id or "")
    return tuple(reversed(path))


def _is_abstract_path(path: tuple[str, ...]) -> bool:
    normalized = tuple(" ".join(title.lower().split()) for title in path)
    return bool(normalized) and all(title in {"abstract", "summary"} for title in normalized)


def _issue(
    code: str,
    paper_id: str,
    *,
    field_path: str,
    evidence_id: str | None = None,
    visual_id: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity="error",
        paper_id=paper_id,
        field_path=field_path,
        evidence_id=evidence_id,
        visual_id=visual_id,
        message=_SAFE_MESSAGES[code],
    )
