"""Keep the public-release branch ruleset aligned with CI."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RULESET = ROOT / ".github" / "rulesets" / "protect-main.json"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _ruleset():
    return json.loads(RULESET.read_text(encoding="utf-8"))


def test_main_ruleset_has_no_unprotected_write_path():
    ruleset = _ruleset()

    assert ruleset["enforcement"] == "active"
    assert ruleset["target"] == "branch"
    assert ruleset["bypass_actors"] == []
    assert ruleset["conditions"]["ref_name"] == {
        "include": ["~DEFAULT_BRANCH"],
        "exclude": [],
    }

    rules = {rule["type"]: rule for rule in ruleset["rules"]}
    assert {
        "deletion",
        "non_fast_forward",
        "required_linear_history",
        "pull_request",
        "required_status_checks",
    } <= rules.keys()

    pull_request = rules["pull_request"]["parameters"]
    assert pull_request["required_approving_review_count"] == 0
    assert pull_request["required_review_thread_resolution"] is True
    assert pull_request["allowed_merge_methods"] == ["squash"]


def test_required_checks_match_the_ci_matrix():
    rules = {rule["type"]: rule for rule in _ruleset()["rules"]}
    status = rules["required_status_checks"]["parameters"]
    checks = status["required_status_checks"]

    assert status["strict_required_status_checks_policy"] is True
    assert {check["context"] for check in checks} == {
        "release build (Linux)",
        "release build (Windows)",
        "test (3.10)",
        "test (3.12)",
    }
    assert {check["integration_id"] for check in checks} == {15368}

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert 'python-version: ["3.10", "3.12"]' in workflow
    assert "pull_request:" in workflow
    assert "branches: [main]" in workflow
    assert "name: release build" in workflow
    assert "mpy-cross==1.27.0.post2" in workflow
    assert "build_manifest.py --check manifest.json" in workflow
