from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.analysis.document_schemas import (
    DocumentBatchResult,
    PaperDocumentResult,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    AnalysisBatchResult,
    PaperAnalysisResult,
    PaperLinks,
)
from zotero_arxiv_daily.analysis.schemas import (
    CandidateBatch,
    CandidateCounts,
    CandidateSelectionLimits,
    RankingModelVersions,
    RankingRecord,
)
from zotero_arxiv_daily.analysis.validation_cache import ValidationCache
from zotero_arxiv_daily.documents.evidence import (
    evidence_candidate_id,
    evidence_packet_fingerprint,
)
from zotero_arxiv_daily.pipeline.validation import (
    ValidationDependencies,
    ValidationSettings,
    build_validation_batch,
)
from tests.analysis.stage4_factories import GoldenInputs, golden_inputs


NOW = datetime(2026, 7, 20, tzinfo=UTC)


def _clone(inputs: GoldenInputs, index: int) -> GoldenInputs:
    suffix = f"{index:05d}"
    paper_id = f"arxiv:2401.{suffix}"
    title = f"Synthetic Paper {index}"
    pdf_url = f"https://arxiv.org/pdf/2401.{suffix}"
    arxiv_url = f"https://arxiv.org/abs/2401.{suffix}"
    paper = inputs.candidate.model_copy(
        update={
            "paper_id": paper_id,
            "arxiv_id": f"2401.{suffix}",
            "title": title,
            "pdf_url": pdf_url,
            "arxiv_url": arxiv_url,
        }
    )
    document = inputs.document.model_copy(
        update={
            "content_fingerprint": f"{index:x}"[-1] * 64,
            "pdf": inputs.document.pdf.model_copy(
                update={
                    "source_url": pdf_url,
                    "sha256": f"{index + 5:x}"[-1] * 64,
                }
            ),
        }
    )
    packet_candidates = tuple(
        item.model_copy(
            update={
                "paper_id": paper_id,
                "evidence_id": evidence_candidate_id(
                    document.pdf.sha256, item.kind, item.regions
                ),
            }
        )
        for item in inputs.packet.candidates
    )
    old_to_new = {
        old.evidence_id: new.evidence_id
        for old, new in zip(inputs.packet.candidates, packet_candidates, strict=True)
    }
    def remap_claim(claim):
        return claim.model_copy(
            update={"evidence_ids": tuple(old_to_new[item] for item in claim.evidence_ids)}
        )
    packet = inputs.packet.model_copy(
        update={
            "paper_id": paper_id,
            "document_fingerprint": document.content_fingerprint,
            "candidates": packet_candidates,
        }
    )
    packet = packet.model_copy(
        update={
            "packet_fingerprint": evidence_packet_fingerprint(
                paper_id=paper_id,
                document_fingerprint=document.content_fingerprint,
                builder_version=packet.builder_version,
                candidates=packet_candidates,
            )
        }
    )
    source_analysis = inputs.analysis_result.analysis
    analysis = inputs.analysis_result.analysis.model_copy(
        update={
            "paper_id": paper_id,
            "english_title": title,
            "links": PaperLinks(pdf_url=pdf_url, arxiv_url=arxiv_url, code_url=None),
            "evidence_candidates": packet_candidates,
            "insights": tuple(remap_claim(item) for item in source_analysis.insights),
            "chinese_title": remap_claim(source_analysis.chinese_title),
            "recommendation_reason": remap_claim(source_analysis.recommendation_reason),
            "research_problem": remap_claim(source_analysis.research_problem),
            "insight_formation_logic": remap_claim(source_analysis.insight_formation_logic),
            "differences_from_prior_work": remap_claim(source_analysis.differences_from_prior_work),
            "method_overview": remap_claim(source_analysis.method_overview),
            "method_modules": tuple(
                item.model_copy(update={"purpose": remap_claim(item.purpose)})
                for item in source_analysis.method_modules
            ),
            "experimental_conclusions": tuple(
                remap_claim(item) for item in source_analysis.experimental_conclusions
            ),
            "limitations": tuple(remap_claim(item) for item in source_analysis.limitations),
            "parameters": tuple(
                item.model_copy(
                    update={
                        "role": remap_claim(item.role),
                        "evidence_ids": tuple(old_to_new[eid] for eid in item.evidence_ids),
                    }
                )
                for item in source_analysis.parameters
            ),
            "ablations": tuple(
                item.model_copy(
                    update={
                        "conclusion": remap_claim(item.conclusion),
                        "visual_evidence_ids": tuple(
                            old_to_new[eid] for eid in item.visual_evidence_ids
                        ),
                    }
                )
                for item in source_analysis.ablations
            ),
            "supporting_visuals": tuple(
                item.model_copy(
                    update={
                        "evidence_id": old_to_new[item.evidence_id],
                        "support_explanation": remap_claim(item.support_explanation),
                    }
                )
                for item in source_analysis.supporting_visuals
            ),
        }
    )
    analysis_result = inputs.analysis_result.model_copy(
        update={"paper_id": paper_id, "analysis": analysis}
    )
    return GoldenInputs(paper, document, packet, analysis_result)


