from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zotero_arxiv_daily.analysis.analyzer import (
    AnalysisDependencies,
    AnalysisSettings,
    analyze_paper,
)
from zotero_arxiv_daily.analysis.document_schemas import (
    BoundingBox,
    DocumentBatchResult,
    DocumentBlock,
    DocumentGraph,
    DocumentPage,
    PdfArtifact,
    SectionNode,
    SourceMapping,
)
from zotero_arxiv_daily.analysis.paper_schemas import (
    AnalysisBatchResult,
    AnalysisIssue,
    PaperAnalysisResult,
)
from zotero_arxiv_daily.analysis.schemas import CandidateBatch, CandidatePaper


def build_analysis_batch(
    candidates: CandidateBatch,
    documents: DocumentBatchResult,
    settings: AnalysisSettings,
    dependencies: AnalysisDependencies,
) -> AnalysisBatchResult:
    if candidates.run_id != documents.run_id:
        raise ValueError("candidate and document batch run_id values must match")

    papers = {paper.paper_id: paper for paper in candidates.candidates}
    document_results = {item.paper_id: item for item in documents.results}
    results: list[PaperAnalysisResult] = []
    expensive_calls = 0
    for paper_id in candidates.selected_for_full_analysis[:5]:
        paper = papers.get(paper_id)
        if paper is None:
            results.append(_unavailable(paper_id, "failed", "analysis_candidate_missing"))
            continue
        document_result = document_results.get(paper_id)
        if document_result is None:
            results.append(_unavailable(paper_id, "skipped", "analysis_document_missing"))
            continue
        if document_result.status not in {"success", "partial"}:
            results.append(_unavailable(paper_id, "failed", "analysis_document_failed"))
            continue
        document = document_result.document
        if document is None or document.pdf.source_url != paper.pdf_url:
            results.append(_unavailable(paper_id, "failed", "analysis_document_mismatch"))
            continue

        before = _call_count(dependencies.client)
        result = analyze_paper(paper, document, settings, dependencies)
        after = _call_count(dependencies.client)
        if getattr(dependencies.client, "is_expensive", True):
            if before is not None and after is not None:
                expensive_calls += max(after - before, 0)
            elif not result.cache_hit:
                expensive_calls += 1
        results.append(result)

    return AnalysisBatchResult(
        run_id=candidates.run_id,
        created_at=dependencies.clock(),
        results=tuple(results),
        expensive_call_count=expensive_calls,
        cache_hit_count=sum(result.cache_hit for result in results),
    )


def run_offline_fixture(
    fixture_path: Path,
    *,
    dry_run: bool,
    cache_root: Path,
) -> AnalysisBatchResult:
    if not dry_run:
        raise ValueError("offline fixture execution requires dry_run=true")
    fixture = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    paper = CandidatePaper.model_validate(fixture["candidate"])
    document = _offline_document(fixture["document"])
    draft = _resolve_fixture_response(fixture["fake_structured_response"], paper, document)
    candidate_batch = _offline_candidate_batch(fixture, paper)
    document_batch = DocumentBatchResult(
        run_id=candidate_batch.run_id,
        created_at=_fixture_time(fixture),
        results=(
            {
                "paper_id": paper.paper_id,
                "status": "success",
                "document": document,
                "processing_seconds": 0,
            },
        ),
    )
    dependencies = AnalysisDependencies(
        client=_OfflineFakeClient(draft),
        cache=_MemoryAnalysisCache(),
        clock=lambda: _fixture_time(fixture),
        sleep=lambda _: None,
    )
    del cache_root  # dry-run intentionally never creates or reads a cache path
    return build_analysis_batch(
        candidate_batch,
        document_batch,
        AnalysisSettings(max_attempts=1),
        dependencies,
    )


class _OfflineFakeClient:
    model_identity = "fake:offline-stage3"
    is_expensive = False

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls = 0

    def generate(self, request) -> str:
        self.calls += 1
        return json.dumps(self.response, ensure_ascii=False)


class _MemoryAnalysisCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def read(self, identity):
        return self.values.get(identity.cache_key)

    def write(self, identity, analysis):
        self.values[identity.cache_key] = analysis
        return None


def _call_count(client: Any) -> int | None:
    value = getattr(client, "calls", None)
    return value if isinstance(value, int) else None


