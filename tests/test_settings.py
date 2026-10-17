import json
import os
import subprocess
import sys

import pytest

import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "pipeline", "settings.py")
URL = "https://docs.google.com/spreadsheets/d/1AbC_d-9xYz/edit#gid=0"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("JOB_SETTINGS", "JOB_SHEET_ID", "GOOGLE_SERVICE_ACCOUNT"):
        monkeypatch.delenv(k, raising=False)


def write(tmp_path, data):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_defaults_when_file_missing(tmp_path):
    s = settings.load(str(tmp_path / "none.json"))
    assert s["sheet"] == {"id": "", "jobs_tab": "Pipeline Jobs", "runs_tab": "Pipeline Runs",
                          "dedup_tabs": ["LinkedIn Easy Apply"]}
    assert s["lanes"]["claude"]["model"] == "claude-sonnet-5-5"
    assert s["fetch"]["hours"] == 30 and s["fetch"]["recent_title_days"] == 3
    assert s["service_account"] == os.path.join(ROOT, "service-account.json")


def test_file_overrides_defaults_and_url_becomes_id(tmp_path):
    p = write(tmp_path, {"sheet": {"id": URL, "dedup_tabs": []},
                         "lanes": {"gemini": {"enabled": False}}, "fetch": {"hours": 48}})
    s = settings.load(p)
    assert s["sheet"]["id"] == "1AbC_d-9xYz"
    assert s["sheet"]["jobs_tab"] == "Pipeline Jobs" and s["sheet"]["dedup_tabs"] == []
    assert s["lanes"]["gemini"] == {"enabled": False, "model": "gemini-3.8-flash-medium",
                                    "retries": 3, "retry_wait": 120}
    assert s["fetch"]["hours"] == 48 and s["fetch"]["timeout"] == 25


def test_env_overrides(tmp_path, monkeypatch):
    p = write(tmp_path, {"sheet": {"id": "file-id"}, "service_account": "/keys/a.json"})
    monkeypatch.setenv("JOB_SHEET_ID", URL)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT", "/keys/b.json")
    s = settings.load(p)
    assert (s["sheet"]["id"], s["service_account"]) == ("1AbC_d-9xYz", "/keys/b.json")
    monkeypatch.setenv("JOB_SETTINGS", p)
    monkeypatch.delenv("JOB_SHEET_ID")
    assert settings.load()["sheet"]["id"] == "file-id"


def test_bad_json_is_reported(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{nope")
    with pytest.raises(ValueError, match="cannot read settings"):
        settings.load(str(p))


def test_cli_get_and_lanes(tmp_path):
    p = write(tmp_path, {"lanes": {"gemini": {"enabled": False}}})
    env = dict(os.environ, JOB_SETTINGS=p)
    env.pop("JOB_SHEET_ID", None)
    def cli(*args):
        return subprocess.run([sys.executable, CLI, *args], capture_output=True, text=True, env=env)
    assert cli("get", "lanes.claude.model").stdout == "claude-sonnet-5-5\n"
    assert cli("get", "lanes.feeds.enabled").stdout == "true\n"
    assert cli("get", "fetch.hours").stdout == "30\n"
    assert cli("lanes").stdout == "claude feeds\n"
    bad = cli("get", "lanes.nope")
    assert bad.returncode == 1 and "unknown setting: lanes.nope" in bad.stderr


def test_example_file_matches_defaults():
    with open(os.path.join(ROOT, "config", "settings.example.json")) as f:
        example = json.load(f)
    assert example["sheet"]["id"].startswith("https://docs.google.com/spreadsheets/d/")
    merged = settings._merge(settings.DEFAULTS, {k: v for k, v in example.items() if k not in ("sheet", "_comment")})
    assert merged == {**settings.DEFAULTS, "sheet": settings.DEFAULTS["sheet"]}


def test_modules_read_settings(tmp_path):
    p = write(tmp_path, {"sheet": {"id": "sid", "jobs_tab": "Jobs", "runs_tab": "Runs",
                                   "dedup_tabs": ["A", "B"]},
                         "fetch": {"timeout": 9, "board_sleep": 0, "detail_sleep": 0.5,
                                   "recent_title_days": 5, "hours": 48, "contact": "me@example.com"}})
    code = ("import sys; sys.path.insert(0, 'pipeline'); import fetchers, enrich, discover, add_job; "
            "print(fetchers.UA['User-Agent'], fetchers.TIMEOUT, fetchers.SLEEP_BETWEEN_BOARDS, "
            "enrich.DETAIL_SLEEP, discover.RECENT_TITLE_DAYS, discover.DEFAULT_HOURS, "
            "add_job.SPREADSHEET_ID, add_job.JOBS_TAB, add_job.RUNS_TAB, add_job.DEDUP_TABS)")
    env = dict(os.environ, JOB_SETTINGS=p)
    env.pop("JOB_SHEET_ID", None)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env=env, cwd=ROOT).stdout.strip()
    assert out == ("job-pipeline/1.0 (personal job search; contact: me@example.com) 9 0 0.5 5 48 "
                   "sid Jobs Runs ['Jobs', 'A', 'B']")


def test_add_job_without_sheet_id_says_how_to_fix(tmp_path):
    p = write(tmp_path, {"sheet": {"id": ""}})
    inp = tmp_path / "candidates_x.json"
    inp.write_text("[]")
    env = dict(os.environ, JOB_SETTINGS=p)
    env.pop("JOB_SHEET_ID", None)
    for extra in ([], ["--check"]):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "pipeline", "add_job.py"), "--agent", "t",
                            "--input", str(inp), *extra], capture_output=True, text=True, env=env)
        assert r.returncode != 0
        assert "set sheet.id in config/settings.json (or run ./setup.sh)" in r.stderr
