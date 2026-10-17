"""setup.sh in a temp copy, with a fake HOME and canned answers (real rc files untouched)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = "https://docs.google.com/spreadsheets/d/1FriendSheet_Id/edit"


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "job-pipeline"
    (r / "config").mkdir(parents=True)
    shutil.copy(os.path.join(ROOT, "setup.sh"), r)
    for f in ("settings.example.json", "profile.example.json", "boards.example.json"):
        shutil.copy(os.path.join(ROOT, "config", f), r / "config")
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    os.symlink(sys.executable, bin_ / "python3")
    (bin_ / "claude").write_text("#!/bin/sh\n")
    (bin_ / "claude").chmod(0o755)
    return r, home, bin_


def setup(repo, answers):
    r, home, bin_ = repo
    env = {"HOME": str(home), "SHELL": "/bin/zsh", "PATH": f"{bin_}:/usr/bin:/bin",
           "SETUP_SKIP_VENV": "1", "SETUP_SKIP_CHECK": "1"}
    return subprocess.run(["bash", str(r / "setup.sh")], input=answers, capture_output=True,
                          text=True, env=env)


def test_wizard_writes_settings_copies_examples_adds_alias(repo):
    r, home, _ = repo
    res = setup(repo, f"{URL}\n/keys/friend.json\ny\ny\n")
    assert res.returncode == 0, res.stderr
    s = json.loads((r / "config" / "settings.json").read_text())
    assert s["sheet"]["id"] == URL and s["service_account"] == "/keys/friend.json"
    assert {k: v["enabled"] for k, v in s["lanes"].items()} == {"claude": True, "gemini": False, "feeds": True}
    assert "_comment" not in s
    assert "agy (Gemini lane) not found" in res.stdout
    assert (r / "config" / "profile.json").read_text() == (r / "config" / "profile.example.json").read_text()
    assert (r / "config" / "boards.json").exists()
    assert (home / ".zshrc").read_text().count(f'alias jobhunt="{r}/run_all.sh"') == 1
    assert "edit config/profile.json" in res.stdout


def test_rerun_keeps_profile_and_alias_once(repo):
    r, home, _ = repo
    setup(repo, f"{URL}\n\ny\ny\n")
    (r / "config" / "profile.json").write_text('{"mine": true}')
    res = setup(repo, "\n\nn\ny\n")       # Enter keeps the current sheet / key
    assert res.returncode == 0, res.stderr
    s = json.loads((r / "config" / "settings.json").read_text())
    assert s["sheet"]["id"] == URL and s["service_account"] == "service-account.json"
    assert s["lanes"]["claude"]["enabled"] is False
    assert (r / "config" / "profile.json").read_text() == '{"mine": true}'
    assert (home / ".zshrc").read_text().count("alias jobhunt=") == 1


def test_sheet_url_is_required(repo):
    res = setup(repo, "\n")
    assert res.returncode != 0 and "a Google Sheet URL is required" in res.stderr
