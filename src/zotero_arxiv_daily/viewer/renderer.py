from __future__ import annotations

import re
from html import escape
from typing import Mapping
from urllib.parse import urlparse

from zotero_arxiv_daily.analysis.paper_schemas import (
    ClaimRecord,
    EvidenceCandidate,
    PaperAnalysis,
    SupportingVisual,
)
from zotero_arxiv_daily.analysis.validation_schemas import ValidationReport
from zotero_arxiv_daily.viewer.feedback import normalize_feedback_paper_id
from zotero_arxiv_daily.viewer.schemas import IndexPageModel, PaperPageModel


class TemplateRenderer:
    def __init__(self, *, site_title: str) -> None:
        self._site_title = site_title

    def render_index(self, page: IndexPageModel) -> str:
        title = escape(self._site_title, quote=True)
        cards = "\n".join(
            self._render_card(item, position=index)
            for index, item in enumerate(page.papers, start=1)
        )
        if not cards:
            cards = """<div class="empty-state">
  <span class="empty-mark" aria-hidden="true">∅</span>
  <div><h2>今日没有可发布的论文</h2><p>候选论文尚未通过完整的证据与发布资格检查。</p></div>
</div>"""
        return f"""<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'">
  <title>{title}</title>
  <link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="assets/site.css">
</head>
<body class="index-page">
  <a class="skip-link" href="#papers">跳到论文列表</a>
  <header class="masthead">
    <div class="masthead-bar">
      <a class="wordmark" href="index.html" aria-label="{title} 首页">PPD<span>／</span>DAILY</a>
      <p class="issue-date">{escape(page.batch_label, quote=True)}</p>
    </div>
    <div class="masthead-grid">
      <div>
        <p class="eyebrow">Evidence-first research briefing</p>
        <h1><span>Personal Paper</span><br>Daily</h1>
        <div class="manual-trigger">
          <a class="manual-trigger-link" href="https://github.com/Zzzb12/personal-paper-daily/actions/workflows/personal-paper-daily.yml" aria-describedby="manual-trigger-help">手动触发</a>
          <p id="manual-trigger-help">前往 GitHub，选择是否进行真实分析和发送飞书后运行。</p>
        </div>
      </div>
      <div class="masthead-dek">
        <div class="orbital-display" aria-hidden="true">
          <span class="orbit orbit--outer"></span>
          <span class="orbit orbit--inner"></span>
          <span class="orbit-node"></span>
          <strong>{len(page.papers):02d}</strong>
          <small>VERIFIED<br>PAPERS</small>
        </div>
        <p>从候选检索到证据核验，把今天真正值得读的论文压缩成一份可追溯的研究简报。</p>
        <dl class="issue-metrics">
          <div><dt>完整阅读</dt><dd>{page.valid_count:02d}</dd></div>
          <div><dt>部分验证</dt><dd>{page.partial_count:02d}</dd></div>
          <div><dt>本期收录</dt><dd>{len(page.papers):02d}</dd></div>
        </dl>
      </div>
    </div>
  </header>
  <nav class="top-nav" aria-label="页面导航">
    <a href="#papers">本期论文</a>
    <span aria-hidden="true">◆</span>
    <a href="#reading-desk">阅读反馈</a>
  </nav>
  <main id="papers" class="index-main">
    <section class="index-intro" aria-labelledby="index-heading">
      <div>
        <p class="section-number">ISSUE / {escape(page.batch_label, quote=True)}</p>
        <h2 id="index-heading">今日研究切片</h2>
      </div>
      <p>仅展示通过 Stage 4 publication eligibility 的内容。所有摘要、结论与图表都保留证据来源边界。</p>
    </section>
    {self._feedback_toolbar()}
    <section class="paper-grid" aria-label="论文列表">{cards}</section>
  </main>
  <footer class="site-footer"><p>Personal Paper Daily · Built for deliberate reading.</p></footer>
  <script src="assets/feedback.js" defer></script>
</body>
</html>
"""

    def render_paper(
        self,
        analysis: PaperAnalysis,
        report: ValidationReport,
        *,
        publication_kind: str,
        evidence_image_urls: Mapping[str, str] | None = None,
    ) -> str:
        title = escape(analysis.english_title, quote=True)
        image_urls = evidence_image_urls or {}
        normalized_paper_id = escape(
            normalize_feedback_paper_id(analysis.paper_id), quote=True
        )
        chinese_title = (
            escape(analysis.chinese_title.text_zh, quote=True)
            if analysis.chinese_title is not None
            else "中文标题未从证据中提取"
        )
        status_label = (
            "完整证据阅读"
            if publication_kind == "full"
            else "部分内容通过验证"
        )
        warning = (
            ""
            if publication_kind == "full"
            else '<p class="publication-warning">部分内容未通过或无法完成验证</p>'
        )

        overview = self._overview_section(analysis)
        insights = self._insights_section(analysis)
        visuals = self._visuals_section(analysis, image_urls)
        method = self._method_section(analysis)
        experiments = self._experiments_section(analysis)
        results = self._results_section(analysis)
        resources = self._resources_section(analysis)
        (
            overview,
            insights,
            visuals,
            method,
            experiments,
            results,
            resources,
        ) = self._number_present_sections(
            (overview, insights, visuals, method, experiments, results, resources)
        )
        coverage = self._coverage_panel(analysis, image_urls)
        issues = self._issues_panel(report)
        table_of_contents = self._table_of_contents(
            (
                ("overview", "先读结论", bool(overview)),
                ("insights", "核心 Insight", bool(insights)),
                ("evidence", "证据图表", bool(visuals)),
                ("method", "Method", bool(method)),
                ("experiments", "参数与实验", bool(experiments)),
                ("results", "实验结论", bool(results)),
                ("resources", "原文链接", True),
            )
        )
        return f"""<!doctype html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'">
  <title>{title}</title>
  <link rel="icon" href="../assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="../assets/site.css">
</head>
<body class="detail-page">
  <a class="skip-link" href="#article-content">跳到正文</a>
  <header class="paper-hero">
    <div class="paper-hero-nav">
      <a class="back-link" href="../index.html"><span aria-hidden="true">←</span> 返回论文索引</a>
      <span class="paper-id">{normalized_paper_id}</span>
    </div>
    <div class="paper-hero-grid">
      <div>
        <p class="eyebrow">Validated paper brief · {status_label}</p>
        <h1>{title}</h1>
        <p class="chinese-title">{chinese_title}</p>
        {warning}
      </div>
      <div class="hero-stamp" aria-label="验证状态">
        <span>STAGE</span><strong>04</strong><small>Publication<br>eligible</small>
      </div>
    </div>
    <div class="telemetry-line" aria-hidden="true">
      <span>PIPELINE / STAGE 04</span><i></i><span>EVIDENCE LOCKED</span><i></i><span>READER NODE ONLINE</span>
    </div>
  </header>
  <div class="detail-shell">
    <aside class="reading-rail" aria-label="阅读导航">
      <div class="rail-sticky">
        <p class="rail-label">READING MAP</p>
        {table_of_contents}
        <section class="detail-feedback" data-paper-id="{normalized_paper_id}" aria-labelledby="feedback-heading">
          <h2 id="feedback-heading">阅读状态</h2>
          {self._feedback_button("read", "标记已读", "R")}
          <p class="feedback-status" role="status" aria-live="polite" data-testid="feedback-status">反馈仅保存在当前浏览器。</p>
        </section>
      </div>
    </aside>
    <main id="article-content" class="article-content">
      <article>
        {overview}
        {insights}
        {visuals}
        {method}
        {experiments}
        {results}
        {resources}
        {coverage}
        {issues}
      </article>
    </main>
  </div>
  <footer class="site-footer"><a href="../index.html">继续浏览本期论文</a></footer>
  <script src="../assets/feedback.js" defer></script>
</body>
</html>"""

    def _overview_section(self, analysis: PaperAnalysis) -> str:
        cards: list[str] = []
        if analysis.recommendation_reason is not None:
            cards.append(
                self._summary_card(
                    "推荐理由",
                    "WHY IT MATTERS",
                    analysis.recommendation_reason,
                    "signal",
                )
            )
        if analysis.research_problem is not None:
            cards.append(
                self._summary_card(
                    "研究问题",
                    "THE QUESTION",
                    analysis.research_problem,
                    "night",
                )
            )
        if not cards:
            return ""
        return self._story_section(
            section_id="overview",
            number="01",
            kicker="Orientation",
            title="先读结论",
            content=f'<div class="summary-grid">{"".join(cards)}</div>',
        )

    def _insights_section(self, analysis: PaperAnalysis) -> str:
        insight_cards = "".join(
            f"""<article class="insight-card">
  <span class="insight-index">{index:02d}</span>
  <div>{self._claim(claim)}</div>
</article>"""
            for index, claim in enumerate(analysis.insights, start=1)
        )
        logic = ""
        if analysis.insight_formation_logic is not None:
            logic = f"""<div class="logic-strip">
  <p class="mini-label">HOW THE INSIGHT FORMS</p>
  <h3>Insight 形成逻辑</h3>
  {self._claim(analysis.insight_formation_logic)}
</div>"""
        if not insight_cards and not logic:
            return ""
        return self._story_section(
            section_id="insights",
            number="02",
            kicker="Core finding",
            title="核心 Insight",
            content=f'<div class="insight-stack">{insight_cards}</div>{logic}',
        )

    def _visuals_section(
        self,
        analysis: PaperAnalysis,
        image_urls: Mapping[str, str],
    ) -> str:
        figures = [
            self._supporting_visual(visual, image_urls.get(visual.evidence_id))
            for visual in analysis.supporting_visuals
        ]
        selected_ids = {visual.evidence_id for visual in analysis.supporting_visuals}
        context_candidates = [
            candidate
            for candidate in analysis.evidence_candidates
            if (
                candidate.kind != "text"
                and candidate.evidence_id not in selected_ids
                and image_urls.get(candidate.evidence_id)
            )
        ]
        figures.extend(
            self._context_visual(candidate, image_urls[candidate.evidence_id])
            for candidate in context_candidates
        )
        if not figures:
            return ""
        has_context = bool(context_candidates)
        note = (
            """<p class="context-note"><strong>图表摘录说明：</strong>
部分图表已完成 PDF 来源定位，但未被模型绑定为核心 Insight 的直接支撑，
因此仅用于建立论文语境，不作为核心结论的直接证据。</p>"""
            if has_context
            else ""
        )
        return self._story_section(
            section_id="evidence",
            number="03",
            kicker="Visual evidence",
            title="证据图表",
            content=f'{note}<div class="evidence-gallery">{"".join(figures)}</div>',
        )

    def _method_section(self, analysis: PaperAnalysis) -> str:
        lead = (
            f'<div class="method-lead">{self._claim(analysis.method_overview)}</div>'
            if analysis.method_overview is not None
            else ""
        )
        modules = "".join(
            f"""<li>
  <span class="module-marker" aria-hidden="true"></span>
  <div><strong>{escape(module.name, quote=True)}</strong>{self._claim(module.purpose)}</div>
</li>"""
            for module in analysis.method_modules
        )
        module_list = (
            f'<ol class="module-list">{modules}</ol>' if modules else ""
        )
        difference = (
            f"""<aside class="difference-card">
  <p class="mini-label">WHAT CHANGED</p>
  <h3>与已有工作的区别</h3>
  {self._claim(analysis.differences_from_prior_work)}
</aside>"""
            if analysis.differences_from_prior_work is not None
            else ""
        )
        if not any((lead, module_list, difference)):
            return ""
        return self._story_section(
            section_id="method",
            number="04",
            kicker="Mechanism",
            title="Method",
            content=f"{lead}{module_list}{difference}",
        )

    def _experiments_section(self, analysis: PaperAnalysis) -> str:
        parameter_table = ""
        if analysis.parameters:
            rows = "".join(
                f"""<tr>
  <td><strong>{escape(item.name, quote=True)}</strong></td>
  <td>{self._optional_value(item.symbol)}</td>
  <td>{self._claim(item.role)}</td>
  <td>{self._optional_value(item.final_value)}</td>
</tr>"""
                for item in analysis.parameters
            )
            parameter_table = f"""<div class="table-block">
  <h2>关键参数</h2>
  <div class="table-scroll"><table>
    <caption>参数符号、作用和最终取值</caption>
    <thead><tr><th>名称</th><th>符号</th><th>作用</th><th>最终取值</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</div>"""
        ablations = ""
        if analysis.ablations:
            items = "".join(
                f"<li>{self._claim(item.conclusion)}</li>"
                for item in analysis.ablations
            )
            ablations = f"""<div class="ablation-block">
  <h2>参数对应的消融实验</h2>
  <ol class="result-list">{items}</ol>
</div>"""
        if not parameter_table and not ablations:
            return ""
        return self._story_section(
            section_id="experiments",
            number="05",
            kicker="Configuration & tests",
            title="参数与实验",
            content=f"{parameter_table}{ablations}",
        )

    def _results_section(self, analysis: PaperAnalysis) -> str:
        if not analysis.experimental_conclusions:
            return ""
        items = "".join(
            f"<li>{self._claim(claim)}</li>"
            for claim in analysis.experimental_conclusions
        )
        return self._story_section(
            section_id="results",
            number="06",
            kicker="Evidence-backed outcome",
            title="主要实验结论",
            content=f'<ol class="result-list result-list--large">{items}</ol>',
        )

    def _resources_section(self, analysis: PaperAnalysis) -> str:
        return self._story_section(
            section_id="resources",
            number="07",
            kicker="Primary sources",
            title="继续读原文",
            content=f'<div class="resource-links">{self._links(analysis)}</div>',
        )

    def _coverage_panel(
        self,
        analysis: PaperAnalysis,
        image_urls: Mapping[str, str],
    ) -> str:
        has_visual = bool(analysis.supporting_visuals) or any(
            candidate.kind != "text" and image_urls.get(candidate.evidence_id)
            for candidate in analysis.evidence_candidates
        )
        fields = (
            ("recommendation", "推荐理由", analysis.recommendation_reason is not None),
            ("research-problem", "研究问题", analysis.research_problem is not None),
            ("visuals", "可发布图表", has_visual),
            ("parameters", "关键参数", bool(analysis.parameters)),
            ("ablations", "消融实验", bool(analysis.ablations)),
            ("limitations", "作者局限性", bool(analysis.limitations)),
        )
        missing = tuple((key, label) for key, label, present in fields if not present)
        if not missing:
            return ""
        chips = "".join(
            f'<li data-missing-field="{escape(key, quote=True)}">{escape(label, quote=True)}</li>'
            for key, label in missing
        )
        return f"""<aside class="coverage-panel" aria-labelledby="coverage-title">
  <div><p class="mini-label">EVIDENCE COVERAGE</p><h2 id="coverage-title">本次信息缺口</h2></div>
  <div>
    <p>以下内容未能从通过验证的证据中稳定提取。页面选择集中说明，而不是用占位文案反复打断阅读。</p>
    <ul>{chips}</ul>
  </div>
</aside>"""

    @staticmethod
    def _issues_panel(report: ValidationReport) -> str:
        if not report.issues:
            return ""
        items = "".join(
            f"<li><code>{escape(issue.code, quote=True)}</code>{escape(issue.message, quote=True)}</li>"
            for issue in report.issues
        )
        return f"""<aside class="validation-panel">
  <h2>验证备注</h2><ul>{items}</ul>
</aside>"""

    @staticmethod
    def _number_present_sections(sections: tuple[str, ...]) -> tuple[str, ...]:
        numbered: list[str] = []
        next_number = 0
        for section in sections:
            if not section:
                numbered.append(section)
                continue
            next_number += 1
            numbered.append(
                re.sub(
                    r'(<span class="section-index">)\d{2}(</span>)',
                    rf"\g<1>{next_number:02d}\g<2>",
                    section,
                    count=1,
                )
            )
        return tuple(numbered)

    @staticmethod
    def _story_section(
        *,
        section_id: str,
        number: str,
        kicker: str,
        title: str,
        content: str,
    ) -> str:
        return f"""<section id="{escape(section_id, quote=True)}" class="story-section">
  <header class="section-heading">
    <span class="section-index">{escape(number, quote=True)}</span>
    <div><p class="section-kicker">{escape(kicker, quote=True)}</p><h2>{escape(title, quote=True)}</h2></div>
  </header>
  <div class="section-body">{content}</div>
</section>"""

    def _summary_card(
        self,
        title: str,
        label: str,
        claim: ClaimRecord,
        tone: str,
    ) -> str:
        return f"""<article class="summary-card summary-card--{tone}">
  <p class="mini-label">{escape(label, quote=True)}</p>
  <h2>{escape(title, quote=True)}</h2>
  {self._claim(claim)}
</article>"""

    def _supporting_visual(
        self,
        visual: SupportingVisual,
        image_url: str | None,
    ) -> str:
        caption = visual.caption or visual.label or f"PDF 第 {visual.pdf_page} 页图表"
        return f"""<figure class="evidence-figure" data-visual-role="evidence">
  <div class="visual-frame">{self._visual_content(visual, image_url)}</div>
  <figcaption>
    <div class="figure-meta"><span>{escape(visual.kind.upper())}</span><span>PDF {visual.pdf_page}</span><span>CONF {visual.confidence:.2f}</span></div>
    <h3>{escape(caption, quote=True)}</h3>
    {self._claim(visual.support_explanation)}
  </figcaption>
</figure>"""

    @staticmethod
    def _context_visual(candidate: EvidenceCandidate, image_url: str) -> str:
        caption = candidate.caption or candidate.label or f"PDF 第 {candidate.pdf_page} 页图表"
        description = "：".join(
            item for item in (candidate.label, candidate.caption) if item
        )
        alt = description or "论文来源图表"
        return f"""<figure class="evidence-figure evidence-figure--context" data-visual-role="context">
  <div class="visual-frame"><img src="{escape(image_url, quote=True)}" alt="{escape(alt, quote=True)}" loading="lazy"></div>
  <figcaption>
    <div class="figure-meta"><span>CONTEXT</span><span>PDF {candidate.pdf_page}</span><span>CONF {candidate.confidence:.2f}</span></div>
    <h3>{escape(caption, quote=True)}</h3>
  </figcaption>
</figure>"""

    @staticmethod
    def _claim(claim: ClaimRecord) -> str:
        label = (
            "系统推断"
            if claim.inferred
            else {
                "author_statement": "作者陈述",
                "system_summary": "系统总结",
            }[claim.source_type]
        )
        tone = "inference" if claim.inferred else claim.source_type
        return f"""<div class="claim">
  <span class="claim-source claim-source--{escape(tone, quote=True)}">{label}</span>
  <p>{escape(claim.text_zh, quote=True)}</p>
</div>"""

    @staticmethod
    def _optional_value(value: str | None) -> str:
        return escape(value, quote=True) if value else '<span class="not-applicable">—</span>'

    @staticmethod
    def _links(analysis: PaperAnalysis) -> str:
        links = (
            ("PDF", "阅读全文", analysis.links.pdf_url),
            ("arXiv", "查看摘要与版本", analysis.links.arxiv_url),
            ("CODE", "打开代码仓库", analysis.links.code_url),
        )
        safe = []
        for label, description, url in links:
            parsed = urlparse(url) if url else None
            if (
                parsed is None
                or parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                continue
            safe.append(
                f"""<a href="{escape(url, quote=True)}" rel="noopener noreferrer">
  <span>{escape(label, quote=True)}</span><strong>{escape(description, quote=True)}</strong><b aria-hidden="true">↗</b>
</a>"""
            )
        return "".join(safe)

    @staticmethod
    def _visual_content(
        visual: SupportingVisual,
        image_url: str | None,
    ) -> str:
        if (
            image_url
            and image_url.startswith("../assets/evidence/")
            and ".." not in image_url[3:].split("/")
        ):
            description = "：".join(
                item for item in (visual.label, visual.caption) if item
            )
            alt = description or "论文证据图表"
            return f'<img src="{escape(image_url, quote=True)}" alt="{escape(alt, quote=True)}" loading="lazy">'
        return f"""<div class="evidence-placeholder" role="status">
  <span aria-hidden="true">◫</span>
  <p>该 {escape(visual.kind)} 已通过来源定位，但当前构建未生成安全图像。</p>
</div>"""

    @staticmethod
    def _table_of_contents(items: tuple[tuple[str, str, bool], ...]) -> str:
        links = "".join(
            f'<li><a href="#{escape(section_id, quote=True)}"><span>{index:02d}</span>{escape(label, quote=True)}</a></li>'
            for index, (section_id, label, present) in enumerate(
                (item for item in items if item[2]), start=1
            )
        )
        return f'<nav class="paper-toc" aria-label="论文内容目录"><ol>{links}</ol></nav>'

    @staticmethod
    def _render_card(paper: PaperPageModel, *, position: int = 1) -> str:
        status = (
            "已验证完整阅读"
            if paper.publication_kind == "full"
            else "部分内容通过验证"
        )
        chinese_title = paper.chinese_title or "中文标题待补充"
        return f"""<article class="paper-card" data-paper-id="{escape(paper.paper_id, quote=True)}" tabindex="0">
  <div class="card-sequence" aria-hidden="true">{position:02d}</div>
  <div class="card-content">
    <p class="status"><span></span>{status}</p>
    <h2><a href="{escape(paper.relative_path, quote=True)}">{escape(paper.english_title, quote=True)}</a></h2>
    <p class="card-title-zh" lang="zh-Hans">{escape(chinese_title, quote=True)}</p>
  </div>
  <div class="card-side">
    <a class="read-link" href="{escape(paper.relative_path, quote=True)}">进入阅读 <span aria-hidden="true">↗</span></a>
    <div class="feedback-actions" aria-label="{escape(paper.english_title, quote=True)} 的阅读反馈">
      {TemplateRenderer._feedback_button("read", "标记已读", "R")}
      {TemplateRenderer._feedback_button("favorite", "收藏", "F")}
      {TemplateRenderer._feedback_button("irrelevant", "标记不相关", "I")}
    </div>
  </div>
</article>"""

    @staticmethod
    def _feedback_button(action: str, label: str, shortcut: str) -> str:
        return (
            f'<button type="button" class="feedback-button" data-feedback-action="{action}" '
            f'data-ai-action="set-{action}" data-testid="set-{action}" aria-pressed="false">'
            f'<span data-feedback-label="{action}">{label}</span>'
            f'<span class="shortcut" aria-hidden="true">{shortcut}</span></button>'
        )

    @staticmethod
    def _feedback_toolbar() -> str:
        return """<section id="reading-desk" class="feedback-toolbar" aria-labelledby="feedback-tools-heading">
      <div class="feedback-heading">
        <p class="eyebrow">Private reading desk</p>
        <h2 id="feedback-tools-heading">整理阅读反馈</h2>
        <p>已读、收藏与不相关状态只保存在当前浏览器，不会写入公开页面。</p>
      </div>
      <fieldset class="feedback-filters">
        <legend>筛选论文</legend>
        <label><input type="checkbox" data-feedback-filter="unread" data-ai-action="filter-unread" data-testid="filter-unread"> 仅看未读</label>
        <label><input type="checkbox" data-feedback-filter="favorite" data-ai-action="filter-favorite" data-testid="filter-favorite"> 仅看收藏</label>
        <label><input type="checkbox" data-feedback-filter="show-irrelevant" data-ai-action="filter-show-irrelevant" data-testid="filter-show-irrelevant"> 显示不相关</label>
      </fieldset>
      <div class="feedback-backup" aria-label="反馈备份">
        <button type="button" data-ai-action="export-feedback" data-testid="export-feedback">导出反馈</button>
        <label class="file-action" for="feedback-import">导入浏览器备份</label>
        <input id="feedback-import" type="file" accept="application/json,.json" data-ai-action="import-feedback" data-testid="import-feedback">
        <button type="button" data-ai-action="clear-feedback" data-testid="clear-feedback">清除本站反馈</button>
      </div>
      <p class="feedback-status" role="status" aria-live="polite" data-testid="feedback-status">反馈仅保存在当前浏览器。</p>
    </section>"""
