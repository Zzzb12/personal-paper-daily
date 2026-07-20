import json
from datetime import UTC, datetime

from zotero_arxiv_daily.analysis.prompts.stage3_v1 import (
    PROMPT_VERSION,
    build_analysis_request,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper
from zotero_arxiv_daily.documents.evidence import build_evidence_packet
from tests.documents.test_evidence import PAPER_ID, document_graph, settings


def candidate():
    return CandidatePaper(
        paper_id=PAPER_ID,
        arxiv_id="2401.00001",
        version=1,
        title="Synthetic Paper",
        authors=("Synthetic Author",),
        abstract="ABSTRACT_SENTINEL_MUST_NOT_ENTER_STAGE3_PROMPT",
        categories=("cs.CV",),
        primary_category="cs.CV",
        published_at=datetime(2026, 7, 19, tzinfo=UTC),
        updated_at=datetime(2026, 7, 19, tzinfo=UTC),
        arxiv_url="https://arxiv.org/abs/2401.00001",
        pdf_url="https://arxiv.org/pdf/2401.00001",
        code_url=None,
    )


def packet():
    return build_evidence_packet(PAPER_ID, document_graph(), settings())


def test_prompt_is_versioned_minimized_and_forbids_fabrication():
    request = build_analysis_request(candidate(), packet(), max_output_tokens=4_000)
    assert request.prompt_version == PROMPT_VERSION == "stage3-v1"
    assert "只能引用" in request.system_prompt
    assert "不得编造" in request.system_prompt
    assert "Figure/Table" in request.system_prompt
    assert "ABSTRACT_SENTINEL" not in request.user_prompt
    assert "collection_paths" not in request.user_prompt
    assert "ZOTERO_KEY" not in request.user_prompt
    assert "LLM_API_KEY" not in request.user_prompt
    assert "Synthetic Paper" in request.user_prompt
    assert packet().candidates[0].evidence_id in request.user_prompt


def test_prompt_contains_strict_draft_schema_and_canonical_links_only():
    request = build_analysis_request(candidate(), packet(), max_output_tokens=4_000)
    assert request.response_schema["additionalProperties"] is False
    assert "supporting_visuals" in request.response_schema["properties"]
    assert "https://arxiv.org/abs/2401.00001" in request.user_prompt
    assert "https://arxiv.org/pdf/2401.00001" in request.user_prompt
    assert request.max_output_tokens == 4_000


def test_prompt_sends_the_strict_response_schema_to_json_object_only_models():
    request = build_analysis_request(candidate(), packet(), max_output_tokens=4_000)
    payload = json.loads(request.user_prompt)
    assert payload["output_json_schema"] == request.response_schema
    assert payload["output_json_schema"]["additionalProperties"] is False


def test_prompt_context_budget_cannot_be_bypassed_by_a_long_visual_caption():
    graph = document_graph(visual_count=1)
    long_visual = graph.visuals[0].model_copy(
        update={"caption": ("超长图注" * 3_000) + "CAPTION_SENTINEL_TAIL"}
    )
    graph = graph.model_copy(update={"visuals": (long_visual,)})
    constrained = settings(max_chars=256, max_block_chars=64, max_candidates=8)
    bounded = build_evidence_packet(PAPER_ID, graph, constrained)
    baseline = build_evidence_packet(
        PAPER_ID, document_graph(visual_count=1), constrained
    )
    request = build_analysis_request(candidate(), bounded, max_output_tokens=4_000)
    baseline_request = build_analysis_request(
        candidate(), baseline, max_output_tokens=4_000
    )
    assert "CAPTION_SENTINEL_TAIL" not in request.user_prompt
    assert len(request.user_prompt) <= len(baseline_request.user_prompt) + 256
