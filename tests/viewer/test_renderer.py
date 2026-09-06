from __future__ import annotations

import re

from zotero_arxiv_daily.viewer.renderer import TemplateRenderer
from zotero_arxiv_daily.viewer.schemas import IndexPageModel, PaperPageModel


def test_index_page_is_semantic_escaped_and_has_meaningful_status() -> None:
    page = IndexPageModel(
        batch_label="2026-07-21",
        valid_count=1,
        partial_count=0,
        papers=(
            PaperPageModel(
                paper_id="arxiv:2401.00001",
                relative_path="papers/2401.00001.html",
                english_title="<script>unsafe</script>",
                chinese_title=None,
                publication_kind="full",
            ),
        ),
    )

    html = TemplateRenderer(site_title="Paper Daily").render_index(page)

    assert "<!doctype html>" in html.lower()
    assert "<nav" in html and "<main" in html
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in html
    assert "<script>unsafe</script>" not in html
    assert "已验证完整阅读" in html
    assert 'href="papers/2401.00001.html"' in html
    assert 'rel="icon" href="assets/favicon.svg"' in html


def test_index_exposes_stateless_accessible_feedback_controls_for_eligible_papers() -> None:
    page = IndexPageModel(
        batch_label="2026-07-22",
        valid_count=1,
        partial_count=0,
        papers=(
            PaperPageModel(
                paper_id="ARXIV:2401.00001v3",
                relative_path="papers/2401.00001.html",
                english_title="Feedback-safe paper",
                chinese_title="反馈安全论文",
                publication_kind="full",
            ),
        ),
    )

    html = TemplateRenderer(site_title="Paper Daily").render_index(page)

    assert page.papers[0].paper_id == "2401.00001"
    assert '<article class="paper-card" data-paper-id="2401.00001" tabindex="0">' in html
    assert html.count('aria-pressed="false"') == 3
    assert 'data-ai-action="set-read"' in html
    assert 'data-ai-action="set-favorite"' in html
    assert 'data-ai-action="set-irrelevant"' in html
    assert 'data-testid="filter-unread"' in html
    assert 'data-testid="filter-favorite"' in html
    assert 'data-testid="filter-show-irrelevant"' in html
    assert 'data-ai-action="export-feedback"' in html
    assert 'data-ai-action="import-feedback"' in html
    assert 'aria-live="polite"' in html
    assert '<script src="assets/feedback.js" defer></script>' in html
    assert "script-src 'self'" in html
    assert "'unsafe-inline'" not in html
    assert "'unsafe-eval'" not in html
    workflow_url = "https://github.com/Zzzb12/personal-paper-daily/actions/workflows/personal-paper-daily.yml"
    manual_trigger = re.search(
        rf'<a\b([^>]*href="{re.escape(workflow_url)}"[^>]*)>手动触发</a>', html
    )
    assert manual_trigger is not None
    assert "target=" not in manual_trigger.group(1)
    assert 'aria-describedby="manual-trigger-help"' in manual_trigger.group(1)
    assert manual_trigger.start() < html.index("</header>")
    assert 'id="manual-trigger-help">前往 GitHub，选择是否进行真实分析和发送飞书后运行。</p>' in html
    assert re.findall(r'https://[^"<>\s]+', html) == [workflow_url]
    assert "localStorage" not in html
    assert "feedback-v1.json" not in html


def test_empty_index_has_a_readable_empty_state() -> None:
    html = TemplateRenderer(site_title="Paper Daily").render_index(
        IndexPageModel(batch_label="2026-07-21", valid_count=0, partial_count=0, papers=())
    )

    assert "今日没有可发布的论文" in html
    assert 'data-ai-action="export-feedback"' in html
    assert 'data-ai-action="import-feedback"' in html
    assert 'data-paper-id=' not in html
    assert 'data-ai-action="set-read"' not in html


def test_detail_page_does_not_render_https_link_with_embedded_credentials() -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    analysis = result.validated.analysis.model_copy(
        update={"links": result.validated.analysis.links.model_copy(update={"code_url": "https://user:secret@example.test/code"})}
    )

    html = TemplateRenderer(site_title="Paper Daily").render_paper(analysis, result.validated.report, publication_kind="full")

    assert "user:secret" not in html


def test_detail_page_uses_validated_analysis_in_fixed_reading_order() -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    html = TemplateRenderer(site_title="Paper Daily").render_paper(
        result.validated.analysis,
        result.validated.report,
        publication_kind="full",
    )

    assert "返回论文索引" in html
    assert "推荐理由" in html and "研究问题" in html
    assert "核心 Insight" in html and "Insight 形成逻辑" in html
    assert html.index("核心 Insight") < html.index("Method") < html.index("关键参数")
    assert "<span>PDF " in html and "<span>CONF " in html
    assert "论文未明确提供" not in html
    assert 'rel="icon" href="../assets/favicon.svg"' in html
    assert 'class="skip-link"' in html
    assert 'class="detail-shell"' in html
    assert 'class="reading-rail"' in html
    assert 'id="insights"' in html
    assert 'id="method"' in html
    assert 'id="experiments"' in html


def test_detail_page_consolidates_missing_content_in_one_coverage_panel() -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    analysis = result.validated.analysis.model_copy(
        update={
            "recommendation_reason": None,
            "supporting_visuals": (),
            "parameters": (),
            "ablations": (),
            "limitations": (),
        }
    )

    html = TemplateRenderer(site_title="Paper Daily").render_paper(
        analysis,
        result.validated.report,
        publication_kind="full",
    )

    assert "论文未明确提供" not in html
    assert html.count('class="coverage-panel"') == 1
    assert 'data-missing-field="recommendation"' in html
    assert 'data-missing-field="parameters"' in html
    assert 'data-missing-field="ablations"' in html
    assert 'data-missing-field="limitations"' in html
    assert "<h2>关键参数</h2>" not in html
    assert "<h2>参数对应的消融实验</h2>" not in html
    section_numbers = re.findall(
        r'<span class="section-index">(\d{2})</span>',
        html,
    )
    assert section_numbers == [
        f"{index:02d}" for index in range(1, len(section_numbers) + 1)
    ]


def test_detail_page_can_mark_read_without_embedding_private_feedback() -> None:
    from tests.analysis.stage4_factories import golden_inputs
    from zotero_arxiv_daily.analysis.validator import validate_paper

    result = validate_paper(*golden_inputs())
    assert result.validated is not None
    analysis = result.validated.analysis.model_copy(update={"paper_id": "arxiv:2401.00001v2"})

    html = TemplateRenderer(site_title="Paper Daily").render_paper(
        analysis,
        result.validated.report,
        publication_kind="full",
    )

    assert 'data-paper-id="2401.00001"' in html
    assert 'data-ai-action="set-read"' in html
    assert html.count('aria-pressed="false"') == 1
    assert '<script src="../assets/feedback.js" defer></script>' in html
    assert "script-src 'self'" in html
    assert "localStorage" not in html
    assert "feedback-v1.json" not in html
