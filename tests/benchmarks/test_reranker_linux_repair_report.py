import hashlib
import json
import re
from pathlib import Path


REPORT = (
    Path(__file__).parents[2]
    / "docs"
    / "benchmarks"
    / "2026-07-28-reranker-linux-repair.json"
)


def test_reranker_linux_repair_has_reviewable_non_regression_evidence():
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["schema_version"] == "1"
    fixture_payload = json.dumps(
        report["fixture_data"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(fixture_payload).hexdigest() == report["fixture_sha256"]
    assert report["fixture"] == {
        "candidate_count": 10,
        "interest_count": 3,
        "relevant_candidate_count": 5,
        "synthetic": True,
    }
    assert len(report["fixture_data"]["interests"]) == report["fixture"]["interest_count"]
    assert len(report["fixture_data"]["candidates"]) == report["fixture"]["candidate_count"]
    assert (
        sum(label for _, label in report["fixture_data"]["candidates"])
        == report["fixture"]["relevant_candidate_count"]
    )
    reference = report["models"]["reference"]
    replacement = report["models"]["replacement"]
    assert re.fullmatch(r"[0-9a-f]{40}", reference["revision"])
    assert re.fullmatch(r"[0-9a-f]{40}", replacement["revision"])
    assert replacement["trust_remote_code"] is False
    assert replacement["implementation"] == "transformers-mean-pooling-v1"
    assert replacement["local_snapshot_bytes"] < reference["local_snapshot_bytes"]
    for metric in ("precision_at_5_ppm", "recall_at_5_ppm", "ndcg_at_5_ppm"):
        assert replacement[metric] >= reference[metric]
        assert replacement[metric] == 1_000_000
    assert report["verdict"] == "non_regression_pass"
    assert report["safety"] == {
        "network_calls": 0,
        "paid_calls": 0,
        "private_records": 0,
        "real_deliveries": 0,
    }


def test_reranker_linux_repair_report_has_no_private_or_machine_paths():
    text = REPORT.read_text(encoding="utf-8")

    assert "ZOTERO" not in text
    assert "FEISHU" not in text
    assert "API_KEY" not in text
    assert "C:\\" not in text
    assert "F:\\" not in text
