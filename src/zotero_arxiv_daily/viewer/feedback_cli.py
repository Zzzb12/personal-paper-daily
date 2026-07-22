"""Explicit, local-only importer for private reader-feedback bundles."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackImportResult,
    FeedbackStore,
    FeedbackStoreSafetyError,
    load_feedback_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", allow_abbrev=False)
    importer.add_argument("--bundle", required=True)
    importer.add_argument("--store", required=True)
    importer.add_argument("--dry-run", action="store_true")
    return parser


def _print_result(result: FeedbackImportResult, *, dry_run: bool) -> None:
    print("status=ok")
    print(f"dry_run={'true' if dry_run else 'false'}")
    print(f"applied_count={result.applied_count}")
    print(f"changed_count={result.changed_count}")
    print(f"duplicate_count={result.duplicate_count}")
    print(f"stale_count={result.stale_count}")
    print(f"conflict_count={result.conflict_count}")
    print(f"bundle_duplicate_count={result.bundle_duplicate_count}")


def main(arguments: Sequence[str] | None = None) -> int:
    namespace = _parser().parse_args(arguments)
    root = Path.cwd()
    try:
        bundle = load_feedback_bundle(namespace.bundle, root=root)
        result = FeedbackStore(namespace.store, root=root).import_bundle(
            bundle, dry_run=namespace.dry_run
        )
    except FeedbackStoreSafetyError:
        print("status=error")
        return 1
    _print_result(result, dry_run=namespace.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
