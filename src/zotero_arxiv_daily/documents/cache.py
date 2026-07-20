from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from zotero_arxiv_daily.analysis.document_schemas import DocumentGraph
from zotero_arxiv_daily.documents.images import _valid_png


@dataclass(frozen=True)
class StoredDocumentGraph:
    path: Path
    sha256: str


class DocumentGraphCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def write(self, graph: DocumentGraph) -> StoredDocumentGraph:
        target = self._path(
            graph.pdf.sha256,
            graph.parser_version,
            graph.mapper_version,
            graph.config_version,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        data = (graph.model_dump_json(indent=2) + "\n").encode("utf-8")
        try:
            with temporary.open("wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            DocumentGraph.model_validate_json(temporary.read_text(encoding="utf-8"))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredDocumentGraph(path=target, sha256=hashlib.sha256(data).hexdigest())

    def read(
        self,
        *,
        pdf_sha256: str,
        parser_version: str,
        mapper_version: str,
        config_version: str,
    ) -> DocumentGraph | None:
        target = self._path(pdf_sha256, parser_version, mapper_version, config_version)
        try:
            graph = DocumentGraph.model_validate_json(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError):
            return None
        if (
            graph.pdf.sha256 != pdf_sha256
            or graph.parser_version != parser_version
            or graph.mapper_version != mapper_version
            or graph.config_version != config_version
        ):
            return None
        if any(
            region.image_path is not None and not _valid_png(region.image_path)
            for visual in graph.visuals
            for region in visual.regions
        ):
            return None
        return graph

    def _path(
        self, pdf_sha256: str, parser_version: str, mapper_version: str, config_version: str
    ) -> Path:
        if len(pdf_sha256) != 64 or any(value not in "0123456789abcdef" for value in pdf_sha256):
            raise ValueError("pdf_sha256 must be a lowercase SHA-256 digest")
        key = hashlib.sha256(
            "\x1f".join(
                (pdf_sha256, DocumentGraph.model_fields["schema_version"].default,
                 parser_version, mapper_version, config_version)
            ).encode("utf-8")
        ).hexdigest()
        target = self.root / "graphs" / pdf_sha256[:2] / f"{key}.json"
        if not target.resolve().is_relative_to(self.root):
            raise ValueError("document cache path escapes its root")
        return target
