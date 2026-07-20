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

