from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from zotero_arxiv_daily.analysis.paper_schemas import (
    AblationRecord,
    ClaimRecord,
    GenerationMetadata,
    MethodModule,
    PaperAnalysis,
    PaperAnalysisResult,
    PaperLinks,
    ParameterRecord,
    SupportingVisual,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper
from zotero_arxiv_daily.documents.evidence import build_evidence_packet
from tests.analysis.test_prompt import candidate
from tests.documents.test_evidence import document_graph, settings


NOW = datetime(2026, 7, 20, tzinfo=UTC)


class GoldenInputs(NamedTuple):
    candidate: CandidatePaper
    document: object
    packet: object
    analysis_result: PaperAnalysisResult


def claim(
    claim_id: str,
    kind: str,
    evidence_ids: tuple[str, ...],
    *,
    source_type: str = "system_summary",
    inferred: bool = False,
) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        kind=kind,
        text_zh="人工合成证据支持该结论。",
        source_type=source_type,
        inferred=inferred,
        evidence_ids=evidence_ids,
        confidence=0.8,
    )


def golden_inputs() -> GoldenInputs:
    paper = candidate()
    document = document_graph()
    packet = build_evidence_packet(paper.paper_id, document, settings())
    text = next(
        item for item in packet.candidates if item.kind == "text" and not item.abstract_only
    )
    visual = next(item for item in packet.candidates if item.kind == "table")
    insight = claim("insight-1", "insight", (text.evidence_id, visual.evidence_id))
    support = claim("support-1", "support", (visual.evidence_id,))
    ablation_conclusion = claim(
        "ablation-claim", "ablation", (visual.evidence_id,)
    )
    analysis = PaperAnalysis(
        paper_id=paper.paper_id,
        english_title=paper.title,
        chinese_title=claim("title-1", "title", (text.evidence_id,)),
        recommendation_reason=claim(
            "recommendation-1", "recommendation", (text.evidence_id,)
        ),
        research_problem=claim("problem-1", "problem", (text.evidence_id,)),
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
        insight_formation_logic=claim(
            "logic-1", "insight_logic", (text.evidence_id, visual.evidence_id)
        ),
        method_overview=claim("method-1", "method", (text.evidence_id,)),
        method_modules=(
            MethodModule(
                name="人工模块",
                purpose=claim("module-1", "method", (text.evidence_id,)),
            ),
        ),
        differences_from_prior_work=claim(
            "difference-1", "difference", (text.evidence_id,)
        ),
        parameters=(
            ParameterRecord(
                name="cache interval",
                symbol=None,
                role=claim("parameter-role", "parameter", (text.evidence_id,)),
                final_value=None,
                selection_method=None,
                per_model_tuning=None,
                evidence_ids=(text.evidence_id,),
                ablation_ids=("ablation-1",),
                source_type="author_statement",
                inferred=False,
                confidence=0.7,
            ),
        ),
        ablations=(
            AblationRecord(
                ablation_id="ablation-1",
                parameter_names=("cache interval",),
                conclusion=ablation_conclusion,
                visual_evidence_ids=(visual.evidence_id,),
            ),
        ),
        experimental_conclusions=(
            claim("result-1", "result", (visual.evidence_id,)),
        ),
        limitations=(),
        links=PaperLinks(
            pdf_url=paper.pdf_url,
            arxiv_url=paper.arxiv_url,
            code_url=paper.code_url,
        ),
        evidence_candidates=packet.candidates,
        generation=GenerationMetadata(
            model_identity="fake:stage3",
            prompt_version="stage3-v1",
            schema_version="1.0",
            cache_key="d" * 64,
            generated_at=NOW,
        ),
    )
    return GoldenInputs(
        paper,
        document,
        packet,
        PaperAnalysisResult(
            paper_id=paper.paper_id,
            status="success",
            analysis=analysis,
            processing_seconds=0,
        ),
    )


def replace_analysis(inputs: GoldenInputs, analysis: PaperAnalysis) -> GoldenInputs:
    return inputs._replace(
        analysis_result=inputs.analysis_result.model_copy(update={"analysis": analysis})
    )


def mutate_analysis_title(inputs: GoldenInputs) -> GoldenInputs:
    analysis = inputs.analysis_result.analysis.model_copy(
        update={"english_title": "Tampered title"}
    )
    return replace_analysis(inputs, analysis)


def mutate_link(inputs: GoldenInputs, field: str) -> GoldenInputs:
    analysis = inputs.analysis_result.analysis
    links = analysis.links.model_copy(
        update={field: "https://example.test/tampered"}
    )
    return replace_analysis(inputs, analysis.model_copy(update={"links": links}))


