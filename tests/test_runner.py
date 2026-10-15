import json
import os
import re
import sqlite3
import sys
import textwrap
import time

import pytest

import runner
from runner import (candidates_since, extract_report, first_line, fmt_duration,
                    notify, parse_claude_event)


def test_parse_claude_event_kinds():
    tool = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "thinking"},
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "python3 pipeline/discover.py --agent claude", "description": "Run discovery"}},
        {"type": "text", "text": "Discovery done.\nDetails follow."}]}})
    assert parse_claude_event(tool) == [("tool", "Bash: Run discovery"),
                                        ("log", "$ python3 pipeline/discover.py --agent claude"),
                                        ("say", "Discovery done.\nDetails follow.")]
    denied = json.dumps({"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
                         "message": "rm -rf is not allowed"})
    assert parse_claude_event(denied) == [("denied", "Bash: rm -rf is not allowed")]
    result_of_tool = json.dumps({"type": "user", "message": {"content": [
        {"type": "tool_result", "content": "{\"fetched\": 3}"}]}})
    assert parse_claude_event(result_of_tool) == [("log", '{"fetched": 3}')]
    result = json.dumps({"type": "result", "subtype": "success", "result": "**Report**", "is_error": False})
    assert parse_claude_event(result) == [("result", "**Report**")]
    hook = json.dumps({"type": "system", "subtype": "hook_started"})
    assert parse_claude_event(hook) == []
    assert parse_claude_event("Traceback (most recent call last):\n") == [
        ("raw", "Traceback (most recent call last):")]


def test_first_line_and_duration():
    assert first_line("\n  hello   world \nsecond") == "hello world"
    assert first_line("x" * 200, width=10) == "x" * 9 + "…"
    assert [fmt_duration(s) for s in (42, 312, 3780)] == ["42s", "5m 12s", "1h 03m"]


def test_extract_report():
    lines = ["I am launching discovery.", "Waiting.", "### Run Summary", "* Fetched: 3"]
    assert extract_report(lines) == ["### Run Summary", "* Fetched: 3"]
    assert extract_report(["a"] * 40) == ["a"] * 30


def test_notify_respects_job_notify(monkeypatch):
    calls = []
    monkeypatch.setenv("JOB_NOTIFY", "0")
    notify("T", "m", run=lambda *a, **k: calls.append(a[0]))
    assert calls == []
    notify("T", "m", force=True, run=lambda *a, **k: calls.append(a[0]))
    assert calls[0][0] == "osascript" and calls[0][-2:] == ["T", "m"]


def test_candidates_since_ignores_older_files(tmp_path):
    old = tmp_path / "candidates_claude_old.json"
    old.write_text(json.dumps([{"job_link": "a", "score": 1}]))
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (tmp_path / "candidates_claude_new.json").write_text(json.dumps([{"job_link": "b", "score": 9}]))
    (tmp_path / "candidates_gemini_new.json").write_text(json.dumps([{"job_link": "c"}]))
    assert list(candidates_since(str(tmp_path), "claude", time.time() - 60)) == ["b"]


FAKE_AGENT = textwrap.dedent('''
    import os, sqlite3, sys, json, time
    from datetime import datetime
    tries = sys.argv[1]
    n = int(open(tries).read()) if os.path.exists(tries) else 0
    open(tries, "w").write(str(n + 1))
    if n < int(sys.argv[3]):
        print("API error 503", file=sys.stderr)
        sys.exit(1)
    with open(os.environ["JOB_PROGRESS_FILE"], "a") as f:
        f.write("[ 1/1] greenhouse/acme   2 fresh\\n")
        f.write("! greenhouse/broken: HTTPError: 404\\n")
    print("I am running discovery.", flush=True)
    time.sleep(0.5)
    con = sqlite3.connect(sys.argv[2])
    con.execute("INSERT INTO seen VALUES ('u1','k1','Acme','Software Engineer','NY','https://x/1',"
                "'fake','source: t',?)", (datetime.now().isoformat(timespec="seconds"),))
    con.commit()
    runs = os.path.join(os.path.dirname(sys.argv[2]), "runs")
    os.makedirs(runs, exist_ok=True)
    json.dump([{"job_link": "https://x/1", "score": 42, "yoe": "3+"}],
              open(os.path.join(runs, "candidates_fake_1.json"), "w"))
    print("### Run report")
    print("* appended 1")
''')


