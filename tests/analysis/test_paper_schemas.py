from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from zotero_arxiv_daily.analysis.document_schemas import BoundingBox, SourceMapping
from zotero_arxiv_daily.analysis.paper_schemas import (
    AblationRecord,
    AnalysisBatchResult,
    ClaimRecord,
    DraftSupportingVisual,
    EvidenceCandidate,
    EvidencePacket,
    EvidenceRegion,
    GenerationMetadata,
    MethodModule,
    PaperAnalysis,
    PaperAnalysisResult,
    PaperLinks,
    ParameterRecord,
    SupportingVisual,
)


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def source_mapping(source_id="docling-table", page=2):
    box = BoundingBox(left=10, top=20, right=90, bottom=70)
    return SourceMapping(
        parser="docling",
        parser_version="2.113.0",
        source_item_id=source_id,
        pdf_page=page,
        bbox=box,
        mapping_method="provenance",
        confidence=0.95,
    )


def region():
    mapping = source_mapping()
    return EvidenceRegion(
        pdf_page=2,
        bbox=mapping.bbox,
        image_path=Path("cache/documents/evidence/table-1.png"),
        source_mapping=mapping,
        confidence=0.9,
    )


def evidence(evidence_id="ev-table", *, abstract_only=False):
    return EvidenceCandidate(
        evidence_id=evidence_id,
        paper_id="arxiv:2401.00001",
        kind="table",
        pdf_page=2,
        section_id="section-experiments",
        section_title="4 Experiments",
        section_path=("4 Experiments",),
        block_ids=("caption-1",),
        evidence_text="Table 1 reports the synthetic result.",
        visual_id="table-1",
        label="Table 1",
        caption="Table 1: Synthetic results.",
        regions=(region(),),
        confidence=0.9,
        abstract_only=abstract_only,
    )


def claim(
    claim_id="claim-1",
    *,
    kind="insight",
    source_type="system_summary",
    inferred=False,
    evidence_ids=("ev-table",),
):
    return ClaimRecord(
        claim_id=claim_id,
        kind=kind,
        text_zh="合成证据支持该结论。",
        source_type=source_type,
        inferred=inferred,
        evidence_ids=evidence_ids,
        confidence=0.8,
    )


def draft_visual():
    return DraftSupportingVisual(
        evidence_id="ev-table",
        insight_ids=("insight-1",),
        support_explanation=claim(
            "support-1", kind="support", evidence_ids=("ev-table",)
        ),
    )


def supporting_visual():
    item = evidence()
    return SupportingVisual(
        evidence_id=item.evidence_id,
        insight_ids=("insight-1",),
        support_explanation=claim(
            "support-1", kind="support", evidence_ids=(item.evidence_id,)
        ),
        visual_id=item.visual_id,
        kind=item.kind,
        label=item.label,
        caption=item.caption,
        pdf_page=item.pdf_page,
        section_id=item.section_id,
        section_title=item.section_title,
        section_path=item.section_path,
        regions=item.regions,
        confidence=item.confidence,
    )


def analysis():
    insight = claim("insight-1")
    return PaperAnalysis(
        paper_id="arxiv:2401.00001",
        english_title="Synthetic Paper",
        chinese_title=claim("title-1", kind="title"),
        recommendation_reason=claim("recommendation-1", kind="recommendation"),
        research_problem=claim("problem-1", kind="problem"),
        insights=(insight,),
        supporting_visuals=(supporting_visual(),),
        insight_formation_logic=claim("logic-1", kind="insight_logic"),
        method_overview=claim("method-1", kind="method"),
        method_modules=(
            MethodModule(
                name="Synthetic module",
                purpose=claim("module-1", kind="method"),
            ),
        ),
        differences_from_prior_work=claim("difference-1", kind="difference"),
        parameters=(),
        ablations=(),
        experimental_conclusions=(claim("result-1", kind="result"),),
        limitations=(),
        links=PaperLinks(
            pdf_url="https://arxiv.org/pdf/2401.00001",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            code_url=None,
        ),
        evidence_candidates=(evidence(),),
        generation=GenerationMetadata(
            model_identity="fake:stage3",
            prompt_version="stage3-v1",
            schema_version="1.0",
            cache_key="a" * 64,
            generated_at=NOW,
        ),
    )