def mutate_saved_evidence_candidate(inputs: GoldenInputs) -> GoldenInputs:
    analysis = inputs.analysis_result.analysis
    first = analysis.evidence_candidates[0].model_copy(update={"confidence": 0.01})
    changed = (first, *analysis.evidence_candidates[1:])
    return replace_analysis(
        inputs, analysis.model_copy(update={"evidence_candidates": changed})
    )


def mutate_packet_document_fingerprint(inputs: GoldenInputs) -> GoldenInputs:
    return inputs._replace(
        packet=inputs.packet.model_copy(update={"document_fingerprint": "e" * 64})
    )


def mutate_visual_id(inputs: GoldenInputs) -> GoldenInputs:
    candidates = list(inputs.packet.candidates)
    index = next(index for index, item in enumerate(candidates) if item.kind != "text")
    original = candidates[index]
    changed_candidate = original.model_copy(update={"visual_id": "table-missing"})
    candidates[index] = changed_candidate
    packet = inputs.packet.model_copy(update={"candidates": tuple(candidates)})
    analysis = inputs.analysis_result.analysis
    analysis_candidates = tuple(
        changed_candidate if item.evidence_id == original.evidence_id else item
        for item in analysis.evidence_candidates
    )
    supporting = analysis.supporting_visuals[0].model_copy(
        update={"visual_id": "table-missing"}
    )
    changed_analysis = analysis.model_copy(
        update={
            "evidence_candidates": analysis_candidates,
            "supporting_visuals": (supporting,),
        }
    )
    return GoldenInputs(
        inputs.candidate,
        inputs.document,
        packet,
        inputs.analysis_result.model_copy(update={"analysis": changed_analysis}),
    )


def mutate_paper_id(inputs: GoldenInputs) -> GoldenInputs:
    return inputs._replace(
        packet=inputs.packet.model_copy(update={"paper_id": "arxiv:2401.99999"})
    )


def mutate_visual_provenance(inputs: GoldenInputs, field: str) -> GoldenInputs:
    candidates = list(inputs.packet.candidates)
    index = next(index for index, item in enumerate(candidates) if item.kind != "text")
    original = candidates[index]
    updates: dict[str, object]
    if field == "label":
        updates = {"label": "Table 999"}
    elif field == "caption":
        updates = {"caption": "Tampered caption"}
    elif field == "pdf_page":
        updates = {"pdf_page": 4}
    elif field == "section_id":
        updates = {"section_id": "section-method"}
    elif field == "section_title":
        updates = {"section_title": "3 Method"}
    elif field == "section_path":
        updates = {"section_path": ("3 Method",)}
    elif field == "confidence":
        updates = {"confidence": 0.1}
    else:
        region = original.regions[0]
        if field == "bbox":
            box = region.bbox.model_copy(update={"left": region.bbox.left + 1})
            mapping = region.source_mapping.model_copy(update={"bbox": box})
            changed_region = region.model_copy(update={"bbox": box, "source_mapping": mapping})
        elif field == "source_mapping":
            mapping = region.source_mapping.model_copy(
                update={"source_item_id": "tampered-source"}
            )
            changed_region = region.model_copy(update={"source_mapping": mapping})
        elif field == "image_path":
            changed_region = region.model_copy(
                update={"image_path": Path("cache/documents/evidence/replaced.png")}
            )
        else:
            raise AssertionError(f"unknown field {field}")
        updates = {"regions": (changed_region, *original.regions[1:])}
    changed_candidate = original.model_copy(update=updates)
    candidates[index] = changed_candidate
    packet = inputs.packet.model_copy(update={"candidates": tuple(candidates)})

    analysis = inputs.analysis_result.analysis
    analysis_candidates = tuple(
        changed_candidate if item.evidence_id == original.evidence_id else item
        for item in analysis.evidence_candidates
    )
    visual = analysis.supporting_visuals[0]
    visual_updates = {
        key: value
        for key, value in updates.items()
        if key
        in {
            "label",
            "caption",
            "pdf_page",
            "section_id",
            "section_title",
            "section_path",
            "regions",
            "confidence",
        }
    }
    changed_visual = visual.model_copy(update=visual_updates)
    changed_analysis = analysis.model_copy(
        update={
            "evidence_candidates": analysis_candidates,
            "supporting_visuals": (changed_visual,),
        }
    )
    return GoldenInputs(
        inputs.candidate,
        inputs.document,
        packet,
        inputs.analysis_result.model_copy(update={"analysis": changed_analysis}),
    )
