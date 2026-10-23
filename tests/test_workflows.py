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


# streamrip 2.1.0 pins Pillow<11 (63 advisories); a current Pillow is installed on top of it, after
# requirements.txt, because pip cannot resolve the two together. Every place that builds an environment
# must do the second step, or tests and the image silently run the vulnerable one.
ROOT = WF.parent.parent
PILLOW_STEP = "pip install -r requirements-pillow.txt"


def test_requirements_pillow_pins_a_current_pillow():
    import re
    lines = [ln.split("#")[0].strip() for ln in (ROOT / "requirements-pillow.txt").read_text(encoding="utf-8").splitlines()]
    pins = [ln for ln in lines if ln]
    assert len(pins) == 1 and pins[0].lower().startswith("pillow>=")
    assert int(re.match(r"pillow>=(\d+)", pins[0].lower()).group(1)) >= 12, pins
    assert "pillow" not in "".join(ln for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                                   if "<" in ln and not ln.lstrip().startswith("#")).lower()   # no cap that fights the override


def test_every_environment_installs_the_pillow_override_after_the_main_requirements():
    for rel, first in ((".github/workflows/ci.yml", "pip install -r requirements-dev.txt"),
                       (".github/workflows/docker-publish.yml", "pip install -r requirements-dev.txt"),
                       ("Dockerfile", "pip install --no-cache-dir -r requirements.txt")):
        text = (ROOT / rel).read_text(encoding="utf-8").replace("--no-cache-dir ", "")
        first = first.replace("--no-cache-dir ", "")
        assert PILLOW_STEP in text, rel
        assert text.index(first) < text.index(PILLOW_STEP), rel
    assert "requirements-pillow.txt" in (ROOT / "Dockerfile").read_text(encoding="utf-8").split("COPY requirements")[1].splitlines()[0]
    assert "PIL.__version__" in (ROOT / "Dockerfile").read_text(encoding="utf-8")        # the build fails if it did not take


def test_the_development_instructions_install_the_pillow_override():
    for rel in ("CLAUDE.md", "README.md"):
        assert PILLOW_STEP in (ROOT / rel).read_text(encoding="utf-8"), rel
