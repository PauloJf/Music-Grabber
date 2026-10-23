"""The docs and the examples must agree with the code: env var names, compose files, links."""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "music_grabber"
COMPOSE_DIR = ROOT / "docs" / "compose"
VAR = r"[A-Z][A-Z0-9_]{2,}"


def code_env_vars() -> set:
    """Every environment variable the code reads."""
    found = set()
    for f in PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        found |= set(re.findall(rf'(?:environ\.get|_bool|getenv)\(\s*"({VAR})"', text))
        found |= set(re.findall(rf'os\.environ\[\s*"({VAR})"', text))
    return found


KNOWN = code_env_vars()
SECRETISH = re.compile(r"(SECRET|TOKEN|PASS|ARL|API_KEY|TOPIC)")


def test_the_scan_finds_the_variables_we_expect():
    assert {"MUSIC_DIR", "SPOTIFY_CLIENT_ID", "DEEZER_ARL", "API_TOKEN", "ANALYSIS", "MG_REMOTE_URL",
            "POST_IMPORT_HOOKS", "LIBRARY_INDEX", "DATA_DIR"} <= KNOWN


def test_readme_documents_every_variable_the_code_reads():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = sorted(v for v in KNOWN if not re.search(rf"\b{v}\b", readme))
    assert not missing, f"undocumented in README: {missing}"


def _services(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]


def _grabber_env(path):
    return _services(path)["music-grabber"]["environment"]


COMPOSE_FILES = sorted(COMPOSE_DIR.glob("*.yml")) + [ROOT / "docker-compose.yml"]


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_files_use_only_variables_the_code_reads(path):
    unknown = sorted(set(_grabber_env(path)) - KNOWN)
    assert not unknown, f"{path.name}: not read by the code: {unknown}"


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_files_carry_no_secrets(path):
    for k, v in _grabber_env(path).items():
        if SECRETISH.search(k):
            assert str(v) == "" or str(v).startswith("${"), f"{path.name}: {k} has a literal value"
    for name, svc in _services(path).items():
        for k, v in (svc.get("environment") or {}).items():
            if SECRETISH.search(k):
                assert str(v) == "" or str(v).startswith("${"), f"{path.name}: {name}.{k} has a literal value"


@pytest.mark.parametrize("path", sorted(COMPOSE_DIR.glob("*.yml")), ids=lambda p: p.name)
def test_published_ports_are_loopback_only(path):
    for name, svc in _services(path).items():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{path.name}: {name} publishes {port} beyond loopback"


def _host_of(volume_list, container_path):
    """The host side of the volume mounted at ``container_path`` (default values such as
    ``${MUSIC_PATH:-/srv/music}`` contain a colon, so protect those before splitting)."""
    for v in volume_list:
        safe = re.sub(r"\$\{[^}]*\}", lambda m: m.group(0).replace(":", "\x00"), str(v))
        parts = safe.split(":")
        if len(parts) >= 2 and parts[1] == container_path:
            return parts[0].replace("\x00", ":")
    return None


@pytest.mark.parametrize("name", ["with-bpm-tagger.yml", "full-stack.yml"])
def test_shared_folder_examples_really_share_one_folder(name):
    s = _services(COMPOSE_DIR / name)
    grabber, tagger = s["music-grabber"], s["bpm-tagger"]
    g_host, t_host = _host_of(grabber["volumes"], "/music"), _host_of(tagger["volumes"], "/music")
    assert g_host and g_host == t_host, "both containers must mount the same host folder at /music"
    env = grabber["environment"]
    assert env["ANALYSIS"] == "intake"
    assert env["INTAKE_DIR"].startswith(env["MUSIC_DIR"].rstrip("/") + "/"), "intake must be inside the library"
    assert tagger["environment"]["MODE"] == "watch"
    assert str(tagger["environment"]["GRABBER_ENABLED"]).lower() == "false"
    assert "navidrome" not in str(env.get("POST_IMPORT_HOOKS", "")), "BPM Tagger triggers the rescan after tagging"


@pytest.mark.parametrize("name", ["with-bpm-tagger.yml", "full-stack.yml"])
def test_examples_use_the_bpm_tagger_beta_channel_until_its_stable_release(name):
    """:latest is still the old BPM Tagger with the grabber built in. When BPM Tagger's stable
    release ships, change both examples to :latest and update this test."""
    assert _services(COMPOSE_DIR / name)["bpm-tagger"]["image"] == "gatoserio/bpm-tagger:beta"


def test_full_stack_index_points_at_bpm_taggers_subsonic_api():
    s = _services(COMPOSE_DIR / "full-stack.yml")
    env = s["music-grabber"]["environment"]
    assert env["LIBRARY_INDEX"] == "opensubsonic" and env["LIBRARY_URL"] == "http://bpm-tagger:5000"
    assert str(s["bpm-tagger"]["environment"]["SUBSONIC_ENABLED"]).lower() == "true"
    shared = _host_of(s["music-grabber"]["volumes"], "/music")
    assert _host_of(s["navidrome"]["volumes"], "/music") == shared, "Navidrome reads the same folder"


def test_env_example_only_names_real_variables():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    names = set(re.findall(rf"^#?\s*({VAR})=", text, flags=re.M))
    assert names - KNOWN == set(), f".env.example names variables the code never reads: {sorted(names - KNOWN)}"


def test_relative_links_in_the_docs_resolve():
    for md in [ROOT / "README.md", ROOT / "docs" / "migration-from-bpm-tagger.md", ROOT / "CLAUDE.md"]:
        for target in re.findall(r"\]\(((?!https?:|#|mailto:)[^)\s]+)\)", md.read_text(encoding="utf-8")):
            path = (md.parent / target.split("#")[0]).resolve()
            assert path.exists(), f"{md.name} links to missing {target}"


def test_changelog_has_a_section_for_the_current_version():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert re.search(rf"^## \[?{re.escape(version)}\]?", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
