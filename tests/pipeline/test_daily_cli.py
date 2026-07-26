from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import shutil

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


def _feedback_config_hash(
    root: Path, *, store_path: str, favorite_delta: float
) -> str:
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "base.yaml").write_text(
        "candidate_pipeline:\n"
        "  feedback:\n"
        f"    store_path: {store_path}\n"
        f"    favorite_delta: {favorite_delta}\n",
        encoding="utf-8",
    )
    return daily._configuration_hash(
        config_dir,
        mode="live",
        fixture=None,
        environment={
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "LLM_MODEL": "model-v1",
            "PAPER_DAILY_SITE_URL": "https://papers.example.test/",
        },
    )


def test_daily_config_hash_omits_feedback_store_path_but_binds_delta_and_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _feedback_config_hash(
        tmp_path / "first", store_path="private-a.json", favorite_delta=0.05
    )
    second = _feedback_config_hash(
        tmp_path / "second", store_path="private-b.json", favorite_delta=0.05
    )
    changed_delta = _feedback_config_hash(
        tmp_path / "delta", store_path="private-a.json", favorite_delta=0.10
    )
    monkeypatch.setattr(
        daily, "FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION", "different-version", raising=False
    )
    changed_implementation = _feedback_config_hash(
        tmp_path / "implementation", store_path="private-a.json", favorite_delta=0.05
    )

    assert first == second
    assert first != changed_delta
    assert first != changed_implementation


def _merged_feedback_hash(
    root: Path, *, custom: str | None = None
) -> str:
    config_dir = root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "base.yaml").write_text(
        "source:\n  arxiv:\n    category: [cs.CV]\n"
        "candidate_pipeline:\n  feedback:\n    favorite_delta: 0.05\n",
        encoding="utf-8",
    )
    if custom is not None:
        (config_dir / "custom.yaml").write_text(custom, encoding="utf-8")
    return daily._configuration_hash(
        config_dir,
        mode="live",
        fixture=None,
        environment={
            "LLM_BASE_URL": "https://llm.example.test/v1",
            "LLM_MODEL": "model-v1",
            "PAPER_DAILY_SITE_URL": "https://papers.example.test/",
        },
    )


def test_daily_config_hash_uses_final_merged_mapping_and_safely_rejects_invalid_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _merged_feedback_hash(tmp_path / "baseline")
    only_private_path = _merged_feedback_hash(
        tmp_path / "path",
        custom="candidate_pipeline:\n  feedback:\n    store_path: private-only.json\n",
    )
    different_path = _merged_feedback_hash(
        tmp_path / "path-other",
        custom="candidate_pipeline:\n  feedback:\n    store_path: private-other.json\n",
    )
    changed_delta = _merged_feedback_hash(
        tmp_path / "delta",
        custom="candidate_pipeline:\n  feedback:\n    favorite_delta: 0.10\n",
    )
    changed_source = _merged_feedback_hash(
        tmp_path / "source",
        custom="source:\n  arxiv:\n    category: [cs.LG]\n",
    )
    monkeypatch.setattr(daily, "FEEDBACK_PROJECTION_IMPLEMENTATION_VERSION", "other", raising=False)
    changed_implementation = _merged_feedback_hash(tmp_path / "implementation")
    invalid_dir = tmp_path / "invalid" / "config"
    invalid_dir.mkdir(parents=True)
    invalid_private = "private-invalid-yaml-content"
    (invalid_dir / "base.yaml").write_text(
        f"candidate_pipeline: [{invalid_private}", encoding="utf-8"
    )

    with pytest.raises(ValueError) as error:
        daily._configuration_hash(
            invalid_dir,
            mode="live",
            fixture=None,
            environment={},
        )

    assert baseline == only_private_path == different_path
    assert baseline != changed_delta
    assert baseline != changed_source
    assert baseline != changed_implementation
    assert str(error.value) == "daily configuration is invalid"
    assert invalid_private not in str(error.value)


def test_prune_empty_configuration_keeps_explicit_empty_lists_hash_distinct_from_absence(
    tmp_path: Path,
) -> None:
    redacted = {"candidate_pipeline": {"feedback": {}}}
    absent = _merged_feedback_hash(tmp_path / "absent")
    explicit_empty_list = _merged_feedback_hash(
        tmp_path / "explicit-empty-list",
        custom="candidate_pipeline:\n  retained_empty_list: []\n",
    )

    assert daily._prune_empty_configuration(redacted) is daily._PRUNED_CONFIGURATION_VALUE
    assert daily._prune_empty_configuration({"source": {"categories": []}}) == {
        "source": {"categories": []}
    }
    assert absent != explicit_empty_list


def test_corrupt_configured_feedback_store_writes_safe_failed_manifest_before_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    project_config = Path(__file__).parents[2] / "config"
    shutil.copy(project_config / "base.yaml", config_dir / "base.yaml")
    private_name = "private-feedback-2401.00001.json"
    (tmp_path / private_name).write_text("private feedback body", encoding="utf-8")
    (config_dir / "custom.yaml").write_text(
        "candidate_pipeline:\n"
        "  feedback:\n"
        f"    store_path: {private_name}\n"
        "    favorite_delta: 0.05\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        candidates.PyzoteroGateway,
        "from_credentials",
        lambda *args, **kwargs: calls.append("zotero"),
    )
    monkeypatch.setattr(
        candidates.HttpArxivMetadataGateway,
        "from_defaults",
        lambda **kwargs: calls.append("arxiv"),
    )
    monkeypatch.setattr(
        candidates,
        "SentenceTransformerEmbeddingProvider",
        lambda **kwargs: calls.append("embedding"),
    )
    run_root = tmp_path / "run"
    manifest_path = run_root / "run-manifest.json"
    environment = {
        "ZOTERO_ID": "configured",
        "ZOTERO_KEY": "configured",
        "LLM_API_KEY": "configured",
        "LLM_BASE_URL": "https://llm.example.test/v1",
        "LLM_MODEL": "model-v1",
        "PAPER_DAILY_SITE_URL": "https://papers.example.test/",
    }

    result = daily.main(
        [
            "--mode", "live", "--config-dir", str(config_dir), "--run-id", "run-feedback-rejected",
            "--run-root", str(run_root), "--viewer-output", str(run_root / "viewer"),
            "--manifest-output", str(manifest_path),
        ],
        environ=environment,
        clock=lambda: NOW,
    )

    serialized = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(serialized)
    assert result != 0
    assert calls == []
    assert '"candidate_stage_failed"' not in serialized
    assert '"feedback_projection_rejected"' in serialized
    assert '"status": "failed"' in serialized
    assert '"status": "skipped"' in serialized
    assert [stage["status"] for stage in manifest["stages"]] == [
        "failed", "skipped", "skipped", "skipped", "skipped", "skipped"
    ]
    assert manifest["stages"][0]["error_codes"] == ["feedback_projection_rejected"]
    assert private_name not in serialized
    assert "private feedback body" not in serialized
    output = capsys.readouterr()
    assert private_name not in output.out
    assert private_name not in output.err
    assert "private feedback body" not in output.out
    assert "private feedback body" not in output.err


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
