from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


class Encoder(Protocol):
    def encode(self, texts: Sequence[str], **kwargs: object) -> object: ...


@dataclass(frozen=True)
class ModelSpec:
    model: str
    revision: str
    trust_remote_code: bool
    encode_kwargs: dict[str, object]


MODEL_SPECS = {
    "reference": ModelSpec(
        model="jinaai/jina-embeddings-v5-text-nano-retrieval",
        revision="ac5d898c8d382b17167c33e5c8af644a3519b47d",
        trust_remote_code=True,
        encode_kwargs={
            "normalize_embeddings": True,
            "prompt_name": "document",
            "task": "retrieval",
        },
    ),
    "replacement": ModelSpec(
        model="sentence-transformers/multi-qa-MiniLM-L6-cos-v1",
        revision="b207367332321f8e44f96e224ef15bc607f4dbf0",
        trust_remote_code=False,
        encode_kwargs={"normalize_embeddings": True},
    ),
}


def canonical_fixture_hash(fixture: object) -> str:
    payload = json.dumps(
        fixture,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_rows(values: np.ndarray) -> np.ndarray:
    if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
        raise ValueError("embedding arrays must be non-empty matrices")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 0):
        raise ValueError("embedding rows must have non-zero norm")
    return values / norms


def retrieval_metrics(
    interest_vectors: np.ndarray,
    candidate_vectors: np.ndarray,
    *,
    labels: Sequence[int],
    top_k: int,
) -> dict[str, object]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    normalized_interests = _normalized_rows(
        np.asarray(interest_vectors, dtype=np.float32)
    )
    normalized_candidates = _normalized_rows(
        np.asarray(candidate_vectors, dtype=np.float32)
    )
    if normalized_interests.shape[1] != normalized_candidates.shape[1]:
        raise ValueError("interest and candidate dimensions must match")
    if normalized_candidates.shape[0] != len(labels):
        raise ValueError("candidate labels must match candidate rows")
    if any(label not in (0, 1) for label in labels):
        raise ValueError("candidate labels must be binary")
    relevant_total = sum(labels)
    if relevant_total <= 0:
        raise ValueError("at least one relevant candidate is required")

    scores = (normalized_candidates @ normalized_interests.T).mean(axis=1)
    order = np.argsort(-scores, kind="stable")
    selected_count = min(top_k, len(labels))
    top_labels = [int(labels[index]) for index in order[:selected_count]]
    relevant_selected = sum(top_labels)
    precision = relevant_selected / selected_count
    recall = relevant_selected / relevant_total
    dcg = sum(
        label / math.log2(rank + 2)
        for rank, label in enumerate(top_labels)
    )
    ideal_count = min(relevant_total, selected_count)
    ideal_dcg = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_count))
    prefix = f"_at_{top_k}_ppm"
    return {
        f"ndcg{prefix}": int(round(dcg / ideal_dcg * 1_000_000)),
        f"precision{prefix}": int(round(precision * 1_000_000)),
        f"recall{prefix}": int(round(recall * 1_000_000)),
        f"top{top_k}_labels": top_labels,
    }


def _default_model_factory(
    spec: ModelSpec,
    cache_folder: Path | None,
) -> Encoder:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        spec.model,
        revision=spec.revision,
        trust_remote_code=spec.trust_remote_code,
        cache_folder=None if cache_folder is None else str(cache_folder),
        local_files_only=True,
    )


def _encode_model(
    spec: ModelSpec,
    interests: Sequence[str],
    candidates: Sequence[str],
    *,
    cache_folder: Path | None,
    model_factory: Callable[[ModelSpec, Path | None], Encoder],
) -> tuple[np.ndarray, np.ndarray]:
    encoder = model_factory(spec, cache_folder)
    try:
        interest_vectors = np.asarray(
            encoder.encode(interests, **spec.encode_kwargs),
            dtype=np.float32,
        )
        candidate_vectors = np.asarray(
            encoder.encode(candidates, **spec.encode_kwargs),
            dtype=np.float32,
        )
        return interest_vectors, candidate_vectors
    finally:
        del encoder
        gc.collect()


def compare_report(
    report_path: Path,
    *,
    reference_cache_folder: Path | None = None,
    replacement_cache_folder: Path | None = None,
    model_factory: Callable[
        [ModelSpec, Path | None],
        Encoder,
    ] = _default_model_factory,
) -> dict[str, object]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fixture = report["fixture_data"]
    if canonical_fixture_hash(fixture) != report["fixture_sha256"]:
        raise ValueError("fixture hash differs from the tracked report")
    interests = tuple(str(value) for value in fixture["interests"])
    candidates = tuple(str(row[0]) for row in fixture["candidates"])
    labels = tuple(int(row[1]) for row in fixture["candidates"])
    cache_folders = {
        "reference": reference_cache_folder,
        "replacement": replacement_cache_folder,
    }

    computed: dict[str, dict[str, object]] = {}
    for name, spec in MODEL_SPECS.items():
        recorded = report["models"][name]
        if (
            recorded["model"] != spec.model
            or recorded["revision"] != spec.revision
            or recorded["trust_remote_code"] is not spec.trust_remote_code
            or recorded["encode_kwargs"] != spec.encode_kwargs
        ):
            raise ValueError(f"{name} model identity differs from recomputation code")
        interest_vectors, candidate_vectors = _encode_model(
            spec,
            interests,
            candidates,
            cache_folder=cache_folders[name],
            model_factory=model_factory,
        )
        metrics = retrieval_metrics(
            interest_vectors,
            candidate_vectors,
            labels=labels,
            top_k=5,
        )
        metrics["dimension"] = int(candidate_vectors.shape[1])
        for key in (
            "dimension",
            "ndcg_at_5_ppm",
            "precision_at_5_ppm",
            "recall_at_5_ppm",
        ):
            if metrics[key] != recorded[key]:
                raise ValueError(f"{name} {key} differs from the tracked report")
        computed[name] = metrics

    metric_names = (
        "ndcg_at_5_ppm",
        "precision_at_5_ppm",
        "recall_at_5_ppm",
    )
    verdict = (
        "non_regression_pass"
        if all(
            computed["replacement"][metric] >= computed["reference"][metric]
            for metric in metric_names
        )
        else "non_regression_fail"
    )
    if verdict != report["verdict"]:
        raise ValueError("computed verdict differs from the tracked report")
    return {
        "fixture_sha256": report["fixture_sha256"],
        "models": computed,
        "verdict": verdict,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Strictly offline recomputation of the tracked reranker comparison",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "docs/benchmarks/2026-07-28-reranker-linux-repair.json"
        ),
    )
    parser.add_argument("--reference-cache-folder", type=Path)
    parser.add_argument(
        "--replacement-cache-folder",
        type=Path,
        default=Path("models/reranker"),
    )
    args = parser.parse_args(argv)
    result = compare_report(
        args.report.resolve(),
        reference_cache_folder=(
            None
            if args.reference_cache_folder is None
            else args.reference_cache_folder.resolve()
        ),
        replacement_cache_folder=args.replacement_cache_folder.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
