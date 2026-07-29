from pathlib import Path

from omegaconf import OmegaConf

from zotero_arxiv_daily.pipeline.analysis import main, run_offline_fixture


FIXTURE = Path(__file__).parents[1] / "fixtures" / "stage3_offline.json"


def test_offline_dry_run_does_not_read_credentials_call_network_or_write_cache(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(
        "zotero_arxiv_daily.pipeline.analysis.build_production_analysis_pipeline",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("production dependency construction is forbidden in dry-run")
        ),
    )
    result = run_offline_fixture(
        FIXTURE, dry_run=True, cache_root=tmp_path / "cache"
    )
    assert [item.status for item in result.results] == ["success"]
    assert result.expensive_call_count == 0
    assert result.cache_hit_count == 0
    assert not (tmp_path / "cache").exists()


def test_offline_cli_outputs_only_a_safe_summary(capsys):
    assert main(["--dry-run", "--offline-fixture", str(FIXTURE)]) == 0
    output = capsys.readouterr().out
    assert '"success": 1' in output
    assert "evidence_text" not in output
    assert "text_zh" not in output


def test_stage_three_config_has_exact_non_secret_defaults():
    base = OmegaConf.load(Path(__file__).parents[2] / "config" / "base.yaml")
    assert OmegaConf.to_container(base.analysis_pipeline, resolve=True) == {
        "cache_root": "cache/analysis",
        "prompt_version": "stage3-v2",
        "schema_version": "1.0",
        "config_version": "2",
        "max_papers": 5,
        "max_visuals_per_paper": 3,
        "max_evidence_candidates": 48,
        "max_evidence_chars": 24000,
        "max_block_chars": 2000,
        "max_output_tokens": 8192,
        "response_max_bytes": 1048576,
        "request_timeout": {"connect": 10, "read": 60, "write": 10, "pool": 10},
        "retry": {
            "max_attempts": 3,
            "backoff_seconds": 1,
            "max_retry_after_seconds": 60,
        },
    }
