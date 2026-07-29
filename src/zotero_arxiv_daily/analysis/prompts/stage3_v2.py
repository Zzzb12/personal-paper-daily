from __future__ import annotations

import json
from typing import Any

from zotero_arxiv_daily.analysis.client import AnalysisRequest
from zotero_arxiv_daily.analysis.paper_schemas import (
    EvidenceCandidate,
    EvidencePacket,
    PaperAnalysisDraft,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper


PROMPT_VERSION = "stage3-v2"

_SYSTEM_PROMPT = """你是严谨的科研论文分析助手。请使用简洁、技术准确的中文，并保留英文原题、数学符号、模型、数据集和指标的标准名称。

你只能引用输入 evidence_candidates 中存在的 evidence_id。不得编造 Figure/Table label、caption、参数、符号、数值、实验设置、代码链接或局限性。找不到的信息必须输出 JSON null 或空数组。

每条陈述必须区分 author_statement、system_summary、system_inference；system_inference 必须 inferred=true，其他来源必须 inferred=false。Insight 不得只由 abstract_only=true 的证据支撑。SupportingVisual 的 evidence_id 必须指向 kind=figure 或 kind=table 的候选；没有这种候选时 supporting_visuals 必须为 []。Ablation 只有在存在 figure/table evidence 时才可输出，否则 ablations 必须为 []。每个 SupportingVisual 必须解释该 Figure/Table 如何支撑对应 Insight。

输出必须严格符合提供的 JSON Schema。minimal_valid_json_example 只演示完整字段和 JSON 类型，不得照抄空内容来替代证据中明确提供的信息。不要输出 Markdown 代码围栏、解释、前言或结尾。"""


def build_analysis_request(
    paper: CandidatePaper,
    packet: EvidencePacket,
    *,
    max_output_tokens: int,
) -> AnalysisRequest:
    payload = {
        "paper": {
            "paper_id": paper.paper_id,
            "english_title": paper.title,
            "links": {
                "pdf_url": paper.pdf_url,
                "arxiv_url": paper.arxiv_url,
                "code_url": paper.code_url,
            },
        },
        "evidence_packet": {
            "schema_version": packet.schema_version,
            "builder_version": packet.builder_version,
            "document_fingerprint": packet.document_fingerprint,
            "packet_fingerprint": packet.packet_fingerprint,
            "evidence_candidates": [
                _prompt_candidate(candidate) for candidate in packet.candidates
            ],
        },
        "required_narrative_order": [
            "insights",
            "supporting_visuals",
            "method_overview",
            "parameters",
            "ablations",
            "experimental_conclusions",
            "limitations",
        ],
        "minimal_valid_json_example": _minimal_valid_json_example(paper),
        "output_json_schema": PaperAnalysisDraft.model_json_schema(),
    }
    return AnalysisRequest(
        prompt_version=PROMPT_VERSION,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        response_schema=PaperAnalysisDraft.model_json_schema(),
        max_output_tokens=max_output_tokens,
    )


def _minimal_valid_json_example(paper: CandidatePaper) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "paper_id": paper.paper_id,
        "english_title": paper.title,
        "chinese_title": None,
        "recommendation_reason": None,
        "research_problem": None,
        "insights": [],
        "supporting_visuals": [],
        "insight_formation_logic": None,
        "method_overview": None,
        "method_modules": [],
        "differences_from_prior_work": None,
        "parameters": [],
        "ablations": [],
        "experimental_conclusions": [],
        "limitations": [],
        "links": {
            "pdf_url": paper.pdf_url,
            "arxiv_url": paper.arxiv_url,
            "code_url": paper.code_url,
        },
    }


def _prompt_candidate(candidate: EvidenceCandidate) -> dict[str, Any]:
    prompt_caption = candidate.evidence_text if candidate.kind != "text" else None
    return {
        "evidence_id": candidate.evidence_id,
        "kind": candidate.kind,
        "pdf_page": candidate.pdf_page,
        "section_id": candidate.section_id,
        "section_title": candidate.section_title,
        "section_path": candidate.section_path,
        "block_ids": candidate.block_ids,
        "evidence_text": candidate.evidence_text if candidate.kind == "text" else None,
        "visual_id": candidate.visual_id,
        "label": candidate.label,
        "caption": prompt_caption,
        "regions": [
            {
                "pdf_page": region.pdf_page,
                "bbox": region.bbox.model_dump(mode="json"),
                "source_mapping": region.source_mapping.model_dump(mode="json"),
                "confidence": region.confidence,
            }
            for region in candidate.regions
        ],
        "confidence": candidate.confidence,
        "abstract_only": candidate.abstract_only,
    }
