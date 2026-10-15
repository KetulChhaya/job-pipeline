import progress


def test_lines_go_to_the_env_file(tmp_path, monkeypatch):
    path = tmp_path / "p.log"
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(path))
    progress.init("claude")
    progress.progress("fetching 3 boards")
    progress.progress("kept 1\n")
    assert path.read_text() == "fetching 3 boards\nkept 1\n"


def test_default_path_and_unwritable_file(tmp_path, monkeypatch):
    monkeypatch.delenv("JOB_PROGRESS_FILE")
    assert progress.default_path("gemini").endswith("logs/progress-gemini.log")
    monkeypatch.setenv("JOB_PROGRESS_FILE", str(tmp_path / "missing-dir" / "x" / "p.log"))
    (tmp_path / "missing-dir").write_text("a file, so the folder can't be created")
    progress.init("claude")
    progress.progress("lost, and that's fine")   # must not raise
