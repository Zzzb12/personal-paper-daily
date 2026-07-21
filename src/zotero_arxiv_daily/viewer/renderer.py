from __future__ import annotations

from html import escape
from urllib.parse import urlparse

from zotero_arxiv_daily.analysis.paper_schemas import ClaimRecord, PaperAnalysis
from zotero_arxiv_daily.analysis.validation_schemas import ValidationReport
from zotero_arxiv_daily.viewer.schemas import IndexPageModel, PaperPageModel


class TemplateRenderer:
    def __init__(self, *, site_title: str) -> None:
        self._site_title = site_title

    def render_index(self, page: IndexPageModel) -> str:
        title = escape(self._site_title, quote=True)
        cards = "\n".join(self._render_card(item) for item in page.papers)
        if not cards:
            cards = '<p class="empty-state">今日没有可发布的论文</p>'
        return f"""<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self'; style-src 'self'; script-src 'none'">
  <title>{title}</title>
  <link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="assets/site.css">
</head>
<body>
  <header><p class="eyebrow">{escape(page.batch_label, quote=True)}</p><h1>{title}</h1></header>
  <nav aria-label="页面导航"><a href="#papers">论文索引</a></nav>
  <main id="papers">
    <p class="summary">完整阅读 {page.valid_count} 篇；部分验证 {page.partial_count} 篇。</p>
    <section aria-label="论文列表">{cards}</section>
  </main>
</body>
</html>
"""

    def render_paper(
        self,
        analysis: PaperAnalysis,
        report: ValidationReport,
        *,
        publication_kind: str,
    ) -> str:
        title = escape(analysis.english_title, quote=True)
        warning = "" if publication_kind == "full" else '<p class="status">部分内容未通过或无法完成验证</p>'
        visuals = "".join(
            f"<figure><div class=\"evidence-placeholder\">{escape(visual.kind)} {escape(visual.label or '论文未明确提供')}</div>"
            f"<figcaption>{escape(visual.caption or '论文未明确提供')}<br>PDF page {visual.pdf_page} · "
            f"{escape(visual.section_title or '论文未明确提供')} · confidence {visual.confidence:.2f}<br>"
            f"{self._claim(visual.support_explanation)}</figcaption></figure>"
            for visual in analysis.supporting_visuals
        ) or '<p>论文未明确提供</p>'
        modules = "".join(f"<li><strong>{escape(module.name)}</strong>：{self._claim(module.purpose)}</li>" for module in analysis.method_modules) or "<li>论文未明确提供</li>"
        parameters = "".join(
            f"<tr><td>{escape(item.name)}</td><td>{escape(item.symbol or '论文未明确提供')}</td><td>{self._claim(item.role)}</td><td>{escape(item.final_value or '论文未明确提供')}</td></tr>"
            for item in analysis.parameters
        ) or '<tr><td colspan="4">论文未明确提供</td></tr>'
        ablations = "".join(f"<li>{self._claim(item.conclusion)}</li>" for item in analysis.ablations) or "<li>论文未明确提供</li>"
        results = self._claims(analysis.experimental_conclusions)
        limitations = self._claims(analysis.limitations)
        issues = "".join(f"<li>{escape(issue.code)}：{escape(issue.message)}</li>" for issue in report.issues)
        return f"""<!doctype html>
<html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self'; style-src 'self'; script-src 'none'"><title>{title}</title><link rel="stylesheet" href="../assets/site.css"></head>
<body><header><a href="../index.html">返回论文索引</a><h1>{title}</h1><p>{self._claim(analysis.chinese_title)}</p>{warning}</header>
<main><article><section><h2>推荐理由</h2><p>{self._claim(analysis.recommendation_reason)}</p><h2>研究问题</h2><p>{self._claim(analysis.research_problem)}</p></section>
<section><h2>核心 Insight</h2>{self._claims(analysis.insights)}<h2>Insight 形成逻辑</h2><p>{self._claim(analysis.insight_formation_logic)}</p><h2>证据图表</h2>{visuals}</section>
<section><h2>Method</h2><p>{self._claim(analysis.method_overview)}</p><ul>{modules}</ul><h2>与已有工作的区别</h2><p>{self._claim(analysis.differences_from_prior_work)}</p></section>
<section><h2>关键参数</h2><table><caption>参数符号、作用和最终取值</caption><thead><tr><th>名称</th><th>符号</th><th>作用</th><th>最终取值</th></tr></thead><tbody>{parameters}</tbody></table><h2>参数对应的消融实验</h2><ul>{ablations}</ul></section>
<section><h2>主要实验结论</h2>{results}<h2>作者明确说明的局限性</h2>{limitations}<h2>链接</h2>{self._links(analysis)}</section>
{f'<aside><h2>验证问题</h2><ul>{issues}</ul></aside>' if issues else ''}</article></main></body></html>"""

    @staticmethod
    def _claim(claim: ClaimRecord | None) -> str:
        if claim is None:
            return "论文未明确提供"
        label = "系统推断，非作者直接陈述" if claim.inferred else {"author_statement": "作者陈述", "system_summary": "系统总结"}[claim.source_type]
        return f'<span class="claim-source">{label}</span> {escape(claim.text_zh, quote=True)}'

    def _claims(self, claims: tuple[ClaimRecord, ...]) -> str:
        return "".join(f"<p>{self._claim(claim)}</p>" for claim in claims) or "<p>论文未明确提供</p>"

    @staticmethod
    def _links(analysis: PaperAnalysis) -> str:
        links = (("PDF", analysis.links.pdf_url), ("arXiv", analysis.links.arxiv_url), ("代码", analysis.links.code_url))
        safe = [f'<a href="{escape(url, quote=True)}" rel="noopener noreferrer">{label}</a>' for label, url in links if url and urlparse(url).scheme == "https"]
        return " · ".join(safe) or "论文未明确提供"

    @staticmethod
    def _render_card(paper: PaperPageModel) -> str:
        status = "已验证完整阅读" if paper.publication_kind == "full" else "部分内容未通过或无法完成验证"
        chinese_title = paper.chinese_title or "论文未明确提供"
        return f"""<article class="paper-card">
  <p class="status">{status}</p>
  <h2><a href="{escape(paper.relative_path, quote=True)}">{escape(paper.english_title, quote=True)}</a></h2>
  <p lang="zh-Hans">{escape(chinese_title, quote=True)}</p>
</article>"""
