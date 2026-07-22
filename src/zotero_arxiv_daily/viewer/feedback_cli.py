"""Explicit, local-only importer for private reader-feedback bundles."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Sequence, TextIO

from zotero_arxiv_daily.viewer.feedback import (
    FeedbackBundle,
    FeedbackImportResult,
    FeedbackStore,
    load_feedback_bundle,
)


DEFAULT_PRIVATE_ROOT = Path("data/private-feedback")


class _CliInputError(ValueError):
    pass


class _FixedErrorParser(argparse.ArgumentParser):
    """Never render argparse's dynamic input values at the CLI boundary."""

    def error(self, message: str) -> None:
        raise _CliInputError()

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise _CliInputError()


BundleLoader = Callable[..., FeedbackBundle]
StoreFactory = Callable[..., FeedbackStore]


def _parser() -> argparse.ArgumentParser:
    parser = _FixedErrorParser(allow_abbrev=False, add_help=False)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_FixedErrorParser)
    importer = commands.add_parser("import", allow_abbrev=False, add_help=False)
    importer.add_argument("--bundle", required=True)
    importer.add_argument("--store", required=True)
    importer.add_argument("--dry-run", action="store_true")
    return parser


def _print_result(result: FeedbackImportResult, *, dry_run: bool, stdout: TextIO) -> None:
    stdout.write("status=ok\n")
    stdout.write(f"dry_run={'true' if dry_run else 'false'}\n")
    stdout.write(f"applied_count={result.applied_count}\n")
    stdout.write(f"changed_count={result.changed_count}\n")
    stdout.write(f"duplicate_count={result.duplicate_count}\n")
    stdout.write(f"stale_count={result.stale_count}\n")
    stdout.write(f"conflict_count={result.conflict_count}\n")
    stdout.write(f"bundle_duplicate_count={result.bundle_duplicate_count}\n")


def _private_store_path(value: str, *, private_root: Path) -> Path:
    """Resolve only a store location that remains inside the trusted private anchor."""
    return FeedbackStore(value, root=private_root).checked_path()


def run_import(
    arguments: Sequence[str] | None,
    *,
    private_root: Path,
    bundle_loader: BundleLoader,
    store_factory: StoreFactory,
    stdout: TextIO,
) -> int:
    namespace = _parser().parse_args(arguments)
    store_path = _private_store_path(namespace.store, private_root=private_root)
    bundle = bundle_loader(Path(namespace.bundle), root=private_root)
    result = store_factory(store_path, root=private_root).import_bundle(
        bundle, dry_run=namespace.dry_run
    )
    _print_result(result, dry_run=namespace.dry_run, stdout=stdout)
    return 0


def main(
    arguments: Sequence[str] | None = None,
    *,
    private_root: Path = DEFAULT_PRIVATE_ROOT,
    bundle_loader: BundleLoader = load_feedback_bundle,
    store_factory: StoreFactory = FeedbackStore,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run with an explicit trusted private root and fixed, non-sensitive output."""
    output = stdout or sys.stdout
    _ = stderr or sys.stderr
    try:
        resolved_private_root = Path(private_root).resolve(strict=False)
        return run_import(
            arguments,
            private_root=resolved_private_root,
            bundle_loader=bundle_loader,
            store_factory=store_factory,
            stdout=output,
        )
    except (Exception,):
        output.write("status=error\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
