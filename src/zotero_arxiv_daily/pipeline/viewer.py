from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from zotero_arxiv_daily.pipeline.validation import run_offline_fixture as run_validation_fixture
from zotero_arxiv_daily.viewer.builder import StaticViewerBuilder
from zotero_arxiv_daily.viewer.schemas import BuildManifest, ViewerSettings


def run_offline_fixture(
    fixture_path: Path,
    *,
    output_root: Path,
    dry_run: bool,
) -> BuildManifest:
    validated = run_validation_fixture(
        fixture_path,
        dry_run=True,
        cache_root=output_root / ".validation-cache",
    )
    return StaticViewerBuilder(ViewerSettings(output_root=output_root)).build(
        validated.results,
        batch_label="offline-fixture",
        dry_run=dry_run,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an offline validated paper reader")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    manifest = run_offline_fixture(args.offline_fixture, output_root=args.output_root, dry_run=args.dry_run)
    print(f"published={manifest.published_count} partial={manifest.partial_count} build={manifest.build_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
