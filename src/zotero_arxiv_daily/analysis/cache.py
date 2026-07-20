from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import DocumentGraph
from zotero_arxiv_daily.analysis.paper_schemas import (
    EvidencePacket,
    PaperAnalysis,
)
from zotero_arxiv_daily.analysis.schemas import CandidatePaper, StrictModel


_SHA256_LENGTH = 64


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("cache identity value must not be blank")
    return normalized


def _sha256(value: str) -> str:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("cache identity digest must be a lowercase SHA-256")
    return value


class AnalysisCacheIdentity(StrictModel):
    paper_id: str
    paper_metadata_fingerprint: str
    pdf_sha256: str
    document_schema_version: str
    document_parser: str
    parser_version: str
    mapper_version: str
    document_config_version: str
    content_fingerprint: str
    packet_schema_version: str
    evidence_builder_version: str
    packet_fingerprint: str
    prompt_version: str
    analysis_schema_version: str
    model_identity: str
    generation_identity: str

    @field_validator(
        "paper_id",
        "document_schema_version",
        "document_parser",
        "parser_version",
        "mapper_version",
        "document_config_version",
        "packet_schema_version",
        "evidence_builder_version",
        "prompt_version",
        "analysis_schema_version",
        "model_identity",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator(
        "pdf_sha256",
        "paper_metadata_fingerprint",
        "content_fingerprint",
        "packet_fingerprint",
        "generation_identity",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return _sha256(value)

    @property
    def cache_key(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class AnalysisCacheEnvelope(StrictModel):
    identity: AnalysisCacheIdentity
    analysis: PaperAnalysis

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.analysis.paper_id != self.identity.paper_id:
            raise ValueError("cached analysis paper_id must match cache identity")
        if self.analysis.generation.cache_key != self.identity.cache_key:
            raise ValueError("cached generation key must match cache identity")
        return self


class AnalysisCache:
    def __init__(self, root: Path, *, max_cache_bytes: int = 10 * 1024 * 1024) -> None:
        if max_cache_bytes <= 0:
            raise ValueError("max_cache_bytes must be positive")
        self.root = Path(root)
        self.max_cache_bytes = max_cache_bytes

    def path_for(self, identity: AnalysisCacheIdentity) -> Path:
        target = self.root / identity.cache_key[:2] / f"{identity.cache_key}.json"
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("analysis cache path escaped its root")
        return target

    def read(self, identity: AnalysisCacheIdentity) -> PaperAnalysis | None:
        path = self.path_for(identity)
        try:
            if path.stat().st_size > self.max_cache_bytes:
                return None
            envelope = AnalysisCacheEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        return envelope.analysis if envelope.identity == identity else None

    def write(
        self, identity: AnalysisCacheIdentity, analysis: PaperAnalysis
    ) -> Path:
        envelope = AnalysisCacheEnvelope(identity=identity, analysis=analysis)
        payload = envelope.model_dump_json(indent=2) + "\n"
        encoded_size = len(payload.encode("utf-8"))
        if encoded_size > self.max_cache_bytes:
            raise ValueError("analysis cache payload exceeds configured size")

        target = self.path_for(identity)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            checked = AnalysisCacheEnvelope.model_validate_json(
                temporary.read_text(encoding="utf-8")
            )
            if checked != envelope:
                raise ValueError("analysis cache revalidation failed")
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def build_analysis_cache_identity(
    paper: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    *,
    prompt_version: str,
    analysis_schema_version: str,
    model_identity: str,
    generation_identity: str,
) -> AnalysisCacheIdentity:
    return AnalysisCacheIdentity(
        paper_id=paper.paper_id,
        paper_metadata_fingerprint=_paper_metadata_fingerprint(paper),
        pdf_sha256=document.pdf.sha256,
        document_schema_version=document.schema_version,
        document_parser=document.parser,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        document_config_version=document.config_version,
        content_fingerprint=document.content_fingerprint,
        packet_schema_version=packet.schema_version,
        evidence_builder_version=packet.builder_version,
        packet_fingerprint=packet.packet_fingerprint,
        prompt_version=prompt_version,
        analysis_schema_version=analysis_schema_version,
        model_identity=model_identity,
        generation_identity=generation_identity,
    )


def _paper_metadata_fingerprint(paper: CandidatePaper) -> str:
    payload = json.dumps(
        {
            "paper_id": paper.paper_id,
            "english_title": paper.title,
            "links": {
                "pdf_url": paper.pdf_url,
                "arxiv_url": paper.arxiv_url,
                "code_url": paper.code_url,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