def _inputs(count: int = 3) -> tuple[GoldenInputs, ...]:
    base = golden_inputs()
    return tuple(_clone(base, index + 1) for index in range(count))


def _candidate_batch(items: tuple[GoldenInputs, ...]) -> CandidateBatch:
    versions = RankingModelVersions(
        provider="fixture",
        model="fixture-v1",
        task="retrieval",
        scorer="cosine",
        embedding_identity_hash="c" * 64,
    )
    return CandidateBatch(
        run_id="run-stage4",
        created_at=NOW,
        retrieved_at=NOW,
        categories=("cs.CV",),
        config_hash="a" * 64,
        interest_corpus_fingerprint="b" * 64,
        candidates=tuple(item.candidate for item in items),
        rankings=tuple(
            RankingRecord(
                paper_id=item.candidate.paper_id,
                embedding_score=1 - index / 10,
                final_score=1 - index / 10,
                rank=index + 1,
                reason="artificial fixture",
                model_versions=versions,
            )
            for index, item in enumerate(items)
        ),
        selected_for_llm=tuple(item.candidate.paper_id for item in items[:15]),
        selected_for_full_analysis=tuple(item.candidate.paper_id for item in items[:5]),
        counts=CandidateCounts(
            retrieved=len(items), deduplicated=len(items), invalid=0, excluded=0
        ),
        limits=CandidateSelectionLimits(),
    )


def _document_batch(items: tuple[GoldenInputs, ...]) -> DocumentBatchResult:
    return DocumentBatchResult(
        run_id="run-stage4",
        created_at=NOW,
        results=tuple(
            PaperDocumentResult(
                paper_id=item.candidate.paper_id,
                status="success",
                document=item.document,
                processing_seconds=0,
            )
            for item in items
        ),
    )


def _analysis_batch(items: tuple[GoldenInputs, ...]) -> AnalysisBatchResult:
    return AnalysisBatchResult(
        run_id="run-stage4",
        created_at=NOW,
        results=tuple(item.analysis_result for item in items),
        expensive_call_count=0,
        cache_hit_count=0,
    )


def _deps(tmp_path: Path) -> ValidationDependencies:
    return ValidationDependencies(
        cache=ValidationCache(tmp_path / "validation"),
        clock=lambda: NOW,
    )


def test_batch_isolates_invalid_middle_paper_and_keeps_order(tmp_path: Path) -> None:
    items = list(_inputs())
    bad = items[1]
    bad_analysis = bad.analysis_result.analysis.model_copy(
        update={"english_title": "Tampered middle title"}
    )
    items[1] = bad._replace(
        analysis_result=bad.analysis_result.model_copy(update={"analysis": bad_analysis})
    )
    values = tuple(items)

    result = build_validation_batch(
        _candidate_batch(values),
        _document_batch(values),
        tuple(item.packet for item in values),
        _analysis_batch(values),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.paper_id for item in result.results] == [
        item.candidate.paper_id for item in values
    ]
    assert [item.status for item in result.results] == [
        "validated",
        "invalid",
        "validated",
    ]


def test_batch_processes_at_most_five_selected_papers(tmp_path: Path) -> None:
    items = _inputs(6)
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items[:5]),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert len(result.results) == 5


def test_duplicate_document_fails_only_that_paper(tmp_path: Path) -> None:
    items = _inputs(2)
    documents = _document_batch(items)
    duplicated = documents.model_copy(
        update={"results": (documents.results[0], documents.results[0], documents.results[1])}
    )
    result = build_validation_batch(
        _candidate_batch(items),
        duplicated,
        tuple(item.packet for item in items),
        _analysis_batch(items),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.status for item in result.results] == ["failed", "validated"]


