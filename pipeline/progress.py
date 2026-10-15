"""One-line progress messages for the terminal runner (pipeline/runner.py).

discover.py and add_job.py call init(agent) once, then progress("...").
Lines go to $JOB_PROGRESS_FILE, else logs/progress-<agent>.log, which the
runner shows live. Writing never raises: a lost line must not break a run.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path = None


def default_path(agent):
    return os.path.join(ROOT, "logs", f"progress-{agent}.log")


def init(agent):
    global _path
    _path = os.environ.get("JOB_PROGRESS_FILE") or default_path(agent)


def progress(msg):
    if not _path:
        return
    try:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        with open(_path, "a") as f:
            f.write(msg.rstrip("\n") + "\n")
    except OSError:
        pass
