"""Music Grabber is standalone: no Python import of BPM Tagger, ever.

Coupling to BPM Tagger is HTTP and/or the shared music folder (docs/plans/extraction.md).
"""

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "music_grabber"
FORBIDDEN = ("bpm_tagger", "web_ui", "librosa", "essentia", "deeprhythm", "soundfile", "numpy")


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.lineno, node.module or ""


def test_no_module_imports_bpm_tagger_or_a_detector():
    bad = []
    for f in sorted(PKG.rglob("*.py")):
        for lineno, name in _imports(ast.parse(f.read_text(encoding="utf-8"))):
            if name.split(".")[0] in FORBIDDEN:
                bad.append(f"{f.relative_to(ROOT)}:{lineno} imports {name}")
    assert not bad, "\n".join(bad)


def test_no_bpm_tagger_string_in_code_paths():
    """A string import (importlib / __import__) would dodge the AST check above."""
    bad = []
    for f in sorted(PKG.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.split(".")[0] == "bpm_tagger":
                bad.append(f"{f.relative_to(ROOT)}:{node.lineno}")
    assert not bad, bad


_PROBE = textwrap.dedent("""
    import importlib.abc, sys

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in sys.argv[1].split(","):
                raise ImportError(f"{name} is blocked")
            return None

    sys.meta_path.insert(0, Block())
    import music_grabber, music_grabber.main, music_grabber.sync_engine, music_grabber.worker
    from music_grabber.providers import build_providers
    # Providers import their heavy libraries lazily, so building them is safe without them.
    assert [p.name for p in build_providers({"provider_order": "ytdlp"})] == ["ytdlp"]
    leaked = sorted(m for m in sys.modules if m.split(".")[0] in sys.argv[1].split(","))
    assert not leaked, leaked
    print("OK")
""")


def test_imports_without_bpm_tagger_detectors_or_download_libraries():
    """The engine imports, and the providers build, with streamrip and yt-dlp absent
    (they are imported lazily) and with BPM Tagger + the detectors unimportable."""
    blocked = ",".join(FORBIDDEN + ("streamrip", "yt_dlp", "flask", "waitress"))
    proc = subprocess.run([sys.executable, "-c", _PROBE, blocked], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=60,
                          env={"PYTHONPATH": str(ROOT), "PATH": "", **{k: os.environ[k] for k in ("SYSTEMROOT",) if k in os.environ}})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
