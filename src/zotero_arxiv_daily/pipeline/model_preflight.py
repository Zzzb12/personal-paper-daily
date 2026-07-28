from __future__ import annotations

import argparse
import importlib
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from zotero_arxiv_daily.candidates.ranking import (
    TransformersMeanPoolingEmbeddingProvider,
)


_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ERROR_CODES = {
    "CpuVisionRuntimeError": "cpu_vision_runtime_failed",
    "EntryNotFoundError": "reranker_hub_access_failed",
    "GatedRepoError": "reranker_hub_access_failed",
    "HfHubHTTPError": "reranker_hub_access_failed",
    "LocalEntryNotFoundError": "reranker_hub_access_failed",
    "RepositoryNotFoundError": "reranker_hub_access_failed",
    "RevisionNotFoundError": "reranker_hub_access_failed",
    "ImportError": "reranker_import_failed",
    "ModuleNotFoundError": "reranker_import_failed",
    "OSError": "reranker_model_io_failed",
    "RuntimeError": "reranker_runtime_failed",
    "ValueError": "reranker_config_failed",
}


class CpuVisionRuntimeError(RuntimeError):
    """Fixed-detail failure for the shared Torch/torchvision CPU runtime."""


def require_cpu_vision_runtime(
    *,
    importer: Callable[[str], Any] = importlib.import_module,
) -> None:
    try:
        torch = importer("torch")
        torchvision = importer("torchvision")
        operations = importer("torchvision.ops")
        if getattr(getattr(torch, "version", None), "cuda", None) is not None:
            raise RuntimeError
        if not callable(getattr(operations, "nms", None)):
            raise RuntimeError
        boxes = torch.empty((0, 4), dtype=torch.float32)
        scores = torch.empty((0,), dtype=torch.float32)
        selected = operations.nms(boxes, scores, 0.5)
        if selected.numel() != 0:
            raise RuntimeError
        del torchvision
    except Exception as error:
        raise CpuVisionRuntimeError from error


def _merged_config(config_dir: Path) -> Any:
    base = OmegaConf.load(config_dir / "base.yaml")
    custom = config_dir / "custom.yaml"
    return OmegaConf.merge(base, OmegaConf.load(custom) if custom.exists() else {})


def _safe_relative_cache_folder(value: object) -> Path:
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("reranker cache folder must be a safe relative path")
    return path


def prepare_local_reranker(
    config_dir: Path,
    *,
    provider_factory: Callable[..., Any] = TransformersMeanPoolingEmbeddingProvider,
    runtime_probe: Callable[[], None] = require_cpu_vision_runtime,
) -> None:
    config = _merged_config(config_dir)
    local = config.reranker.local
    revision = str(local.revision)
    if _COMMIT_REVISION.fullmatch(revision) is None:
        raise ValueError("reranker revision must be a full commit SHA")
    cache_folder = _safe_relative_cache_folder(local.cache_folder)
    raw_encode = OmegaConf.to_container(local.encode_kwargs, resolve=True)
    encode_kwargs = dict(raw_encode or {})
    prompt_name = encode_kwargs.pop("prompt_name", None)
    runtime_probe()
    provider = provider_factory(
        model=str(local.model),
        revision=revision,
        cache_folder=cache_folder,
        task=str(local.get("task", "retrieval")),
        prompt_name=prompt_name,
        trust_remote_code=bool(local.get("trust_remote_code", False)),
        encode_kwargs=encode_kwargs,
        max_sequence_length=int(local.get("max_sequence_length", 512)),
    )
    try:
        return None
    finally:
        provider.close()


def _safe_error_code(error: Exception) -> str:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        code = _SAFE_ERROR_CODES.get(type(current).__name__)
        if code is not None:
            return code
        current = current.__cause__ or current.__context__
    return "reranker_unknown_failed"


def main(
    argv: Sequence[str] | None = None,
    *,
    preparer: Callable[[Path], None] = prepare_local_reranker,
) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the version-declared public reranker model",
        allow_abbrev=False,
    )
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    args = parser.parse_args(argv)
    try:
        preparer(args.config_dir.resolve())
    except Exception as error:
        parser.error(
            "version-declared reranker could not be prepared; "
            f"error_code={_safe_error_code(error)}"
        )
    print("reranker_model_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
