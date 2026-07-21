from __future__ import annotations

import argparse
import os
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a safe Feishu card preview")
    parser.add_argument("--offline-fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)

    settings: FeishuSettings | None = None
    if args.send:
        try:
            settings = FeishuSettings.from_environment(os.environ)
        except ValueError as error:
            parser.error(str(error))

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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{rendered}\n", encoding="utf-8")

    if settings is None:
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
