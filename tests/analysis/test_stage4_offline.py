from __future__ import annotations

import json
import socket
from pathlib import Path

from zotero_arxiv_daily.pipeline.validation import main, run_offline_fixture


FIXTURE = Path("tests/fixtures/evidence/stage4_golden.json")


def test_stage4_offline_golden_is_zero_network_zero_credentials_zero_writes(
    tmp_path: Path, monkeypatch
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    result = run_offline_fixture(
        FIXTURE,
        dry_run=True,
        cache_root=tmp_path / "cache",
    )

    assert result.results[0].status == "validated"
    assert result.results[0].validated.report.publication_eligibility == "eligible"
    assert result.cache_hit_count == 0
    assert not (tmp_path / "cache").exists()


def test_stage4_offline_cli_prints_safe_counts_only(tmp_path: Path, capsys) -> None:
    exit_code = main(
        [
            "--dry-run",
            "--offline-fixture",
            str(FIXTURE),
            "--cache-root",
            str(tmp_path / "cache"),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output == {
        "run_id": "stage4-offline",
        "validator_version": "stage4-v1",
        "valid": 1,
        "partial": 0,
        "invalid": 0,
        "failed": 0,
        "skipped": 0,
        "eligible": 1,
        "cache_hit_count": 0,
    }
    assert not (tmp_path / "cache").exists()
