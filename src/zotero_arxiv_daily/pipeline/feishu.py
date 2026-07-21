from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from zotero_arxiv_daily.delivery.feishu import (
    DigestPolicy,
    FeishuClient,
    FeishuRenderer,
    HttpxTransport,
)
from zotero_arxiv_daily.delivery.schemas import FeishuSettings
from zotero_arxiv_daily.pipeline.validation import run_offline_fixture as run_validation_fixture


_PREVIEW_CHAT_ID = "oc_00000000000000000000000000000000"
_PREVIEW_SITE_URL = "https://papers.example.test/offline-preview/"


def _write_preview_atomic(output: Path, rendered: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
            newline="\n",
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(f"{rendered}\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, output)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a safe Feishu card preview", allow_abbrev=False
    )
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)

    if args.offline_fixture.resolve() == args.output.resolve():
        parser.error("offline fixture and output must resolve to different paths")

    settings: FeishuSettings | None = None
    if args.send:
        try:
            settings = FeishuSettings.from_environment(os.environ)
        except ValueError:
            parser.error("invalid Feishu delivery configuration")

    batch = run_validation_fixture(
        args.offline_fixture,
        dry_run=True,
        cache_root=args.output.parent / ".validation-cache",
    )
    request = DigestPolicy.build(
        batch,
        chat_id=settings.chat_id if settings is not None else _PREVIEW_CHAT_ID,
        site_url=settings.site_url if settings is not None else _PREVIEW_SITE_URL,
    )
    rendered = FeishuRenderer.render(request.payload)

    if settings is None:
        _write_preview_atomic(args.output, rendered)
        print(f"preview={args.output} papers={len(request.payload.papers)} sent=false")
        return 0

    transport = HttpxTransport()
    try:
        receipt = FeishuClient(
            settings,
            transport,
            environment=os.environ,
        ).send(request, rendered)
    finally:
        transport.close()
    print(f"message_id={receipt.message_id} request_id={receipt.request_id} sent=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