@pytest.fixture
def lane(tmp_path):
    db = tmp_path / "seen.db"
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE seen(url_key TEXT PRIMARY KEY, content_key TEXT UNIQUE,
        company TEXT, role TEXT, location TEXT, job_link TEXT, agent TEXT, source TEXT, first_seen TEXT)""")
    con.commit()
    agent = tmp_path / "fake_agent.py"
    agent.write_text(FAKE_AGENT)
    return tmp_path, db, agent


def _args(tmp, db, agent, fails=0, retries=1):
    return ["--agent", "fake", "--label", "Test lane", "--stream", "lines",
            "--retries", str(retries), "--retry-wait", "0",
            "--db", str(db), "--logs-dir", str(tmp / "logs"), "--runs-dir", str(tmp / "runs"),
            "--", sys.executable, str(agent), str(tmp / "tries"), str(db), str(fails)]


def test_lane_shows_progress_narration_and_summary(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent)) == 0
    out = capsys.readouterr().out
    assert "━━ Fake · Test lane · started" in out
    assert "  · [ 1/1] greenhouse/acme   2 fresh" in out
    assert "  ! greenhouse/broken: HTTPError: 404" in out
    assert "  › I am running discovery." in out
    assert "━━ Fake done ·" in out and "1 job added" in out
    assert " 42  Acme" in out and "3+" in out and "https://x/1" in out
    assert "  Agent report:\n    ### Run report\n    * appended 1" in out
    assert "  › ### Run report" not in out          # report isn't echoed twice
    log = next((tmp / "logs").glob("fake-*.log"))
    assert f"  View: less +G {log}" in out
    assert "I am running discovery." in log.read_text()
    last = json.loads((tmp / "logs" / "last-run-fake.json").read_text())
    assert (last["added"], last["exit"]) == (1, 0)


def test_lane_retries_then_succeeds(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent, fails=1, retries=3)) == 0
    out = capsys.readouterr().out
    assert "  ! attempt 1 failed (exit 1), retrying in 0s" in out
    assert "1 job added" in out
    assert "API error 503" in next((tmp / "logs").glob("fake-*.log")).read_text()


def test_lane_gives_up_after_retries(lane, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    tmp, db, agent = lane
    assert runner.main(_args(tmp, db, agent, fails=5, retries=2)) == 1
    out = capsys.readouterr().out
    assert "0 jobs added" in out and "  ! exited with code 1" in out


def test_missing_command(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    code = runner.main(["--agent", "x", "--logs-dir", str(tmp_path), "--runs-dir", str(tmp_path),
                        "--db", str(tmp_path / "none.db"), "--", "no-such-cli-xyz"])
    assert code == 127
    assert "  ! command not found: no-such-cli-xyz" in capsys.readouterr().out


def test_total_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_RUN_STARTED", str(time.time() - 5))
    sent = []
    monkeypatch.setattr(runner, "notify", lambda t, m, force=False: sent.append((t, m, force)))
    (tmp_path / "last-run-claude.json").write_text(json.dumps({"added": 1, "exit": 0}))
    (tmp_path / "last-run-gemini.json").write_text(json.dumps({"added": 2, "exit": 1}))
    runner.main(["--total", "claude,gemini,feeds", "--logs-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert re.search(r"━━ Total: 3 jobs added \(claude 1, gemini 2!, feeds –\) · \d+s ━━", out)
    assert sent == [("Job pipeline", "3 jobs added (claude 1, gemini 2!, feeds –)", True)]


def test_direct_mode_runs_discover_then_add_job(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    runs = tmp_path / "runs"
    runs.mkdir()
    cand = runs / "candidates_feeds_1.json"
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1].endswith("discover.py"):
            cand.write_text(json.dumps([{"job_link": "https://x/9", "company": "WPP",
                                         "role": "Software Engineer", "score": 61, "yoe": "2+"}]))
            out = {"candidates_file": str(cand), "fetched": 70, "already_known": 5, "kept": 1,
                   "errors": ["github/jobright-swe-newgrad: HTTPError: 503"]}
        else:
            out = {"appended": 1}
        return runner.subprocess.CompletedProcess(cmd, 0, json.dumps(out), "")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    code = runner.main(["--agent", "feeds", "--label", "GitHub feeds", "--direct", "github",
                        "--dry-run", "--db", str(tmp_path / "s.db"),
                        "--logs-dir", str(tmp_path / "logs"), "--runs-dir", str(runs)])
    assert code == 0
    assert calls[0][-6:] == ["--sources", "github", "--db", str(tmp_path / "s.db"), "--out-dir", str(runs)]
    assert calls[1][-1] == "--dry-run" and str(cand) in calls[1]
    out = capsys.readouterr().out
    assert "━━ Feeds done ·" in out and "1 job to add (dry run)" in out
    assert " 61  WPP" in out
    assert "  Run report:\n    fetched 70 · already appended 5 · kept 1 · would append 1" in out
    assert "    ! github/jobright-swe-newgrad: HTTPError: 503" in out


def test_bad_bytes_on_agent_stdout_do_not_crash(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("JOB_NOTIFY", "0")
    agent = tmp_path / "bad.py"
    agent.write_text("import sys; sys.stdout.buffer.write(b'caf\\xff ok\\n')")
    code = runner.main(["--agent", "bad", "--logs-dir", str(tmp_path), "--runs-dir", str(tmp_path),
                        "--db", str(tmp_path / "none.db"), "--", sys.executable, str(agent)])
    assert code == 0
    assert "━━ Bad done" in capsys.readouterr().out
