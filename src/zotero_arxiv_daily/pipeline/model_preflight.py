from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

from zotero_arxiv_daily.candidates.ranking import (
    SentenceTransformerEmbeddingProvider,
)


_COMMIT_REVISION = re.compile(r"^[0-9a-f]{40}$")


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
    provider_factory: Callable[..., Any] = SentenceTransformerEmbeddingProvider,
) -> None:
    config = _merged_config(config_dir)
    local = config.reranker.local
    revision = str(local.revision)
    if _COMMIT_REVISION.fullmatch(revision) is None:
        raise ValueError("reranker revision must be a full commit SHA")
    cache_folder = _safe_relative_cache_folder(local.cache_folder)
    raw_encode = OmegaConf.to_container(local.encode_kwargs, resolve=True)
    encode_kwargs = dict(raw_encode or {})
    task = str(encode_kwargs.pop("task", "retrieval"))
    prompt_name = encode_kwargs.pop("prompt_name", None)
    provider = provider_factory(
        model=str(local.model),
        revision=revision,
        cache_folder=cache_folder,
        task=task,
        prompt_name=prompt_name,
        encode_kwargs=encode_kwargs,
    )
    try:
        return None
    finally:
        provider.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the version-declared public reranker model",
        allow_abbrev=False,
    )
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    args = parser.parse_args(argv)
    try:
        prepare_local_reranker(args.config_dir.resolve())
    except Exception:
        parser.error("version-declared reranker could not be prepared")
    print("reranker_model_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
