"""run_all.sh lane selection and menu, against stub lane scripts in a temp folder."""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def home(tmp_path):
    shutil.copy(os.path.join(ROOT, "run_all.sh"), tmp_path)
    for lane in ("claude", "gemini", "feeds"):
        p = tmp_path / f"run_{lane}.sh"
        p.write_text(f'#!/bin/bash\necho "ran {lane}${{*:+ $*}}"\n')
        p.chmod(0o755)
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "runner.py").write_text("import sys; print('total', *sys.argv[1:])\n")
    shutil.copy(os.path.join(ROOT, "pipeline", "settings.py"), tmp_path / "pipeline")
    (tmp_path / "pipeline" / "seen.db").write_text("db")
    (tmp_path / "logs").mkdir()
    return tmp_path


def run(home, *args, stdin=""):
    env = dict(os.environ, JOBHUNT_PAGER="cat")
    r = subprocess.run(["bash", str(home / "run_all.sh"), *args], input=stdin,
                       capture_output=True, text=True, env=env)
    return r.returncode, [l for l in r.stdout.splitlines() if l.startswith(("ran", "total"))], r


def test_no_args_runs_every_lane(home):
    code, lines, _ = run(home)
    assert code == 0
    assert lines == ["ran claude", "ran gemini", "ran feeds", "total --total claude,gemini,feeds"]


def test_named_lanes_in_order(home):
    assert run(home, "feeds")[1] == ["ran feeds", "total --total feeds"]
    assert run(home, "claude", "feeds")[1] == ["ran claude", "ran feeds", "total --total claude,feeds"]


def test_unknown_lane_runs_nothing(home):
    code, lines, r = run(home, "claud")
    assert code == 2 and lines == []
    assert "unknown lane: claud" in r.stderr


def test_menu_multi_pick(home):
    code, lines, r = run(home, "menu", stdin="1 3\n")
    assert code == 0
    assert "1) Claude" in r.stdout and "6) Open the latest log" in r.stdout
    assert lines == ["ran claude", "ran feeds", "total --total claude,feeds"]


def test_menu_all(home):
    assert run(home, "menu", stdin="4\n")[1][:3] == ["ran claude", "ran gemini", "ran feeds"]


def test_menu_feeds_dry_run_uses_a_registry_copy(home):
    code, lines, _ = run(home, "menu", stdin="5\n")
    assert code == 0 and len(lines) == 1
    assert lines[0].startswith("ran feeds --dry-run --db ")
    copy = lines[0].split("--db ")[1]
    assert copy != str(home / "pipeline" / "seen.db")


def test_menu_open_latest_log(home):
    (home / "logs" / "feeds-20261015-1120.log").write_text("old")
    newest = home / "logs" / "claude-20261015-1300.log"
    newest.write_text("newest log text")
    os.utime(home / "logs" / "feeds-20261015-1120.log", (1, 1))
    code, _, r = run(home, "menu", stdin="6\n")
    assert code == 0 and "newest log text" in r.stdout


def test_menu_enter_quits(home):
    code, lines, _ = run(home, "menu", stdin="\n")
    assert code == 0 and lines == []


def test_menu_bad_pick(home):
    code, lines, r = run(home, "menu", stdin="9\n")
    assert code == 2 and lines == [] and "pick 1-6" in r.stderr


def test_disabled_lanes_are_skipped(home, monkeypatch):
    cfg = home / "settings.json"
    cfg.write_text('{"lanes": {"gemini": {"enabled": false}}}')
    monkeypatch.setenv("JOB_SETTINGS", str(cfg))
    assert run(home)[1] == ["ran claude", "ran feeds", "total --total claude,feeds"]
    code, lines, r = run(home, "gemini", "feeds")
    assert lines == ["ran feeds", "total --total feeds"]
    assert "skip gemini (disabled in config/settings.json)" in r.stdout
