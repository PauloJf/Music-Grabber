"""Guard rails on the CI/publish workflows."""

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _on(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get(True, data.get("on"))   # YAML parses a bare `on:` key as boolean True


def test_publish_runs_only_on_a_published_release_or_a_confirmed_manual_run():
    trig = _on(WF / "docker-publish.yml")
    assert set(trig) == {"release", "workflow_dispatch"}, trig          # never push/tag/schedule/PR
    assert trig["release"]["types"] == ["published"]
    assert trig["workflow_dispatch"]["inputs"]["confirm"]["required"] is True


def test_publish_is_gated_on_verification_and_the_confirmation():
    data = yaml.safe_load((WF / "docker-publish.yml").read_text(encoding="utf-8"))
    verify, push = data["jobs"]["verify"], data["jobs"]["push"]
    assert verify["if"] == "github.event_name == 'release' || inputs.confirm == 'publish'"
    assert push["needs"] == "verify"                                    # a skipped verify skips the push
    text = (WF / "docker-publish.yml").read_text(encoding="utf-8")
    assert "pytest -q" in text and "does not match VERSION" in text     # tests + tag/VERSION check
    assert data["permissions"] == {"contents": "read"}                  # the token cannot write the repo


def test_publish_uses_only_the_two_registry_secrets():
    import re
    text = (WF / "docker-publish.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"}


def test_only_the_publish_workflow_touches_registry_secrets():
    for f in WF.glob("*.yml"):
        text = f.read_text(encoding="utf-8")
        if f.name != "docker-publish.yml":
            assert "secrets." not in text and "docker/login-action" not in text, f.name


def test_ci_runs_on_push_to_main_and_prs_only():
    trig = _on(WF / "ci.yml")
    assert set(trig) == {"push", "pull_request"}
    assert trig["push"]["branches"] == ["main"]


def test_every_job_has_a_timeout_and_apt_is_never_called_bare():
    """A hung step (the apt mirror once stalled CI for 25+ minutes) must fail fast, not wait for
    GitHub's six-hour default, and nothing may call apt without the retrying, time-boxed helper."""
    for f in WF.glob("*.yml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for name, job in data["jobs"].items():
            assert 0 < job.get("timeout-minutes", 0) <= 60, f"{f.name}:{name} needs a timeout-minutes"
        assert "apt-get" not in f.read_text(encoding="utf-8"), f"{f.name}: use scripts/ci-install-ffmpeg.sh"
    script = (WF.parent.parent / "scripts" / "ci-install-ffmpeg.sh").read_text(encoding="utf-8")
    assert "Acquire::Retries" in script and "timeout " in script and "exit 1" in script
    assert "ci-install-ffmpeg.sh" in (WF / "ci.yml").read_text(encoding="utf-8")
    assert "ci-install-ffmpeg.sh" in (WF / "docker-publish.yml").read_text(encoding="utf-8")
