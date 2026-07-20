from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator

from zotero_arxiv_daily.analysis.document_schemas import DocumentGraph
from zotero_arxiv_daily.analysis.paper_schemas import EvidencePacket, PaperAnalysis
from zotero_arxiv_daily.analysis.schemas import CandidatePaper, StrictModel
from zotero_arxiv_daily.analysis.validation_schemas import (
    VALIDATION_SCHEMA_VERSION,
    VALIDATOR_VERSION,
    ValidatedPaperAnalysis,
    ValidationReport,
)
from zotero_arxiv_daily.analysis.validator import validation_input_fingerprint


_SHA256_LENGTH = 64


def _non_empty(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("validation cache identity value must not be blank")
    return normalized


def _sha256(value: str) -> str:
    if len(value) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("validation cache identity digest must be a lowercase SHA-256")
    return value


class ValidationCacheIdentity(StrictModel):
    paper_id: str
    validator_version: str
    validation_schema_version: str
    candidate_fingerprint: str
    pdf_sha256: str
    document_schema_version: str
    document_parser: str
    parser_version: str
    mapper_version: str
    document_config_version: str
    document_fingerprint: str
    packet_schema_version: str
    evidence_builder_version: str
    packet_fingerprint: str
    analysis_schema_version: str
    analysis_generation_key: str
    analysis_fingerprint: str
    input_fingerprint: str

    @field_validator(
        "paper_id",
        "validator_version",
        "validation_schema_version",
        "document_schema_version",
        "document_parser",
        "parser_version",
        "mapper_version",
        "document_config_version",
        "packet_schema_version",
        "evidence_builder_version",
        "analysis_schema_version",
    )
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator(
        "candidate_fingerprint",
        "pdf_sha256",
        "document_fingerprint",
        "packet_fingerprint",
        "analysis_generation_key",
        "analysis_fingerprint",
        "input_fingerprint",
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


class ValidationCacheEnvelope(StrictModel):
    identity: ValidationCacheIdentity
    analysis: PaperAnalysis
    report: ValidationReport

    @model_validator(mode="after")
    def validate_relationships(self) -> Self:
        if self.analysis.paper_id != self.identity.paper_id:
            raise ValueError("cached analysis paper_id must match validation cache identity")
        if self.report.paper_id != self.identity.paper_id:
            raise ValueError("cached report paper_id must match validation cache identity")
        if self.report.validator_version != self.identity.validator_version:
            raise ValueError("cached validator version must match validation cache identity")
        if self.report.schema_version != self.identity.validation_schema_version:
            raise ValueError("cached validation schema must match validation cache identity")
        if self.report.input_fingerprint != self.identity.input_fingerprint:
            raise ValueError("cached input fingerprint must match validation cache identity")
        return self


class ValidationCache:
    def __init__(self, root: Path, *, max_cache_bytes: int = 10 * 1024 * 1024) -> None:
        if max_cache_bytes <= 0:
            raise ValueError("max_cache_bytes must be positive")
        self.root = Path(root)
        self.max_cache_bytes = max_cache_bytes

    def path_for(self, identity: ValidationCacheIdentity) -> Path:
        target = self.root / identity.cache_key[:2] / f"{identity.cache_key}.json"
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("validation cache path escaped its root")
        return target

    def read(
        self, identity: ValidationCacheIdentity
    ) -> ValidatedPaperAnalysis | None:
        path = self.path_for(identity)
        try:
            if path.stat().st_size > self.max_cache_bytes:
                return None
            envelope = ValidationCacheEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        if envelope.identity != identity:
            return None
        return ValidatedPaperAnalysis(
            analysis=envelope.analysis,
            report=envelope.report,
        )

    def write(
        self,
        identity: ValidationCacheIdentity,
        validated: ValidatedPaperAnalysis,
    ) -> Path:
        envelope = ValidationCacheEnvelope(
            identity=identity,
            analysis=validated.analysis,
            report=validated.report,
        )
        payload = envelope.model_dump_json(indent=2) + "\n"
        if len(payload.encode("utf-8")) > self.max_cache_bytes:
            raise ValueError("validation cache payload exceeds configured size")

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
            checked = ValidationCacheEnvelope.model_validate_json(
                temporary.read_text(encoding="utf-8")
            )
            if checked != envelope:
                raise ValueError("validation cache revalidation failed")
            os.replace(temporary, target)
            temporary = None
            return target
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def build_validation_cache_identity(
    candidate: CandidatePaper,
    document: DocumentGraph,
    packet: EvidencePacket,
    analysis: PaperAnalysis,
    *,
    validator_version: str = VALIDATOR_VERSION,
    validation_schema_version: str = VALIDATION_SCHEMA_VERSION,
) -> ValidationCacheIdentity:
    return ValidationCacheIdentity(
        paper_id=candidate.paper_id,
        validator_version=validator_version,
        validation_schema_version=validation_schema_version,
        candidate_fingerprint=_canonical_sha256(
            {
                "paper_id": candidate.paper_id,
                "english_title": candidate.title,
                "pdf_url": candidate.pdf_url,
                "arxiv_url": candidate.arxiv_url,
                "code_url": candidate.code_url,
            }
        ),
        pdf_sha256=document.pdf.sha256,
        document_schema_version=document.schema_version,
        document_parser=document.parser,
        parser_version=document.parser_version,
        mapper_version=document.mapper_version,
        document_config_version=document.config_version,
        document_fingerprint=document.content_fingerprint,
        packet_schema_version=packet.schema_version,
        evidence_builder_version=packet.builder_version,
        packet_fingerprint=packet.packet_fingerprint,
        analysis_schema_version=analysis.schema_version,
        analysis_generation_key=analysis.generation.cache_key,
        analysis_fingerprint=_canonical_sha256(analysis.model_dump(mode="json")),
        input_fingerprint=validation_input_fingerprint(
            candidate, document, packet, analysis
        ),
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
