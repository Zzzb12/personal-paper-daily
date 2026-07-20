import json
from datetime import UTC, datetime

from zotero_arxiv_daily.analysis.analyzer import (
    AnalysisDependencies,
    AnalysisSettings,
    analyze_paper,
)
from zotero_arxiv_daily.analysis.cache import AnalysisCache
from zotero_arxiv_daily.analysis.client import AnalysisClientError
from zotero_arxiv_daily.analysis.paper_schemas import (
    AblationRecord,
    ClaimRecord,
    DraftSupportingVisual,
    MethodModule,
    PaperAnalysisDraft,
    PaperLinks,
    ParameterRecord,
)
from zotero_arxiv_daily.documents.evidence import build_evidence_packet
from tests.analysis.test_prompt import candidate
from tests.documents.test_evidence import PAPER_ID, document_graph, settings as evidence_settings


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def claim(claim_id, kind, evidence_ids, text="合成中文结论"):
    return ClaimRecord(
        claim_id=claim_id,
        kind=kind,
        text_zh=text,
        source_type="system_summary",
        inferred=False,
        evidence_ids=tuple(evidence_ids),
        confidence=0.8,
    )


def evidence_packet():
    return build_evidence_packet(PAPER_ID, document_graph(), evidence_settings())


def valid_draft():
    packet = evidence_packet()
    text = next(
        item for item in packet.candidates if item.kind == "text" and not item.abstract_only
    )
    visual = next(item for item in packet.candidates if item.kind != "text")
    insight = claim("insight-1", "insight", (text.evidence_id, visual.evidence_id))
    ablation_claim = claim("ablation-claim", "ablation", (visual.evidence_id,))
    return PaperAnalysisDraft(
        paper_id=PAPER_ID,
        english_title="Synthetic Paper",
        chinese_title=claim("title-1", "title", (text.evidence_id,)),
        recommendation_reason=claim(
            "recommendation-1", "recommendation", (text.evidence_id,)
        ),
        research_problem=claim("problem-1", "problem", (text.evidence_id,)),
        insights=(insight,),
        supporting_visuals=(
            DraftSupportingVisual(
                evidence_id=visual.evidence_id,
                insight_ids=(insight.claim_id,),
                support_explanation=claim(
                    "support-1", "support", (visual.evidence_id,)
                ),
            ),
        ),
        insight_formation_logic=claim(
            "logic-1", "insight_logic", (text.evidence_id, visual.evidence_id)
        ),
        method_overview=claim("method-1", "method", (text.evidence_id,)),
        method_modules=(
            MethodModule(
                name="Synthetic module",
                purpose=claim("module-1", "method", (text.evidence_id,)),
            ),
        ),
        differences_from_prior_work=claim(
            "difference-1", "difference", (text.evidence_id,)
        ),
        parameters=(
            ParameterRecord(
                name="cache interval",
                symbol="N",
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
                conclusion=ablation_claim,
                visual_evidence_ids=(visual.evidence_id,),
            ),
        ),
        experimental_conclusions=(
            claim("result-1", "result", (visual.evidence_id,)),
        ),
        limitations=(),
        links=PaperLinks(
            pdf_url="https://arxiv.org/pdf/2401.00001",
            arxiv_url="https://arxiv.org/abs/2401.00001",
            code_url=None,
        ),
    )


class FakeClient:
    model_identity = "fake:stage3"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def analyzer_settings(**updates):
    base = AnalysisSettings(
        evidence=evidence_settings(),
        prompt_version="stage3-v1",
        schema_version="1.0",
        config_version="1",
        max_output_tokens=4_000,
        max_attempts=3,
        backoff_seconds=1,
        max_retry_after_seconds=5,
    )
    return base.model_copy(update=updates)


def dependencies(tmp_path, *responses):
    client = FakeClient(*responses)
    sleeps = []
    return AnalysisDependencies(
        client=client,
        cache=AnalysisCache(tmp_path / "cache"),
        clock=lambda: NOW,
        sleep=sleeps.append,
    ), sleeps


def abstract_only_graph():
    graph = document_graph(visual_count=0)
    abstract = next(section for section in graph.sections if section.title == "Abstract")
    block_ids = set(abstract.block_ids)
    blocks = tuple(block for block in graph.blocks if block.block_id in block_ids)
    pages = tuple(
        page.model_copy(
            update={
                "block_ids": tuple(
                    block_id for block_id in page.block_ids if block_id in block_ids
                )
            }
        )
        for page in graph.pages
    )
    return graph.model_copy(
        update={"blocks": blocks, "sections": (abstract,), "pages": pages, "visuals": ()}
    )