def test_claim_source_requires_matching_inferred_flag():
    with pytest.raises(ValidationError, match="system_inference"):
        claim(source_type="system_inference", inferred=False)
    with pytest.raises(ValidationError, match="system_inference"):
        claim(source_type="system_summary", inferred=True)
    inferred = claim(source_type="system_inference", inferred=True)
    assert inferred.inferred is True


def test_models_reject_unknown_fields_and_blank_generated_text():
    payload = claim().model_dump()
    payload["unexpected"] = "not allowed"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ClaimRecord.model_validate(payload)
    with pytest.raises(ValidationError, match="blank"):
        ClaimRecord.model_validate({**claim().model_dump(), "text_zh": "  "})


def test_analysis_field_order_and_explicit_missing_values():
    data = analysis().model_dump()
    assert list(data)[:7] == [
        "schema_version",
        "paper_id",
        "english_title",
        "chinese_title",
        "recommendation_reason",
        "research_problem",
        "insights",
    ]
    assert data["parameters"] == ()
    assert data["limitations"] == ()
    assert data["links"]["code_url"] is None


def test_visual_projection_preserves_stage_two_provenance():
    visual = supporting_visual()
    assert visual.regions[0].source_mapping.source_item_id == "docling-table"
    assert visual.regions[0].pdf_page == 2
    assert visual.label == "Table 1"
    assert visual.image_paths == (Path("cache/documents/evidence/table-1.png"),)


def test_evidence_packet_requires_unique_matching_candidates_and_fingerprint():
    packet = EvidencePacket(
        paper_id="arxiv:2401.00001",
        document_fingerprint="b" * 64,
        builder_version="1",
        candidates=(evidence(),),
        packet_fingerprint="c" * 64,
    )
    assert packet.candidates[0].paper_id == packet.paper_id

    with pytest.raises(ValidationError, match="unique"):
        EvidencePacket.model_validate(
            {**packet.model_dump(), "candidates": (evidence(), evidence())}
        )
    with pytest.raises(ValidationError, match="paper_id"):
        EvidencePacket.model_validate(
            {
                **packet.model_dump(),
                "candidates": (
                    evidence().model_copy(update={"paper_id": "arxiv:2401.99999"}),
                ),
            }
        )


def test_parameter_and_ablation_records_preserve_missing_and_linkage():
    parameter = ParameterRecord(
        name="cache interval",
        symbol=None,
        role=claim("parameter-role", kind="parameter"),
        final_value=None,
        selection_method=None,
        per_model_tuning=None,
        evidence_ids=("ev-table",),
        ablation_ids=("ablation-1",),
        source_type="author_statement",
        inferred=False,
        confidence=0.7,
    )
    ablation = AblationRecord(
        ablation_id="ablation-1",
        parameter_names=(parameter.name,),
        conclusion=claim("ablation-claim", kind="ablation"),
        visual_evidence_ids=("ev-table",),
    )
    assert parameter.final_value is None
    assert ablation.parameter_names == ("cache interval",)


def test_result_status_relationships_and_batch_limit_are_strict():
    success = PaperAnalysisResult(
        paper_id="arxiv:2401.00001",
        status="success",
        analysis=analysis(),
        processing_seconds=0.1,
        cache_hit=False,
    )
    assert success.analysis is not None

    with pytest.raises(ValidationError, match="require an analysis"):
        PaperAnalysisResult(
            paper_id="arxiv:2401.00001",
            status="success",
            analysis=None,
            processing_seconds=0,
        )
    with pytest.raises(ValidationError, match="must not contain"):
        PaperAnalysisResult(
            paper_id="arxiv:2401.00001",
            status="failed",
            analysis=analysis(),
            processing_seconds=0,
        )

    items = tuple(
        PaperAnalysisResult(
            paper_id=f"arxiv:2401.{index:05d}",
            status="skipped",
            analysis=None,
            processing_seconds=0,
        )
        for index in range(6)
    )
    with pytest.raises(ValidationError, match="at most five"):
        AnalysisBatchResult(
            run_id="run-1",
            created_at=NOW,
            results=items,
            expensive_call_count=0,
            cache_hit_count=0,
        )
