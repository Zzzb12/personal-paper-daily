from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from pydantic import Field, field_validator

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBlock,
    DocumentGraph,
    SectionNode,
    VisualArtifact,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    EvidenceCandidate,
    EvidencePacket,
    EvidenceRegion,
)
from zotero_arxiv_daily.analysis.schemas import StrictModel


EVIDENCE_BUILDER_VERSION = "1"
_NUMBER_PREFIX_RE = re.compile(r"^\s*(?:[A-Z]?\d+(?:\.\d+)*[.):]?\s*)+", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^a-z]+")


class EvidenceBuildSettings(StrictModel):
    max_candidates: int = Field(default=48, ge=1, le=200)
    max_chars: int = Field(default=24_000, ge=256)
    max_block_chars: int = Field(default=2_000, ge=64)
    max_visuals: int = Field(default=3, ge=0, le=3)
    builder_version: str = EVIDENCE_BUILDER_VERSION

    @field_validator("builder_version")
    @classmethod
    def normalize_builder_version(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("builder_version must not be blank")
        return normalized


@dataclass(frozen=True)
class _OrderedCandidate:
    priority: int
    pdf_page: int
    reading_order: int
    kind_order: int
    stable_id: str
    candidate: EvidenceCandidate

    @property
    def key(self) -> tuple[int, int, int, int, str]:
        return (
            self.priority,
            self.pdf_page,
            self.kind_order,
            self.reading_order,
            self.stable_id,
        )


def build_evidence_packet(
    paper_id: str,
    document: DocumentGraph,
    settings: EvidenceBuildSettings,
) -> EvidencePacket:
    section_by_id = {section.section_id: section for section in document.sections}
    section_paths = {
        section.section_id: _section_path(section, section_by_id)
        for section in document.sections
    }

    text_candidates = _text_candidates(
        paper_id, document, section_by_id, section_paths, settings
    )
    visual_candidates = sorted(
        _visual_candidates(paper_id, document, section_by_id, section_paths),
        key=lambda item: item.key,
    )[: settings.max_visuals]
    ordered = sorted((*text_candidates, *visual_candidates), key=lambda item: item.key)
    bounded = _apply_budget(ordered, settings)
    fingerprint = _packet_fingerprint(
        paper_id=paper_id,
        document_fingerprint=document.content_fingerprint,
        builder_version=settings.builder_version,
        candidates=bounded,
    )
    return EvidencePacket(
        paper_id=paper_id,
        document_fingerprint=document.content_fingerprint,
        builder_version=settings.builder_version,
        candidates=bounded,
        packet_fingerprint=fingerprint,
    )


def _text_candidates(
    paper_id: str,
    document: DocumentGraph,
    section_by_id: dict[str, SectionNode],
    section_paths: dict[str, tuple[str, ...]],
    settings: EvidenceBuildSettings,
) -> list[_OrderedCandidate]:
    candidates: list[_OrderedCandidate] = []
    for block in document.blocks:
        if block.block_type not in {"text", "list_item", "formula"}:
            continue
        section = section_by_id.get(block.section_id) if block.section_id else None
        path = section_paths.get(block.section_id, ()) if block.section_id else ()
        text = block.text[: settings.max_block_chars]
        region = EvidenceRegion(
            pdf_page=block.pdf_page,
            bbox=block.bbox,
            image_path=None,
            source_mapping=block.source_mapping,
            confidence=block.source_mapping.confidence,
        )
        evidence_id = _evidence_id(
            document.pdf.sha256,
            "text",
            (region,),
        )
        candidate = EvidenceCandidate(
            evidence_id=evidence_id,
            paper_id=paper_id,
            kind="text",
            pdf_page=block.pdf_page,
            section_id=block.section_id,
            section_title=section.title if section else None,
            section_path=path,
            block_ids=(block.block_id,),
            evidence_text=text,
            visual_id=None,
            label=None,
            caption=None,
            regions=(region,),
            confidence=block.source_mapping.confidence,
            abstract_only=_is_abstract_path(path),
        )
        candidates.append(
            _OrderedCandidate(
                priority=_section_priority(path),
                pdf_page=block.pdf_page,
                reading_order=block.reading_order,
                kind_order=0,
                stable_id=evidence_id,
                candidate=candidate,
            )
        )
    return candidates


def _visual_candidates(
    paper_id: str,
    document: DocumentGraph,
    section_by_id: dict[str, SectionNode],
    section_paths: dict[str, tuple[str, ...]],
) -> list[_OrderedCandidate]:
    candidates: list[_OrderedCandidate] = []
    for visual in document.visuals:
        section = section_by_id.get(visual.section_id) if visual.section_id else None
        path = section_paths.get(visual.section_id, ()) if visual.section_id else ()
        regions = tuple(_evidence_region(region) for region in visual.regions)
        evidence_id = _evidence_id(
            document.pdf.sha256, visual.kind, regions
        )
        candidate = EvidenceCandidate(
            evidence_id=evidence_id,
            paper_id=paper_id,
            kind=visual.kind,
            pdf_page=visual.regions[0].pdf_page,
            section_id=visual.section_id,
            section_title=section.title if section else None,
            section_path=path,
            block_ids=visual.caption_block_ids,
            evidence_text=visual.caption,
            visual_id=visual.visual_id,
            label=visual.label,
            caption=visual.caption,
            regions=regions,
            confidence=visual.confidence,
            abstract_only=_is_abstract_path(path),
        )
        candidates.append(
            _OrderedCandidate(
                priority=_section_priority(path),
                pdf_page=visual.regions[0].pdf_page,
                reading_order=0,
                kind_order=1,
                stable_id=visual.visual_id,
                candidate=candidate,
            )
        )
    return candidates


def _evidence_region(region) -> EvidenceRegion:
    return EvidenceRegion(
        pdf_page=region.pdf_page,
        bbox=region.bbox,
        image_path=region.image_path,
        source_mapping=region.source_mapping,
        confidence=region.confidence,
    )


def _apply_budget(
    ordered: list[_OrderedCandidate], settings: EvidenceBuildSettings
) -> tuple[EvidenceCandidate, ...]:
    remaining = settings.max_chars
    bounded: list[EvidenceCandidate] = []
    for item in ordered:
        if len(bounded) >= settings.max_candidates:
            break
        candidate = item.candidate
        text = candidate.evidence_text
        if text is not None:
            if remaining <= 0:
                if candidate.kind == "text":
                    continue
                text = None
            else:
                text = text[:remaining]
                remaining -= len(text)
        bounded.append(candidate.model_copy(update={"evidence_text": text}))
    return tuple(bounded)


def _section_path(
    section: SectionNode, section_by_id: dict[str, SectionNode]
) -> tuple[str, ...]:
    path: list[str] = []
    current: SectionNode | None = section
    while current is not None:
        path.append(current.title)
        current = section_by_id.get(current.parent_id) if current.parent_id else None
    return tuple(reversed(path))


def _normalized_title(title: str) -> str:
    without_number = _NUMBER_PREFIX_RE.sub("", title)
    return " ".join(_NON_WORD_RE.sub(" ", without_number.lower()).split())


def _section_priority(path: tuple[str, ...]) -> int:
    titles = {_normalized_title(title) for title in path}
    if any(
        any(keyword in title for keyword in ("introduction", "motivation", "observation", "analysis"))
        for title in titles
    ):
        return 0
    if any(
        any(keyword in title for keyword in ("method", "approach", "framework", "algorithm"))
        for title in titles
    ):
        return 1
    if any(
        any(keyword in title for keyword in ("experiment", "result", "evaluation"))
        for title in titles
    ):
        return 2
    if any("ablation" in title for title in titles):
        return 3
    if any(
        any(keyword in title for keyword in ("appendix", "supplement"))
        for title in titles
    ):
        return 4
    if _is_abstract_path(path):
        return 6
    return 5


def _is_abstract_path(path: tuple[str, ...]) -> bool:
    return bool(path) and all(
        _normalized_title(title) in {"abstract", "summary"} for title in path
    )


def _evidence_id(
    pdf_sha256: str,
    kind: str,
    regions: tuple[EvidenceRegion, ...],
) -> str:
    payload = json.dumps(
        {
            "pdf_sha256": pdf_sha256,
            "kind": kind,
            "regions": [
                {
                    "source_item_id": region.source_mapping.source_item_id,
                    "pdf_page": region.pdf_page,
                    "bbox": region.bbox.model_dump(mode="json"),
                }
                for region in regions
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ev-{hashlib.sha256(payload).hexdigest()[:24]}"


def _packet_fingerprint(
    *,
    paper_id: str,
    document_fingerprint: str,
    builder_version: str,
    candidates: tuple[EvidenceCandidate, ...],
) -> str:
    payload = json.dumps(
        {
            "paper_id": paper_id,
            "document_fingerprint": document_fingerprint,
            "builder_version": builder_version,
            "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
