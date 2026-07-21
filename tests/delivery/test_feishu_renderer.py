from __future__ import annotations

import json

from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.analysis.validator import validate_paper
from zotero_arxiv_daily.delivery.feishu import DigestPolicy, FeishuRenderer
from zotero_arxiv_daily.delivery.schemas import DigestPaper, FeishuPayload
from tests.analysis.stage4_factories import golden_inputs


CHAT_ID = "oc_0123456789abcdef0123456789abcdef"
SITE_URL = "https://papers.example.test/daily/2026-07-21/"
MISSING = "论文未明确提供"


def _valid_result():
    return validate_paper(*golden_inputs())


def _batch(*results) -> ValidationBatchResult:
    return ValidationBatchResult.model_construct(
        schema_version="1.0",
        validator_version="stage4-v1",
        run_id="run-20260721",
        created_at=golden_inputs().analysis_result.analysis.generation.generated_at,
        results=results,
        cache_hit_count=0,
    )


def _renamed_result(index: int):
    result = _valid_result()
    paper_id = f"arxiv:2401.{index:05d}"
    analysis = result.validated.analysis.model_copy(
        update={
            "paper_id": paper_id,
            "english_title": f"Paper {index}",
            "chinese_title": result.validated.analysis.chinese_title.model_copy(
                update={"text_zh": f"论文 {index}"}
            ),
        }
    )
    report = result.report.model_copy(update={"paper_id": paper_id})
    validated = result.validated.model_copy(update={"analysis": analysis, "report": report})
    return result.model_copy(
        update={"paper_id": paper_id, "validated": validated, "report": report}
    )


def test_policy_uses_only_valid_eligible_stage4_results_and_renderer_orders_chinese_fields() -> None:
    valid = _valid_result()
    partial = valid.model_copy(
        update={
            "status": "partial",
            "report": valid.report.model_copy(
                update={"status": "partial", "publication_eligibility": "blocked"}
            ),
        }
    )
    invalid = valid.model_copy(
        update={
            "status": "invalid",
            "validated": None,
            "report": valid.report.model_copy(
                update={"status": "invalid", "publication_eligibility": "blocked"}
            ),
        }
    )

    request = DigestPolicy.build(_batch(valid, partial, invalid), chat_id=CHAT_ID, site_url=SITE_URL)
    card = json.loads(FeishuRenderer.render(request.payload))
    content = card["card"]["body"]["elements"][0]["content"]

    assert tuple(paper.paper_id for paper in request.payload.papers) == (valid.paper_id,)
    assert content.index("中文标题") < content.index("英文标题") < content.index("阅读链接")
    assert valid.validated.analysis.chinese_title.text_zh in content
    assert valid.validated.analysis.english_title in content
    assert SITE_URL in content
    assert "partial" not in content
    assert "invalid" not in content


def test_policy_caps_schema_bypassed_six_valid_results_at_five() -> None:
    request = DigestPolicy.build(
        _batch(*(_renamed_result(index) for index in range(1, 7))),
        chat_id=CHAT_ID,
        site_url=SITE_URL,
    )

    assert tuple(paper.paper_id for paper in request.payload.papers) == tuple(
        f"arxiv:2401.{index:05d}" for index in range(1, 6)
    )


def test_renderer_uses_controlled_fallback_for_missing_chinese_title() -> None:
    payload = FeishuPayload(
        chat_id=CHAT_ID,
        papers=(
            DigestPaper(
                paper_id="arxiv:2401.00001",
                english_title="English title",
                chinese_title=None,
                site_url=SITE_URL,
                validation_status="valid",
                publication_eligibility="eligible",
            ),
        ),
    )

    content = json.loads(FeishuRenderer.render(payload))["card"]["body"]["elements"][0]["content"]

    assert MISSING in content


def test_renderer_omits_unsafe_or_credential_bearing_links_from_schema_bypassed_payload() -> None:
    unsafe_papers = (
        DigestPaper.model_construct(
            paper_id="arxiv:2401.00001",
            english_title="Non HTTPS",
            chinese_title="非 HTTPS",
            site_url="http://papers.example.test/daily/",
            validation_status="valid",
            publication_eligibility="eligible",
        ),
        DigestPaper.model_construct(
            paper_id="arxiv:2401.00002",
            english_title="Credential bearing",
            chinese_title="带凭据链接",
            site_url="https://user:test-password@papers.example.test/daily/",
            validation_status="valid",
            publication_eligibility="eligible",
        ),
    )
    payload = FeishuPayload.model_construct(chat_id=CHAT_ID, papers=unsafe_papers)

    rendered = FeishuRenderer.render(payload)
    content = json.loads(rendered)["card"]["body"]["elements"][0]["content"]

    assert "http://papers.example.test/daily/" not in content
    assert "test-password" not in rendered
    assert "https://user:" not in content
    assert content.count(MISSING) >= 2
