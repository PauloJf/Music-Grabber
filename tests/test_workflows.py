"""Guard rails on the CI/publish workflows."""

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _on(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get(True, data.get("on"))   # YAML parses a bare `on:` key as boolean True


def test_publish_workflow_is_manual_dispatch_only():
    trig = _on(WF / "docker-publish.yml")
    assert set(trig) == {"workflow_dispatch"}, trig
    assert trig["workflow_dispatch"]["inputs"]["confirm"]["required"] is True


def test_publish_job_requires_the_typed_confirmation():
    data = yaml.safe_load((WF / "docker-publish.yml").read_text(encoding="utf-8"))
    assert data["jobs"]["push"]["if"] == "inputs.confirm == 'publish'"


def test_only_the_publish_workflow_touches_registry_secrets():
    for f in WF.glob("*.yml"):
        text = f.read_text(encoding="utf-8")
        if f.name != "docker-publish.yml":
            assert "secrets." not in text and "docker/login-action" not in text, f.name


def test_ci_runs_on_push_to_main_and_prs_only():
    trig = _on(WF / "ci.yml")
    assert set(trig) == {"push", "pull_request"}
    assert trig["push"]["branches"] == ["main"]
