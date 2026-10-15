import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from filters import load_profile  # noqa: E402


@pytest.fixture(scope="session")
def prof():
    return load_profile(os.path.join(ROOT, "config", "profile.json"))


@pytest.fixture(autouse=True)
def _progress_to_tmp(tmp_path, monkeypatch):
    """Keep progress lines (and subprocesses' ones) out of the real logs/ folder."""
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(tmp_path / "progress.log"))
