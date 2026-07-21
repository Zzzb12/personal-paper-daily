from __future__ import annotations

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


def test_empty_index_has_a_readable_empty_state() -> None:
    html = TemplateRenderer(site_title="Paper Daily").render_index(
        IndexPageModel(batch_label="2026-07-21", valid_count=0, partial_count=0, papers=())
    )

    assert "今日没有可发布的论文" in html


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
    assert "PDF page" in html and "confidence" in html
    assert "论文未明确提供" in html
