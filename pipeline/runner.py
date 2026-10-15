"""Run one pipeline lane with live progress and an end-of-lane summary.

    runner.py --agent claude --label "..." --stdin prompts/claude.md --stream claude -- claude -p ...
    runner.py --agent gemini --label "..." --stream lines --retries 3 --retry-wait 120 -- agy -p ...
    runner.py --agent feeds --label "GitHub feeds" --direct github [--db PATH] [--dry-run]
    runner.py --total claude,gemini,feeds

Two live streams reach the terminal: progress lines that pipeline code
writes through progress.py ("  · ..."), and the agent's own narration
("  › ..."). The summary lists what this lane appended, read from the seen
registry rather than from the agent's words.
"""
import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime

PIPE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PIPE)
sys.path.insert(0, PIPE)
from progress import default_path  # noqa: E402

LOGS = os.path.join(ROOT, "logs")
RUNS = os.path.join(ROOT, "runs")
DB = os.path.join(PIPE, "seen.db")
BAR = "━━"
WIDTH = 110
REPORT_LINES = 40
TABLE_ROWS = 25


# --------------------------------------------------------------- formatting
def first_line(text, width=WIDTH):
    for line in (text or "").splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= width else line[:width - 1] + "…"
    return ""


def fmt_duration(seconds):
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {(s % 3600) // 60:02d}m"


def fmt_job(j):
    score = j.get("score")
    return (f"  {score if score is not None else '':>3}  {j['company'][:14]:<14} "
            f"{j['role'][:44]:<44} {(j.get('yoe') or '?'):<5} {j['job_link']}")


def _say(text):
    if text:
        print(f"  › {text}", flush=True)


# ------------------------------------------------------------ agent output
def parse_claude_event(line):
    """One Claude CLI stream-json line -> [(kind, text)].
    kinds: say, tool, denied, result, error, raw (non-JSON line)."""
    try:
        e = json.loads(line)
    except ValueError:
        return [("raw", line.rstrip("\n"))] if line.strip() else []
    out, kind = [], e.get("type")
    if kind == "assistant":
        for c in (e.get("message") or {}).get("content") or []:
            if c.get("type") == "text" and (c.get("text") or "").strip():
                out.append(("say", c["text"]))
            elif c.get("type") == "tool_use":
                inp = c.get("input") or {}
                what = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
                out.append(("tool", f"{c.get('name')}: {what}"))
    elif kind == "system" and e.get("subtype") == "permission_denied":
        out.append(("denied", e.get("tool_name") or "tool"))
    elif kind == "result":
        out.append(("result", e.get("result") or ""))
        if e.get("is_error"):
            out.append(("error", e.get("subtype") or "error"))
    return out


def _report_start(line):
    s = line.lstrip()
    return s.startswith("#") or s.startswith("|")


def extract_report(lines):
    """Plain-text agents: the report runs from the first Markdown heading or
    table to the end; without one, the last 30 lines."""
    for i, line in enumerate(lines):
        if _report_start(line):
            return lines[i:]
    return lines[-30:]


def _stream(proc, stream, log, bangs):
    """Echo the agent's stdout live; return its report lines."""
    report, plain, in_report = [], [], False
    for line in proc.stdout:
        if stream == "claude":
            for kind, text in parse_claude_event(line):
                if kind == "say":
                    _say(first_line(text))
                    log.write(text + "\n")
                elif kind == "tool":
                    _say(first_line(text))
                    log.write(f"> {text}\n")
                elif kind == "denied":
                    bangs.append(f"permission denied: {text}")
                    print(f"  ! permission denied: {text}", flush=True)
                    log.write(f"! permission denied: {text}\n")
                elif kind == "result":
                    report = text.splitlines()
                    log.write(text + "\n")
                elif kind == "error":
                    bangs.append(f"agent reported an error: {text}")
                else:
                    log.write(text + "\n")
        else:
            log.write(line)
            text = line.rstrip("\n")
            plain.append(text)
            in_report = in_report or _report_start(text)
            if not in_report:
                _say(first_line(text))
        log.flush()
    return report if stream == "claude" else extract_report(plain)


def run_agent(a, env, log, bangs):
    """Start the agent CLI (with retries). Returns (exit code, report lines)."""
    report = []
    for attempt in range(1, a.retries + 1):
        stdin = open(a.stdin) if a.stdin else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(a.command, stdin=stdin, stdout=subprocess.PIPE,
                                    stderr=log, text=True, env=env, cwd=ROOT)
        except FileNotFoundError:
            bangs.append(f"command not found: {a.command[0]}")
            return 127, report
        finally:
            if a.stdin:
                stdin.close()
        report = _stream(proc, a.stream, log, bangs)
        code = proc.wait()
        if code == 0 or attempt == a.retries:
            return code, report
        print(f"  ! attempt {attempt} failed (exit {code}), retrying in {a.retry_wait}s", flush=True)
        log.write(f"--- attempt {attempt} failed (exit {code}) ---\n")
        time.sleep(a.retry_wait)
    return 1, report


def run_direct(a, env, log, bangs):
    """No LLM: discover.py, then add_job.py on the candidates it wrote."""
    py = sys.executable
    cmd = [py, os.path.join(PIPE, "discover.py"), "--agent", a.agent,
           "--sources", a.direct, "--db", a.db, "--out-dir", a.runs_dir]
    d = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    log.write(d.stdout + d.stderr)
    if d.returncode != 0:
        bangs.append(first_line(d.stderr.strip().splitlines()[-1] if d.stderr.strip() else "discover failed"))
        return d.returncode, [], None
    found = json.loads(d.stdout)
    cmd = [py, os.path.join(PIPE, "add_job.py"), "--agent", a.agent,
           "--input", found["candidates_file"], "--db", a.db] + (["--dry-run"] if a.dry_run else [])
    g = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    log.write(g.stdout + g.stderr)
    if g.returncode != 0:
        bangs.append(first_line(g.stderr.strip().splitlines()[-1] if g.stderr.strip() else "add_job failed"))
        return g.returncode, [], found
    added = json.loads(g.stdout)
    report = [f"fetched {found['fetched']} · already appended {found['already_known']} · "
              f"kept {found['kept']} · {'would append' if a.dry_run else 'appended'} {added['appended']}"]
    report += [f"! {e}" for e in found.get("errors", [])]
    return 0, report, found


# ---------------------------------------------------------------- progress
def tail_progress(path, stop, bangs):
    """Print lines appended to the progress file until stop is set."""
    pos = 0
    while True:
        done = stop.is_set()
        try:
            with open(path) as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except OSError:
            chunk = ""
        for line in chunk.splitlines():
            if line.startswith("!"):
                bangs.append(line[1:].strip())
                print(f"  {line}", flush=True)
            else:
                print(f"  · {line}", flush=True)
        if done:
            return
        stop.wait(0.3)


# ----------------------------------------------------------------- summary
def appended_since(db, agent, since_iso):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute("SELECT company, role, location, job_link FROM seen "
                           "WHERE agent = ? AND first_seen >= ? ORDER BY rowid",
                           (agent, since_iso)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    return [dict(zip(("company", "role", "location", "job_link"), r)) for r in rows]


def candidates_since(runs_dir, agent, since_ts):
    """job_link -> candidate dict, from this agent's candidates files of this lane."""
    out = {}
    for path in sorted(glob.glob(os.path.join(runs_dir, f"candidates_{agent}_*.json"))):
        if os.path.getmtime(path) < since_ts:
            continue
        try:
            with open(path) as f:
                for c in json.load(f):
                    out[c.get("job_link")] = c
        except (OSError, ValueError):
            continue
    return out


def notify(title, message, force=False, run=subprocess.run):
    """macOS banner; silent when JOB_NOTIFY=0 (unless force) or osascript is missing."""
    if os.environ.get("JOB_NOTIFY") == "0" and not force:
        return
    try:
        run(["osascript", "-e", "on run argv", "-e",
             "display notification (item 2 of argv) with title (item 1 of argv)",
             "-e", "end run", title, message], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _plural(n):
    return f"{n} job{'s' if n != 1 else ''}"


def print_summary(a, code, report, bangs, jobs, seconds, log_path):
    name = a.agent.capitalize()
    verb = "to add (dry run)" if a.dry_run else "added"
    print(f"{BAR} {name} done · {fmt_duration(seconds)} · {_plural(len(jobs))} {verb} {BAR}")
    ranked = sorted(jobs, key=lambda j: -(j.get("score") or 0))
    for j in ranked[:TABLE_ROWS]:
        print(fmt_job(j))
    if len(ranked) > TABLE_ROWS:
        print(f"  … {len(ranked) - TABLE_ROWS} more (all in the sheet, best first)")
    if code != 0:
        print(f"  ! exited with code {code}")
    for b in bangs[:5]:
        print(f"  ! {b}")
    if len(bangs) > 5:
        print(f"  ! … {len(bangs) - 5} more in the log")
    if report:
        print("  Agent report:" if not a.direct else "  Run report:")
        for line in report[:REPORT_LINES]:
            print(f"    {line}")
        if len(report) > REPORT_LINES:
            print(f"    … {len(report) - REPORT_LINES} more lines in the log")
    print(f"  Log:  {log_path}")
    print(f"  View: less +G {log_path}", flush=True)


def run_lane(a):
    started, t0 = datetime.now(), time.time()
    os.makedirs(a.logs_dir, exist_ok=True)
    log_path = os.path.join(a.logs_dir, f"{a.agent}-{started:%Y%m%d-%H%M}.log")
    prog_path = (default_path(a.agent) if a.logs_dir == LOGS
                 else os.path.join(a.logs_dir, f"progress-{a.agent}.log"))
    open(prog_path, "w").close()
    env = dict(os.environ, JOB_PROGRESS_FILE=prog_path, PYTHONUNBUFFERED="1")
    print(f"{BAR} {a.agent.capitalize()} · {a.label} · started {started:%H:%M} {BAR}", flush=True)

    bangs, stop = [], threading.Event()
    tail = threading.Thread(target=tail_progress, args=(prog_path, stop, bangs), daemon=True)
    tail.start()
    found = None
    with open(log_path, "a") as log:
        if a.direct:
            code, report, found = run_direct(a, env, log, bangs)
        else:
            code, report = run_agent(a, env, log, bangs)
    stop.set()
    tail.join(timeout=5)

    seconds = time.time() - t0
    cands = candidates_since(a.runs_dir, a.agent, t0)
    if a.dry_run:
        jobs = list(cands.values())
    else:
        jobs = [dict(j, **{k: cands.get(j["job_link"], {}).get(k) for k in ("score", "yoe")})
                for j in appended_since(a.db, a.agent, started.isoformat(timespec="seconds"))]
    print_summary(a, code, report, bangs, jobs, seconds, log_path)

    with open(os.path.join(a.logs_dir, f"last-run-{a.agent}.json"), "w") as f:
        json.dump({"agent": a.agent, "added": len(jobs), "exit": code, "seconds": int(seconds),
                   "jobs": [{k: j.get(k) for k in ("company", "role", "job_link")} for j in jobs]}, f)
    companies = ", ".join(dict.fromkeys(j["company"] for j in jobs))
    notify(f"Job pipeline · {a.agent.capitalize()}",
           f"{_plural(len(jobs))} added" + (f": {companies}" if companies else "")
           + (f" (exit {code})" if code else ""))
    return code


def run_total(a):
    started = float(os.environ.get("JOB_RUN_STARTED") or 0)
    parts, total = [], 0
    for agent in [x for x in a.total.split(",") if x]:
        path = os.path.join(a.logs_dir, f"last-run-{agent}.json")
        try:
            if os.path.getmtime(path) < started:
                raise OSError("stale")
            with open(path) as f:
                r = json.load(f)
        except (OSError, ValueError):
            parts.append(f"{agent} –")
            continue
        total += r.get("added", 0)
        parts.append(f"{agent} {r.get('added', 0)}" + ("!" if r.get("exit") else ""))
    took = f" · {fmt_duration(time.time() - started)}" if started else ""
    print(f"{BAR} Total: {_plural(total)} added ({', '.join(parts)}){took} {BAR}", flush=True)
    notify("Job pipeline", f"{_plural(total)} added ({', '.join(parts)})", force=True)
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agent")
    ap.add_argument("--label", default="")
    ap.add_argument("--stdin", help="file fed to the agent's stdin (the prompt)")
    ap.add_argument("--stream", choices=["claude", "lines"], default="lines")
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--retry-wait", type=int, default=120)
    ap.add_argument("--direct", help="sources for a no-LLM discover + add_job run")
    ap.add_argument("--dry-run", action="store_true", help="--direct only: add_job --dry-run")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--logs-dir", default=LOGS)
    ap.add_argument("--runs-dir", default=RUNS)
    ap.add_argument("--total", help="comma-separated agents: print the combined total")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.command and a.command[0] == "--":
        a.command = a.command[1:]
    if not a.total and not a.agent:
        ap.error("--agent is required")
    if not (a.total or a.direct or a.command):
        ap.error("give a command after --, or --direct SOURCES")
    return a


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    return run_total(a) if a.total else run_lane(a)


if __name__ == "__main__":
    sys.exit(main())
