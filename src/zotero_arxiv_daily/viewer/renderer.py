from __future__ import annotations

from html import escape

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

    @staticmethod
    def _render_card(paper: PaperPageModel) -> str:
        status = "已验证完整阅读" if paper.publication_kind == "full" else "部分内容未通过或无法完成验证"
        chinese_title = paper.chinese_title or "论文未明确提供"
        return f"""<article class="paper-card">
  <p class="status">{status}</p>
  <h2><a href="{escape(paper.relative_path, quote=True)}">{escape(paper.english_title, quote=True)}</a></h2>
  <p lang="zh-Hans">{escape(chinese_title, quote=True)}</p>
</article>"""
