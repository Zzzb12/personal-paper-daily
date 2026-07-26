from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from zotero_arxiv_daily.pipeline import daily
from zotero_arxiv_daily.pipeline import candidates
from zotero_arxiv_daily.pipeline.daily import DailyFactoryContext
from tests.pipeline.test_daily import _dependencies
from tests.pipeline.test_validation import _inputs


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=UTC)
FIXTURE = Path("tests/fixtures/evidence/stage4_golden.json")


def _arguments(tmp_path: Path, *, trigger: str = "local") -> list[str]:
    run_root = tmp_path / trigger / "run"
    return [
        "--trigger",
        trigger,
        "--mode",
        "dry-run",
        "--offline-fixture",
        str(FIXTURE),
        "--run-id",
        f"stage7-{trigger}",
        "--run-root",
        str(run_root),
        "--viewer-output",
        str(run_root / "viewer"),
        "--manifest-output",
        str(run_root / "run-manifest.json"),
    ]


@pytest.mark.parametrize("trigger", ("scheduled", "manual", "local"))
def test_cli_supports_all_triggers_through_the_same_offline_pipeline(
    tmp_path: Path, trigger: str
) -> None:
    contexts: list[DailyFactoryContext] = []

    def offline_factory(context: DailyFactoryContext):
        contexts.append(context)
        return _dependencies(context.run_root.parent, _inputs(1))

    result = daily.main(
        _arguments(tmp_path, trigger=trigger),
        environ={},
        clock=lambda: NOW,
        offline_factory=offline_factory,
        production_factory=lambda context: pytest.fail("production factory was called"),
    )

    assert result == 0
    assert contexts[0].settings.trigger == trigger
    assert contexts[0].settings.dry_run is True
    assert contexts[0].settings.send_feishu is False


def test_default_dry_run_constructs_no_production_network_or_send_boundary(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    def offline_factory(context: DailyFactoryContext):
        dependencies = _dependencies(context.run_root.parent, _inputs(1), order=events)
        dependencies.close_callbacks = (lambda: events.append("closed"),)
        return dependencies

    daily.main(
        _arguments(tmp_path),
        environ={"LLM_API_KEY": "must-not-be-read"},
        clock=lambda: NOW,
        offline_factory=offline_factory,
        production_factory=lambda context: pytest.fail("live clients were constructed"),
    )

    assert "send_feishu" not in events
    assert events[-1] == "closed"


def test_offline_daily_mode_does_not_construct_or_read_a_feedback_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        candidates.FeedbackProjectionLoader,
        "load",
        lambda self: pytest.fail("dry-run must not read feedback storage"),
    )

    result = daily.main(
        _arguments(tmp_path),
        environ={},
        clock=lambda: NOW,
        production_factory=lambda context: pytest.fail("production factory was called"),
    )

    assert result == 0


@pytest.mark.parametrize("abbreviation", ("--trig", "--off", "--send-fei"))
def test_cli_rejects_dangerous_option_abbreviations(
    tmp_path: Path, abbreviation: str
) -> None:
    with pytest.raises(SystemExit) as error:
        daily.main([abbreviation, "local", *_arguments(tmp_path)], environ={})

    assert error.value.code == 2


def test_dry_run_requires_fixture_and_forbids_send(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as missing:
        daily.main(
            [
                "--mode",
                "dry-run",
                "--run-root",
                str(tmp_path / "run"),
            ],
            environ={},
        )
    assert missing.value.code == 2

    with pytest.raises(SystemExit) as unsafe:
        daily.main([*_arguments(tmp_path), "--send-feishu"], environ={})
    assert unsafe.value.code == 2


def test_live_mode_reports_only_missing_environment_variable_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_value = "value-that-must-never-appear"
    with pytest.raises(SystemExit) as error:
        daily.main(
            [
                "--mode",
                "live",
                "--run-root",
                str(tmp_path / "run"),
            ],
            environ={"ZOTERO_ID": secret_value},
        )

    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "ZOTERO_KEY" in stderr
    assert "LLM_API_KEY" in stderr
    assert "PAPER_DAILY_SITE_URL" in stderr
    assert secret_value not in stderr


def test_live_send_requires_exact_flag_and_all_feishu_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_environment = {
        "ZOTERO_ID": "configured",
        "ZOTERO_KEY": "configured",
        "LLM_API_KEY": "configured",
        "LLM_BASE_URL": "https://llm.example.test/v1",
        "LLM_MODEL": "model-v1",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/",
    }
    with pytest.raises(SystemExit):
        daily.main(
            [
                "--mode",
                "live",
                "--send-feishu",
                "--run-root",
                str(tmp_path / "run"),
            ],
            environ=base_environment,
        )

    stderr = capsys.readouterr().err
    assert "FEISHU_APP_ID" in stderr
    assert "FEISHU_APP_SECRET" in stderr
    assert "FEISHU_CHAT_ID" in stderr
    assert "configured" not in stderr


def test_live_mode_uses_injected_production_factory_without_printing_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contexts: list[DailyFactoryContext] = []
    environment = {
        "ZOTERO_ID": "private-zotero-value",
        "ZOTERO_KEY": "private-zotero-key",
        "LLM_API_KEY": "private-llm-key",
        "LLM_BASE_URL": "https://llm.example.test/v1",
        "LLM_MODEL": "model-v1",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/",
    }

    def production_factory(context: DailyFactoryContext):
        contexts.append(context)
        return _dependencies(context.run_root.parent, _inputs(1))

    arguments = _arguments(tmp_path)
    mode_index = arguments.index("dry-run")
    arguments[mode_index] = "live"
    fixture_index = arguments.index("--offline-fixture")
    del arguments[fixture_index : fixture_index + 2]
    result = daily.main(
        arguments,
        environ=environment,
        clock=lambda: NOW,
        offline_factory=lambda context: pytest.fail("offline factory was called"),
        production_factory=production_factory,
    )

    assert result == 0
    assert contexts[0].settings.dry_run is False
    output = capsys.readouterr().out
    assert "status=success" in output
    assert all(value not in output for value in environment.values())


def test_docling_preflight_rejects_missing_empty_or_linked_model_roots(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="Docling model artifacts"):
        daily._require_docling_artifacts(missing)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="Docling model artifacts"):
        daily._require_docling_artifacts(empty)

    prepared = tmp_path / "prepared"
    for name in daily._REQUIRED_DOCLING_MODEL_DIRECTORIES:
        directory = prepared / name
        directory.mkdir(parents=True)
        (directory / "model.bin").write_bytes(b"fixture")
    assert daily._require_docling_artifacts(prepared) == prepared.resolve()
