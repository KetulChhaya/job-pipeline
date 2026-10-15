import json
import os
import sqlite3
import subprocess
import sys

from add_job import RUNS_HEADER, dropped_summary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADD_JOB = os.path.join(ROOT, "pipeline", "add_job.py")


def test_dropped_summary_reads_sibling_file(tmp_path):
    (tmp_path / "candidates_test_1.json").write_text("[]")
    (tmp_path / "rejected_test_1.json").write_text(json.dumps(
        [{"reason": "level"}, {"reason": "level"}, {"reason": "yoe"}]))
    assert dropped_summary(str(tmp_path / "candidates_test_1.json")) == "level 2, yoe 1"


def test_dropped_summary_without_sibling(tmp_path):
    (tmp_path / "candidates_test_2.json").write_text("[]")
    assert dropped_summary(str(tmp_path / "candidates_test_2.json")) == "-"
    assert dropped_summary(str(tmp_path / "other.json")) == "-"


def test_runs_header_has_dropped():
    assert RUNS_HEADER[-1] == "Dropped" and len(RUNS_HEADER) == 9


def _run(tmp_path, rows):
    inp = tmp_path / "candidates_test_x.json"
    inp.write_text(json.dumps(rows))
    db = tmp_path / "seen.db"
    r = subprocess.run([sys.executable, ADD_JOB, "--agent", "test", "--input", str(inp),
                        "--skip-sheet", "--db", str(db)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout), sqlite3.connect(db)


def test_old_format_candidates_still_work(tmp_path):
    old = [{"date_posted": "2026-10-14", "company": "Datadog", "role": "Software Engineer",
            "location": "New York, NY", "yoe": "", "job_link": "https://x/1", "stack": "",
            "notes": "source: greenhouse/datadog"}]
    summary, _ = _run(tmp_path, old)
    assert summary["appended"] == 1


def test_appends_best_score_first(tmp_path):
    rows = [{"company": c, "role": "Software Engineer", "location": "NY",
             "job_link": f"https://x/{c}", "notes": "source: t", "score": s}
            for c, s in (("low", 10), ("high", 90), ("mid", 50))]
    _, con = _run(tmp_path, rows)
    order = [r[0] for r in con.execute("SELECT company FROM seen ORDER BY rowid")]
    assert order == ["high", "mid", "low"]


def test_add_job_writes_a_progress_line(tmp_path):
    rows = [{"company": "Acme", "role": "Software Engineer", "location": "NY",
             "job_link": "https://x/a", "notes": "source: t"}]
    inp = tmp_path / "candidates_test_y.json"
    inp.write_text(json.dumps(rows))
    log = tmp_path / "progress.log"
    env = dict(os.environ, JOB_PROGRESS_FILE=str(log))
    r = subprocess.run([sys.executable, ADD_JOB, "--agent", "test", "--input", str(inp), "--dry-run"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert log.read_text() == "would append 1 to Pipeline Jobs (dup link 0, dup content 0)\n"