def _unavailable(
    paper_id: str, status: str, code: str
) -> PaperAnalysisResult:
    return PaperAnalysisResult(
        paper_id=paper_id,
        status=status,
        analysis=None,
        issues=(
            AnalysisIssue(
                code=code,
                severity="error" if status == "failed" else "warning",
                message="Stage 3 skipped this paper because its Stage 2 input was unavailable",
            ),
        ),
        processing_seconds=0,
    )


def _fixture_time(fixture: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(fixture["now"]).replace("Z", "+00:00")).astimezone(UTC)


def _offline_candidate_batch(
    fixture: dict[str, Any], paper: CandidatePaper
) -> CandidateBatch:
    now = _fixture_time(fixture)
    return CandidateBatch.model_validate(
        {
            "run_id": fixture["run_id"],
            "created_at": now,
            "retrieved_at": now,
            "categories": list(paper.categories),
            "config_hash": "b" * 64,
            "interest_corpus_fingerprint": "c" * 64,
            "candidates": [paper],
            "rankings": [
                {
                    "paper_id": paper.paper_id,
                    "embedding_score": 1,
                    "final_score": 1,
                    "rank": 1,
                    "reason": "offline fixture",
                    "model_versions": {
                        "provider": "offline-fixture",
                        "model": "fixture-v1",
                        "task": "retrieval",
                        "scorer": "cosine",
                        "embedding_identity_hash": "d" * 64,
                    },
                }
            ],
            "selected_for_llm": [paper.paper_id],
            "selected_for_full_analysis": [paper.paper_id],
            "counts": {"retrieved": 1, "deduplicated": 1, "invalid": 0, "excluded": 0},
            "limits": {
                "candidate_pool_size": 30,
                "llm_rerank_limit": 15,
                "full_analysis_limit": 5,
            },
        }
    )


def _offline_document(data: dict[str, Any]) -> DocumentGraph:
    box = BoundingBox(left=10, top=10, right=90, bottom=30)
    mapping = SourceMapping(
        parser="fixture",
        parser_version="1",
        source_item_id="fixture-method-text",
        pdf_page=1,
        bbox=box,
        mapping_method="fixture",
        confidence=1,
    )
    block = DocumentBlock(
        block_id="method-text",
        block_type="text",
        text=data["evidence_text"],
        pdf_page=1,
        bbox=box,
        reading_order=0,
        section_id="method",
        source_mapping=mapping,
    )
    return DocumentGraph(
        parser="fixture",
        parser_version="1",
        mapper_version="1",
        config_version="1",
        content_fingerprint=data["content_fingerprint"],
        pdf=PdfArtifact(
            source_url=data["source_url"],
            local_path=Path("cache/documents/offline-fixture.pdf"),
            sha256=data["pdf_sha256"],
            byte_size=1,
            content_type="application/pdf",
            page_count=1,
            downloaded_at=datetime(2026, 7, 20, tzinfo=UTC),
            cache_hit=False,
        ),
        pages=(
            DocumentPage(
                pdf_page=1,
                width=100,
                height=100,
                text_layer_status="present",
                block_ids=(block.block_id,),
            ),
        ),
        blocks=(block,),
        sections=(
            SectionNode(
                section_id="method",
                title=data["section_title"],
                level=1,
                parent_id=None,
                block_ids=(block.block_id,),
                start_pdf_page=1,
                end_pdf_page=1,
                source_mapping=mapping,
                confidence=1,
            ),
        ),
        visuals=(),
    )


def _resolve_fixture_response(
    response: dict[str, Any], paper: CandidatePaper, document: DocumentGraph
) -> dict[str, Any]:
    from zotero_arxiv_daily.documents.evidence import build_evidence_packet

    packet = build_evidence_packet(paper.paper_id, document, AnalysisSettings().evidence)
    text_id = packet.candidates[0].evidence_id
    encoded = json.dumps(response, ensure_ascii=False)
    encoded = encoded.replace("$TEXT_EVIDENCE_ID", text_id)
    return json.loads(encoded)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Stage 3 analysis pipeline")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=Path("cache/analysis"))
    args = parser.parse_args(argv)
    result = run_offline_fixture(
        args.offline_fixture,
        dry_run=args.dry_run,
        cache_root=args.cache_root,
    )
    counts = {
        status: sum(item.status == status for item in result.results)
        for status in ("success", "partial", "failed", "skipped")
    }
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                **counts,
                "expensive_call_count": result.expensive_call_count,
                "cache_hit_count": result.cache_hit_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
