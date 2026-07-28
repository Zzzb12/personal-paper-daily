from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "personal-paper-daily.yml"
)


def test_daily_workflow_uses_node24_cache_action_pin():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/cache@5a3ec84eff668545956fd18022155c47e93e2684" not in text
    assert (
        text.count(
            "actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae"
        )
        == 2
    )