def test_analyzer_materializes_visual_provenance_from_packet_not_llm(tmp_path):
    deps, _ = dependencies(tmp_path, valid_draft().model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "success"
    visual = result.analysis.supporting_visuals[0]
    source = next(
        item for item in evidence_packet().candidates if item.evidence_id == visual.evidence_id
    )
    assert visual.label == source.label == "Table 1"
    assert visual.regions == source.regions
    assert visual.regions[0].source_mapping.source_item_id == "visual-0-p3"
    assert result.analysis.evidence_candidates == evidence_packet().candidates


def test_analyzer_rejects_unknown_evidence_without_echoing_response(tmp_path):
    draft = valid_draft()
    bad_insight = draft.insights[0].model_copy(
        update={"evidence_ids": ("SECRET_UNKNOWN_EVIDENCE",)}
    )
    draft = draft.model_copy(update={"insights": (bad_insight,)})
    deps, _ = dependencies(tmp_path, draft.model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "failed"
    assert result.issues[0].code == "analysis_unknown_evidence"
    assert "SECRET_UNKNOWN_EVIDENCE" not in result.issues[0].message


def test_analyzer_rejects_abstract_only_insight(tmp_path):
    draft = valid_draft()
    abstract_id = next(
        item.evidence_id for item in evidence_packet().candidates if item.abstract_only
    )
    insight = draft.insights[0].model_copy(update={"evidence_ids": (abstract_id,)})
    draft = draft.model_copy(update={"insights": (insight,)})
    deps, _ = dependencies(tmp_path, draft.model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "failed"
    assert result.issues[0].code == "analysis_abstract_only_insight"


def test_analyzer_does_not_call_client_for_an_abstract_only_document(tmp_path):
    deps, _ = dependencies(tmp_path, valid_draft().model_dump_json())
    result = analyze_paper(candidate(), abstract_only_graph(), analyzer_settings(), deps)
    assert result.status == "failed"
    assert result.issues[0].code == "evidence_packet_non_abstract_empty"
    assert deps.client.calls == 0


def test_analyzer_marks_an_explicitly_missing_insight_as_partial_without_caching_it(tmp_path):
    draft = valid_draft().model_copy(update={"insights": (), "supporting_visuals": ()})
    deps, _ = dependencies(tmp_path, draft.model_dump_json(), draft.model_dump_json())
    first = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    second = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert first.status == second.status == "partial"
    assert first.issues[0].code == "analysis_insight_not_provided"
    assert second.cache_hit is False
    assert deps.client.calls == 2


def test_analyzer_rejects_claim_kind_in_the_wrong_output_field(tmp_path):
    payload = json.loads(valid_draft().model_dump_json())
    payload["insights"][0]["kind"] = "result"
    deps, _ = dependencies(tmp_path, json.dumps(payload))
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "failed"
    assert result.issues[0].code == "analysis_schema_invalid"


def test_analyzer_rejects_non_visual_ablation_and_metadata_tampering(tmp_path):
    draft = valid_draft()
    text_id = next(
        item.evidence_id for item in evidence_packet().candidates if item.kind == "text"
    )
    ablation = draft.ablations[0].model_copy(
        update={"visual_evidence_ids": (text_id,)}
    )
    draft = draft.model_copy(update={"ablations": (ablation,)})
    deps, _ = dependencies(tmp_path, draft.model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.issues[0].code == "analysis_ablation_requires_visual"

    tampered = valid_draft().model_copy(update={"english_title": "Tampered Title"})
    deps, _ = dependencies(tmp_path / "other", tampered.model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.issues[0].code == "analysis_metadata_mismatch"


def test_analyzer_bounds_retry_after_and_succeeds_on_transient_retry(tmp_path):
    transient = AnalysisClientError(
        "analysis_transient_error", retryable=True, retry_after_seconds=99
    )
    deps, sleeps = dependencies(tmp_path, transient, valid_draft().model_dump_json())
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "success"
    assert deps.client.calls == 2
    assert sleeps == [5]


def test_analyzer_does_not_retry_permanent_errors(tmp_path):
    permanent = AnalysisClientError("analysis_auth_failed", retryable=False)
    deps, sleeps = dependencies(tmp_path, permanent)
    result = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert result.status == "failed"
    assert result.issues[0].code == "analysis_auth_failed"
    assert deps.client.calls == 1
    assert sleeps == []


def test_analyzer_reports_malformed_and_schema_errors_without_raw_content(tmp_path):
    deps, _ = dependencies(tmp_path, "SECRET_RAW_NOT_JSON")
    malformed = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert malformed.issues[0].code == "analysis_malformed_json"
    assert "SECRET_RAW_NOT_JSON" not in malformed.issues[0].message

    deps, _ = dependencies(tmp_path / "schema", '{"paper_id":"SECRET_BAD_SCHEMA"}')
    invalid = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert invalid.issues[0].code == "analysis_schema_invalid"
    assert "SECRET_BAD_SCHEMA" not in invalid.issues[0].message


def test_identical_second_analysis_is_cache_hit_with_zero_duplicate_call(tmp_path):
    deps, _ = dependencies(tmp_path, valid_draft().model_dump_json())
    first = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    second = analyze_paper(candidate(), document_graph(), analyzer_settings(), deps)
    assert first.status == second.status == "success"
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert deps.client.calls == 1
    assert first.analysis == second.analysis


def test_changed_title_and_links_cannot_reuse_stale_cached_analysis(tmp_path):
    original = candidate()
    changed = original.model_copy(
        update={"title": "Updated Synthetic Paper", "code_url": "https://example.test/code"}
    )
    changed_draft = valid_draft().model_copy(
        update={
            "english_title": changed.title,
            "links": PaperLinks(
                pdf_url=changed.pdf_url,
                arxiv_url=changed.arxiv_url,
                code_url=changed.code_url,
            ),
        }
    )
    deps, _ = dependencies(
        tmp_path,
        valid_draft().model_dump_json(),
        changed_draft.model_dump_json(),
    )
    first = analyze_paper(original, document_graph(), analyzer_settings(), deps)
    second = analyze_paper(changed, document_graph(), analyzer_settings(), deps)
    assert first.status == second.status == "success"
    assert second.cache_hit is False
    assert second.analysis.english_title == changed.title
    assert second.analysis.links.code_url == changed.code_url
    assert deps.client.calls == 2
