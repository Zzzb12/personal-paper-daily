"""Pure, offline-safe conversion from validated analyses to Feishu cards."""

from __future__ import annotations

import hashlib
import html
import json
from urllib.parse import quote, urlparse

from zotero_arxiv_daily.analysis.validation_schemas import ValidationBatchResult
from zotero_arxiv_daily.delivery.schemas import DeliveryRequest, DigestPaper, FeishuPayload


_MISSING_FACT = "论文未明确提供"
_CARD_TITLE = "个人论文日报"
_MARKDOWN_SPECIAL_CHARACTERS = frozenset(r"\\`*_{}[]()#+-.!|>~")


def _safe_https_url(value: object) -> str | None:
    """Return a display-safe HTTPS URL, never returning credentialed authorities."""

    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return quote(value.strip(), safe=":/?#[]@!$&'*+,;=%")


def _escaped_text(value: object) -> str:
    """Escape user-provided text before placing it in a Markdown card element."""

    if not isinstance(value, str) or not value.strip():
        return _MISSING_FACT
    single_line = " ".join(value.split())
    markdown_escaped = "".join(
        f"\\{character}" if character in _MARKDOWN_SPECIAL_CHARACTERS else character
        for character in single_line
    )
    return html.escape(markdown_escaped, quote=True)


def _evidence_pointer(paper: DigestPaper) -> str:
    """Render the one strongest validated visual pointer, without fabricating it."""

    label = _escaped_text(paper.evidence_label)
    page = paper.evidence_page
    safe_page = (
        page
        if isinstance(page, int) and not isinstance(page, bool) and page >= 1
        else None
    )
    if safe_page is None:
        return label
    if label == _MISSING_FACT:
        return f"第 {safe_page} 页"
    return f"{label}（第 {safe_page} 页）"


class DigestPolicy:
    """Build a bounded Feishu request exclusively from publishable Stage 4 output."""

    @staticmethod
    def build(
        batch: ValidationBatchResult, *, chat_id: str, site_url: str
    ) -> DeliveryRequest:
        safe_site_url = _safe_https_url(site_url)
        papers: list[DigestPaper] = []
        if safe_site_url is not None:
            for result in batch.results:
                report = result.report
                validated = result.validated
                if (
                    result.status != "validated"
                    or report is None
                    or report.status != "valid"
                    or report.publication_eligibility != "eligible"
                    or validated is None
                ):
                    continue
                analysis = validated.analysis
                if (
                    validated.report != report
                    or result.paper_id != analysis.paper_id
                    or result.paper_id != report.paper_id
                ):
                    continue
                strongest_visual = max(
                    analysis.supporting_visuals,
                    key=lambda visual: visual.confidence,
                    default=None,
                )
                papers.append(
                    DigestPaper(
                        paper_id=analysis.paper_id,
                        english_title=analysis.english_title,
                        chinese_title=(
                            analysis.chinese_title.text_zh
                            if analysis.chinese_title is not None
                            else None
                        ),
                        recommendation_reason=(
                            analysis.recommendation_reason.text_zh
                            if analysis.recommendation_reason is not None
                            else None
                        ),
                        core_insight=(
                            analysis.insights[0].text_zh if analysis.insights else None
                        ),
                        evidence_label=(
                            strongest_visual.label if strongest_visual is not None else None
                        ),
                        evidence_page=(
                            strongest_visual.pdf_page if strongest_visual is not None else None
                        ),
                        experimental_conclusion=(
                            analysis.experimental_conclusions[0].text_zh
                            if analysis.experimental_conclusions
                            else None
                        ),
                        site_url=safe_site_url,
                        validation_status="valid",
                        publication_eligibility="eligible",
                    )
                )
                if len(papers) == 5:
                    break

        payload = FeishuPayload(chat_id=chat_id, papers=tuple(papers))
        canonical_payload = json.dumps(
            payload.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return DeliveryRequest(
            payload=payload,
            idempotency_key=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        )


class FeishuRenderer:
    """Render a validated payload as deterministic Feishu interactive-card JSON."""

    @staticmethod
    def render(payload: FeishuPayload) -> str:
        sections = [_CARD_TITLE]
        for index, paper in enumerate(payload.papers[:5], start=1):
            if (
                paper.validation_status != "valid"
                or paper.publication_eligibility != "eligible"
            ):
                continue
            safe_link = _safe_https_url(paper.site_url)
            sections.extend(
                (
                    f"### 论文 {index}",
                    "**中文标题**",
                    _escaped_text(paper.chinese_title),
                    "**英文标题**",
                    _escaped_text(paper.english_title),
                    "**推荐理由**",
                    _escaped_text(paper.recommendation_reason),
                    "**核心 Insight**",
                    _escaped_text(paper.core_insight),
                    "**关键证据**",
                    _evidence_pointer(paper),
                    "**实验结论**",
                    _escaped_text(paper.experimental_conclusion),
                    "**阅读链接**",
                    f"[打开完整解读]({safe_link})" if safe_link is not None else _MISSING_FACT,
                )
            )

        card = {
            "msg_type": "interactive",
            "card": {
                "schema": "2.0",
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": "blue",
                    "title": {"tag": "plain_text", "content": _CARD_TITLE},
                },
                "body": {
                    "elements": [{"tag": "markdown", "content": "\n".join(sections)}]
                },
            },
        }
        return json.dumps(card, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
