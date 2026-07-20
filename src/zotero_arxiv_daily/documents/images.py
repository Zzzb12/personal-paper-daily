from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pymupdf

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentGraph,
    DocumentIssue,
    VisualArtifact,
    VisualRegion,
)


def extract_evidence_images(
    graph: DocumentGraph, output_root: Path, *, scale: float = 2.0
) -> DocumentGraph:
    if scale <= 0:
        raise ValueError("scale must be positive")
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        document = pymupdf.open(graph.pdf.local_path)
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError) as exc:
        visuals = tuple(
            _with_visual_issue(
                visual,
                f"PDF could not be opened for evidence rendering: {type(exc).__name__}",
            )
            for visual in graph.visuals
        )
        return DocumentGraph.model_validate(graph.model_copy(update={"visuals": visuals}).model_dump())

    try:
        visuals: list[VisualArtifact] = []
        for visual in graph.visuals:
            regions: list[VisualRegion] = []
            issues = list(visual.issues)
            for region in visual.regions:
                try:
                    page = document.load_page(region.pdf_page - 1)
                    clip = pymupdf.Rect(
                        region.bbox.left,
                        region.bbox.top,
                        region.bbox.right,
                        region.bbox.bottom,
                    )
                    if clip.is_empty or not page.rect.contains(clip):
                        raise ValueError("visual bbox is outside the rendered PDF page")
                    key = hashlib.sha256(
                        (
                            f"{graph.pdf.sha256}|{visual.visual_id}|{region.pdf_page}|"
                            f"{region.bbox.model_dump_json()}|{scale}"
                        ).encode("utf-8")
                    ).hexdigest()
                    target = (
                        root
                        / graph.pdf.sha256[:16]
                        / visual.visual_id[:16]
                        / f"{key[:32]}.png"
                    )
                    if not target.resolve().is_relative_to(root):
                        raise ValueError("evidence image path escapes its cache root")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.is_file():
                        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
                        _atomic_write(target, pixmap.tobytes("png"))
                    regions.append(region.model_copy(update={"image_path": target}))
                except (RuntimeError, ValueError, OSError) as exc:
                    regions.append(region.model_copy(update={"image_path": None}))
                    issues.append(
                        DocumentIssue(
                            code="visual_image_not_extracted",
                            severity="warning",
                            message=f"visual region could not be rendered: {type(exc).__name__}",
                            pdf_page=region.pdf_page,
                            source_item_id=region.source_mapping.source_item_id,
                        )
                    )
            visuals.append(
                visual.model_copy(update={"regions": tuple(regions), "issues": tuple(issues)})
            )
        return DocumentGraph.model_validate(graph.model_copy(update={"visuals": tuple(visuals)}).model_dump())
    finally:
        document.close()


def _with_visual_issue(visual: VisualArtifact, message: str) -> VisualArtifact:
    issue = DocumentIssue(
        code="visual_image_not_extracted",
        severity="warning",
        message=message,
        pdf_page=visual.regions[0].pdf_page,
        source_item_id=visual.regions[0].source_mapping.source_item_id,
    )
    regions = tuple(region.model_copy(update={"image_path": None}) for region in visual.regions)
    return visual.model_copy(update={"regions": regions, "issues": (*visual.issues, issue)})


def _atomic_write(target: Path, data: bytes) -> None:
    temporary = target.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
