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
    ClaimRecord,
    EvidenceCandidate,
    EvidencePacket,
    PaperAnalysis,
    PaperAnalysisResult,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper
from zotero_arxiv_daily.analysis.validation_schemas import (
    VALIDATOR_VERSION,
    ClaimValidationResult,
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
    "unknown_evidence": "Claim references evidence that does not exist",
    "abstract_only_insight": "Insight lacks non-Abstract evidence",
    "duplicate_claim_id": "Claim identifier is not unique within the paper",
    "claim_kind_mismatch": "Claim kind does not match its analysis field",
    "claim_source_inference_mismatch": "Claim source type and inference flag are inconsistent",
    "supporting_visual_requires_figure_or_table": "Supporting visual does not reference Figure or Table evidence",
    "supporting_visual_unknown_insight": "Supporting visual references an unknown Insight",
    "invalid_support_explanation": "Supporting visual lacks a structured support explanation",
    "supporting_visual_provenance_mismatch": "Supporting visual provenance differs from its evidence candidate",
    "ablation_requires_visual": "Ablation does not reference real Figure or Table evidence",
    "ablation_unknown_parameter": "Ablation references an unknown parameter",
    "parameter_unknown_ablation": "Parameter references an unknown ablation",
    "parameter_unknown_evidence": "Parameter references evidence that does not exist",
    "parameter_source_inference_mismatch": "Parameter source type and inference flag are inconsistent",
    "duplicate_ablation_id": "Ablation identifier is not unique within the paper",
    "duplicate_parameter_name": "Parameter name is not unique within the paper",
    "core_insight_missing": "Analysis does not contain a verifiable core Insight",
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

    claim_results = _validate_analysis_rules(analysis, packet, issues)

    report_status = (
        "invalid"
        if any(issue.severity == "error" for issue in issues)
        else "partial"
        if analysis_result.status == "partial"
        or any(issue.code == "core_insight_missing" for issue in issues)
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
        claim_results=claim_results,
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


def _validate_analysis_rules(
    analysis: PaperAnalysis,
    packet: EvidencePacket,
    issues: list[ValidationIssue],
) -> tuple[ClaimValidationResult, ...]:
    candidates = {item.evidence_id: item for item in packet.candidates}
    claim_locations = _claim_locations(analysis)
    claim_ids = tuple(claim.claim_id for claim, _, _ in claim_locations)
    duplicates = {claim_id for claim_id in claim_ids if claim_ids.count(claim_id) > 1}
    for claim_id in sorted(duplicates):
        issues.append(
            _issue(
                "duplicate_claim_id",
                analysis.paper_id,
                field_path="claims",
                claim_id=claim_id,
            )
        )

    results: list[ClaimValidationResult] = []
    seen_claim_ids: set[str] = set()
    for claim, field_path, expected_kind in claim_locations:
        claim_codes: list[str] = []
        if claim.kind != expected_kind:
            claim_codes.append("claim_kind_mismatch")
        if claim.inferred != (claim.source_type == "system_inference"):
            claim_codes.append("claim_source_inference_mismatch")
        unknown = tuple(
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_id not in candidates
        )
        for evidence_id in unknown:
            issues.append(
                _issue(
                    "unknown_evidence",
                    analysis.paper_id,
                    field_path=f"{field_path}.evidence_ids",
                    claim_id=claim.claim_id,
                    evidence_id=evidence_id,
                )
            )
        if unknown:
            claim_codes.append("unknown_evidence")
        if expected_kind == "insight" and (
            not claim.evidence_ids
            or not any(
                evidence_id in candidates and not candidates[evidence_id].abstract_only
                for evidence_id in claim.evidence_ids
            )
        ):
            claim_codes.append("abstract_only_insight")
        for code in tuple(dict.fromkeys(claim_codes)):
            if code not in {"unknown_evidence"}:
                issues.append(
                    _issue(
                        code,
                        analysis.paper_id,
                        field_path=field_path,
                        claim_id=claim.claim_id,
                    )
                )
        if claim.claim_id not in seen_claim_ids:
            seen_claim_ids.add(claim.claim_id)
            results.append(
                ClaimValidationResult(
                    claim_id=claim.claim_id,
                    status="invalid" if claim_codes or claim.claim_id in duplicates else "valid",
                    resolved_evidence_ids=tuple(
                        evidence_id
                        for evidence_id in claim.evidence_ids
                        if evidence_id in candidates
                    ),
                    issue_codes=tuple(dict.fromkeys(claim_codes)),
                )
            )

    _validate_supporting_visuals(analysis, candidates, issues)
    _validate_parameters_and_ablations(analysis, candidates, issues)
    if not analysis.insights:
        issues.append(
            _issue(
                "core_insight_missing",
                analysis.paper_id,
                field_path="insights",
                severity="warning",
            )
        )
    return tuple(results)


def _claim_locations(
    analysis: PaperAnalysis,
) -> tuple[tuple[ClaimRecord, str, str], ...]:
    optional = (
        (analysis.chinese_title, "chinese_title", "title"),
        (analysis.recommendation_reason, "recommendation_reason", "recommendation"),
        (analysis.research_problem, "research_problem", "problem"),
        (analysis.insight_formation_logic, "insight_formation_logic", "insight_logic"),
        (analysis.method_overview, "method_overview", "method"),
        (analysis.differences_from_prior_work, "differences_from_prior_work", "difference"),
    )
    located: list[tuple[ClaimRecord, str, str]] = [
        (claim, path, kind)
        for claim, path, kind in optional
        if claim is not None
    ]
    located.extend(
        (claim, f"insights.{index}", "insight")
        for index, claim in enumerate(analysis.insights)
    )
    located.extend(
        (visual.support_explanation, f"supporting_visuals.{index}.support_explanation", "support")
        for index, visual in enumerate(analysis.supporting_visuals)
    )
    located.extend(
        (module.purpose, f"method_modules.{index}.purpose", "method")
        for index, module in enumerate(analysis.method_modules)
    )
    located.extend(
        (parameter.role, f"parameters.{index}.role", "parameter")
        for index, parameter in enumerate(analysis.parameters)
    )
    located.extend(
        (ablation.conclusion, f"ablations.{index}.conclusion", "ablation")
        for index, ablation in enumerate(analysis.ablations)
    )
    located.extend(
        (claim, f"experimental_conclusions.{index}", "result")
        for index, claim in enumerate(analysis.experimental_conclusions)
    )
    located.extend(
        (claim, f"limitations.{index}", "limitation")
        for index, claim in enumerate(analysis.limitations)
    )
    return tuple(located)


def _validate_supporting_visuals(
    analysis: PaperAnalysis,
    candidates: dict[str, EvidenceCandidate],
    issues: list[ValidationIssue],
) -> None:
    insight_ids = {insight.claim_id for insight in analysis.insights}
    for index, visual in enumerate(analysis.supporting_visuals):
        path = f"supporting_visuals.{index}"
        candidate = candidates.get(visual.evidence_id)
        if candidate is None or candidate.kind == "text" or candidate.visual_id is None:
            issues.append(
                _issue(
                    "supporting_visual_requires_figure_or_table",
                    analysis.paper_id,
                    field_path=f"{path}.evidence_id",
                    evidence_id=visual.evidence_id,
                    visual_id=visual.visual_id,
                )
            )
        if not visual.insight_ids or not set(visual.insight_ids).issubset(insight_ids):
            issues.append(
                _issue(
                    "supporting_visual_unknown_insight",
                    analysis.paper_id,
                    field_path=f"{path}.insight_ids",
                    visual_id=visual.visual_id,
                )
            )
        explanation = visual.support_explanation
        if (
            explanation.kind != "support"
            or not explanation.text_zh.strip()
            or visual.evidence_id not in explanation.evidence_ids
        ):
            issues.append(
                _issue(
                    "invalid_support_explanation",
                    analysis.paper_id,
                    field_path=f"{path}.support_explanation",
                    claim_id=explanation.claim_id,
                    evidence_id=visual.evidence_id,
                    visual_id=visual.visual_id,
                )
            )
        if candidate is not None and candidate.kind != "text" and not _support_matches_candidate(
            visual, candidate
        ):
            issues.append(
                _issue(
                    "supporting_visual_provenance_mismatch",
                    analysis.paper_id,
                    field_path=path,
                    evidence_id=visual.evidence_id,
                    visual_id=visual.visual_id,
                )
            )


def _support_matches_candidate(visual, candidate: EvidenceCandidate) -> bool:
    return (
        visual.evidence_id == candidate.evidence_id
        and visual.visual_id == candidate.visual_id
        and visual.kind == candidate.kind
        and visual.label == candidate.label
        and visual.caption == candidate.caption
        and visual.pdf_page == candidate.pdf_page
        and visual.section_id == candidate.section_id
        and visual.section_title == candidate.section_title
        and visual.section_path == candidate.section_path
        and visual.regions == candidate.regions
        and visual.confidence == candidate.confidence
    )


def _validate_parameters_and_ablations(
    analysis: PaperAnalysis,
    candidates: dict[str, EvidenceCandidate],
    issues: list[ValidationIssue],
) -> None:
    parameter_names = tuple(parameter.name for parameter in analysis.parameters)
    duplicate_names = {
        name for name in parameter_names if parameter_names.count(name) > 1
    }
    for name in sorted(duplicate_names):
        issues.append(
            _issue(
                "duplicate_parameter_name",
                analysis.paper_id,
                field_path="parameters",
            )
        )
    ablation_ids = tuple(ablation.ablation_id for ablation in analysis.ablations)
    duplicate_ablation_ids = {
        ablation_id for ablation_id in ablation_ids if ablation_ids.count(ablation_id) > 1
    }
    for ablation_id in sorted(duplicate_ablation_ids):
        issues.append(
            _issue(
                "duplicate_ablation_id",
                analysis.paper_id,
                field_path="ablations",
            )
        )
    parameter_name_set = set(parameter_names)
    ablation_id_set = set(ablation_ids)
    for index, parameter in enumerate(analysis.parameters):
        path = f"parameters.{index}"
        if parameter.inferred != (parameter.source_type == "system_inference"):
            issues.append(
                _issue(
                    "parameter_source_inference_mismatch",
                    analysis.paper_id,
                    field_path=path,
                    claim_id=parameter.role.claim_id,
                )
            )
        for evidence_id in parameter.evidence_ids:
            if evidence_id not in candidates:
                issues.append(
                    _issue(
                        "parameter_unknown_evidence",
                        analysis.paper_id,
                        field_path=f"{path}.evidence_ids",
                        evidence_id=evidence_id,
                    )
                )
        for ablation_id in parameter.ablation_ids:
            if ablation_id not in ablation_id_set:
                issues.append(
                    _issue(
                        "parameter_unknown_ablation",
                        analysis.paper_id,
                        field_path=f"{path}.ablation_ids",
                    )
                )
    for index, ablation in enumerate(analysis.ablations):
        path = f"ablations.{index}"
        for parameter_name in ablation.parameter_names:
            if parameter_name not in parameter_name_set:
                issues.append(
                    _issue(
                        "ablation_unknown_parameter",
                        analysis.paper_id,
                        field_path=f"{path}.parameter_names",
                    )
                )
        for evidence_id in ablation.visual_evidence_ids:
            candidate = candidates.get(evidence_id)
            if candidate is None or candidate.kind == "text":
                issues.append(
                    _issue(
                        "ablation_requires_visual",
                        analysis.paper_id,
                        field_path=f"{path}.visual_evidence_ids",
                        evidence_id=evidence_id,
                    )
                )


def _is_abstract_path(path: tuple[str, ...]) -> bool:
    normalized = tuple(" ".join(title.lower().split()) for title in path)
    return bool(normalized) and all(title in {"abstract", "summary"} for title in normalized)


def _issue(
    code: str,
    paper_id: str,
    *,
    field_path: str,
    claim_id: str | None = None,
    evidence_id: str | None = None,
    visual_id: str | None = None,
    severity: str = "error",
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        paper_id=paper_id,
        claim_id=claim_id,
        field_path=field_path,
        evidence_id=evidence_id,
        visual_id=visual_id,
        message=_SAFE_MESSAGES[code],
    )
