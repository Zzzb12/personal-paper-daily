from zotero_arxiv_daily.analysis.prompts.stage3_v2 import (
    PROMPT_VERSION,
    build_analysis_request,
)
from zotero_arxiv_daily.documents.evidence import build_evidence_packet
from tests.analysis.test_prompt import candidate
from tests.documents.test_evidence import document_graph, settings


def test_stage3_v2_includes_an_exact_minimal_json_example():
    paper = candidate()
    packet = build_evidence_packet(paper.paper_id, document_graph(), settings())

    request = build_analysis_request(paper, packet, max_output_tokens=4096)

    assert request.prompt_version == PROMPT_VERSION == "stage3-v2"
    assert '"minimal_valid_json_example"' in request.user_prompt
    assert f'"paper_id":"{paper.paper_id}"' in request.user_prompt
    assert f'"english_title":"{paper.title}"' in request.user_prompt
    assert '"supporting_visuals":[]' in request.user_prompt
