from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[2]
    / ".github"
    / "workflows"
    / "personal-paper-daily.yml"
)
CI_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


def test_daily_workflow_uses_node24_cache_action_pin():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "actions/cache@5a3ec84eff668545956fd18022155c47e93e2684" not in text
    assert (
        text.count(
            "actions/cache@27d5ce7f107fe9357f9df03efb73ab90386fccae"
        )
        == 2
    )


def test_workflows_pin_node24_github_actions():
    daily = WORKFLOW.read_text(encoding="utf-8")
    ci = CI_WORKFLOW.read_text(encoding="utf-8")

    checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
    assert checkout in daily
    assert checkout in ci
    assert (
        "actions/upload-artifact@b7c566a772e6b6bfb58ed0dc250532a479d7789f"
        in daily
    )
    assert (
        "actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9"
        in daily
    )
    assert (
        "actions/deploy-pages@cd2ce8fcbc39b97be8ca5fce6e763baed58fa128"
        in daily
    )
