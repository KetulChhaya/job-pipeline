import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
# Tests see the committed example settings, never the runner's own settings.json.
os.environ["JOB_SETTINGS"] = os.path.join(ROOT, "config", "settings.example.json")
os.environ.pop("JOB_SHEET_ID", None)
os.environ.pop("GOOGLE_SERVICE_ACCOUNT", None)

from filters import load_profile  # noqa: E402


@pytest.fixture(scope="session")
def prof():
    """The committed example profile, with sponsorship on: the rules the tests
    pin down. Nobody's personal config/profile.json is needed to run them."""
    p = load_profile(os.path.join(ROOT, "config", "profile.example.json"))
    p["needs_sponsorship"] = True
    return p


@pytest.fixture(autouse=True)
def _progress_to_tmp(tmp_path, monkeypatch):
    """Keep progress lines (and subprocesses' ones) out of the real logs/ folder."""
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(tmp_path / "progress.log"))