def test_missing_packet_skips_only_that_paper(tmp_path: Path) -> None:
    items = _inputs(2)
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        (items[1].packet,),
        _analysis_batch(items),
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert [item.status for item in result.results] == ["skipped", "validated"]


def test_batch_cache_hit_and_validator_version_identity(tmp_path: Path) -> None:
    items = _inputs(1)
    args = (
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items),
    )
    dependencies = _deps(tmp_path)
    first = build_validation_batch(*args, ValidationSettings(), dependencies)
    second = build_validation_batch(*args, ValidationSettings(), dependencies)
    changed = build_validation_batch(
        *args,
        ValidationSettings(validator_version="stage4-v2"),
        dependencies,
    )

    assert first.cache_hit_count == 0
    assert second.cache_hit_count == 1
    assert changed.cache_hit_count == 0


def test_stage3_status_change_cannot_reuse_eligible_cache(tmp_path: Path) -> None:
    items = _inputs(1)
    dependencies = _deps(tmp_path)
    common = (
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
    )
    first = build_validation_batch(
        *common, _analysis_batch(items), ValidationSettings(), dependencies
    )
    partial_item = items[0]._replace(
        analysis_result=items[0].analysis_result.model_copy(update={"status": "partial"})
    )
    second = build_validation_batch(
        *common,
        _analysis_batch((partial_item,)),
        ValidationSettings(),
        dependencies,
    )

    assert first.results[0].status == "validated"
    assert second.cache_hit_count == 0
    assert second.results[0].status == "partial"
    assert second.results[0].validated.report.publication_eligibility == "blocked"


def test_run_id_mismatch_is_safe_failed_result(tmp_path: Path) -> None:
    items = _inputs(1)
    analyses = _analysis_batch(items).model_copy(update={"run_id": "other-run"})
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        analyses,
        ValidationSettings(),
        _deps(tmp_path),
    )

    assert result.results[0].status == "failed"
    assert result.results[0].issues[0].code == "validation_run_id_mismatch"


class _ThrowingCache:
    def __init__(self, failing_paper_id: str) -> None:
        self.failing_paper_id = failing_paper_id

    def read(self, identity):
        if identity.paper_id == self.failing_paper_id:
            raise RuntimeError("https://secret.test/?token=should-not-leak")
        return None

    def write(self, identity, validated):
        return None


def test_cache_exception_fails_one_paper_without_leaking_or_stopping_batch() -> None:
    items = _inputs(2)
    dependencies = ValidationDependencies(
        cache=_ThrowingCache(items[0].candidate.paper_id), clock=lambda: NOW
    )

    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items),
        ValidationSettings(),
        dependencies,
    )

    assert [item.status for item in result.results] == ["failed", "validated"]
    assert result.results[0].issues[0].code == "validation_internal_error"
    assert "secret" not in result.results[0].issues[0].message


def test_validator_exception_fails_one_paper_and_continues(tmp_path: Path) -> None:
    items = _inputs(2)
    def throwing_validator(candidate, document, packet, analysis_result, **kwargs):
        if candidate.paper_id == items[0].candidate.paper_id:
            raise RuntimeError("private path C:/Users/example/.env")
        from zotero_arxiv_daily.analysis.validator import validate_paper
        return validate_paper(candidate, document, packet, analysis_result, **kwargs)

    dependencies = ValidationDependencies(
        cache=ValidationCache(tmp_path / "validation"),
        clock=lambda: NOW,
        validator=throwing_validator,
    )
    result = build_validation_batch(
        _candidate_batch(items),
        _document_batch(items),
        tuple(item.packet for item in items),
        _analysis_batch(items),
        ValidationSettings(),
        dependencies,
    )

    assert [item.status for item in result.results] == ["failed", "validated"]
    assert result.results[0].issues[0].code == "validation_internal_error"


def test_validation_config_is_non_secret_and_capped() -> None:
    config = OmegaConf.load(Path("config/base.yaml"))
    assert config.validation_pipeline.validator_version == "stage4-v1"
    assert int(config.validation_pipeline.max_papers) == 5
    assert str(config.validation_pipeline.cache_root) == "cache/validation"
