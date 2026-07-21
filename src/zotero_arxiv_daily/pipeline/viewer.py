from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import yaml
from zotero_arxiv_daily.pipeline.validation import run_offline_fixture as run_validation_fixture
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import BuildManifest, ViewerSettings


def run_offline_fixture(
    fixture_path: Path,
    *,
    output_root: Path,
    dry_run: bool,
    evidence_roots: tuple[Path, ...] = (),
) -> BuildManifest:
    validated = run_validation_fixture(
        fixture_path,
        dry_run=True,
        cache_root=output_root / ".validation-cache",
    )
    return StaticViewerBuilder(
        ViewerSettings(output_root=output_root, evidence_roots=evidence_roots)
    ).build(
        validated.results,
        batch_label="offline-fixture",
        dry_run=dry_run,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline validated paper reader")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/base.yaml"))
    parser.add_argument("--evidence-root", type=Path, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        configured_roots = load_configured_evidence_roots(args.config)
    except ValueError as error:
        parser.error(str(error))
    manifest = run_offline_fixture(
        args.offline_fixture,
        output_root=args.output_root,
        dry_run=args.dry_run,
        evidence_roots=(*configured_roots, *args.evidence_root),
    )
    print(f"published={manifest.published_count} partial={manifest.partial_count} build={manifest.build_version}")
    return 0


def load_configured_evidence_roots(config_path: Path) -> tuple[Path, ...]:
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError("viewer configuration could not be read") from error
    if not isinstance(loaded, dict):
        raise ValueError("viewer configuration must be a mapping")
    section = loaded.get("viewer_pipeline", {})
    if not isinstance(section, dict):
        raise ValueError("viewer_pipeline configuration must be a mapping")
    values = section.get("evidence_roots", ())
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise ValueError("viewer_pipeline.evidence_roots must be a list of non-empty paths")
    return tuple(Path(value) for value in values)


if __name__ == "__main__":
    raise SystemExit(main())
