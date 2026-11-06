"""tick.sh: claude never inherits the tick lock, and a timeout kills its whole process group."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

TICK = Path(__file__).resolve().parents[1] / "tick.sh"

CLAUDE_STUB = """#!/bin/bash
if { true >&9; } 2>/dev/null; then echo open > "$TICK_MARK/fd9"; else echo closed > "$TICK_MARK/fd9"; fi
sleep 300 &
echo $! > "$TICK_MARK/orphan.pid"
sleep 300
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(shutil.which("caffeinate") is None or shutil.which("perl") is None,
                    reason="tick.sh needs macOS caffeinate and perl")
def test_claude_runs_without_the_lock_fd_and_timeout_kills_orphans(tmp_path: Path):
    home, fh, mark = tmp_path / "home", tmp_path / "factory", tmp_path / "mark"
    for d in (home, mark, fh / "wiz_scheduler" / "backend" / ".venv" / "bin"):
        d.mkdir(parents=True)
    py = fh / "wiz_scheduler" / "backend" / ".venv" / "bin" / "python"
    py.write_text("#!/bin/bash\necho run\nexit 0\n")
    claude = tmp_path / "claude"
    claude.write_text(CLAUDE_STUB)
    py.chmod(0o755)
    claude.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "FACTORY_HOME": str(fh), "TMPDIR": str(tmp_path),
           "FACTORY_CLAUDE_BIN": str(claude), "FACTORY_TICK_TIMEOUT_S": "2", "TICK_MARK": str(mark)}
    proc = subprocess.run(["bash", str(TICK), "--dry-run"], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert (mark / "fd9").read_text().strip() == "closed"
    orphan = int((mark / "orphan.pid").read_text())
    for _ in range(20):
        if not _alive(orphan):
            break
        time.sleep(0.25)
    try:
        assert not _alive(orphan), "a background job of the timed-out claude survived"
    finally:
        if _alive(orphan):
            os.kill(orphan, 9)
    log = next((home / "Library" / "Logs" / "wizfactory").glob("*.log")).read_text()
    assert "exit timeout" in log


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}).stdout.strip()


def _factory_with_stale_clone(tmp_path: Path) -> tuple[dict, Path, Path, str]:
    """A factory whose app clone is one commit behind its origin; preflight records the HEAD it ran on."""
    home, fh, mark = tmp_path / "home", tmp_path / "factory", tmp_path / "mark"
    origin, seed = tmp_path / "origin.git", tmp_path / "seed"
    for d in (home, mark, fh, seed):
        d.mkdir(parents=True)
    _git("init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    _git("init", "-q", "-b", "main", cwd=seed)
    (seed / "f").write_text("1")
    _git("add", "f", cwd=seed)
    _git("commit", "-q", "-m", "one", cwd=seed)
    _git("push", "-q", str(origin), "main", cwd=seed)
    app = fh / "wiz_scheduler"
    _git("clone", "-q", str(origin), str(app), cwd=tmp_path)
    (seed / "f").write_text("2")
    _git("commit", "-q", "-am", "two", cwd=seed)
    _git("push", "-q", str(origin), "main", cwd=seed)
    new_head = _git("rev-parse", "HEAD", cwd=seed)
    py = app / "backend" / ".venv" / "bin" / "python"  # untracked, so the reset keeps it
    py.parent.mkdir(parents=True)
    py.write_text('#!/bin/bash\ngit rev-parse HEAD > "$TICK_MARK/head"\n'
                  'echo "$FACTORY_TICK_HOLDS_LOCK" > "$TICK_MARK/holds"\necho "owner active"\nexit 3\n')
    py.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "FACTORY_HOME": str(fh), "TMPDIR": str(tmp_path),
           "FACTORY_CLAUDE_BIN": "/bin/false", "TICK_MARK": str(mark)}
    return env, fh, mark, new_head


def test_clones_sync_before_preflight_so_a_preflight_fix_lands_while_ticks_skip(tmp_path: Path):
    env, fh, mark, new_head = _factory_with_stale_clone(tmp_path)
    proc = subprocess.run(["bash", str(TICK)], env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert (mark / "head").read_text().strip() == new_head
    assert (mark / "holds").read_text().strip() == "1"
    log = next((tmp_path / "home" / "Library" / "Logs" / "wizfactory").glob("*.log")).read_text()
    assert "skip (rc=3): owner active" in log


def test_no_sync_while_another_tick_holds_the_lock_or_the_factory_is_stopped(tmp_path: Path):
    import fcntl
    env, fh, mark, new_head = _factory_with_stale_clone(tmp_path)
    old_head = _git("rev-parse", "HEAD", cwd=fh / "wiz_scheduler")
    with open(fh / "tick.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        subprocess.run(["bash", str(TICK)], env=env, capture_output=True, text=True, timeout=60)
    assert _git("rev-parse", "HEAD", cwd=fh / "wiz_scheduler") == old_head and not (mark / "head").exists()
    (fh / "STOP").write_text("")
    subprocess.run(["bash", str(TICK)], env=env, capture_output=True, text=True, timeout=60)
    assert _git("rev-parse", "HEAD", cwd=fh / "wiz_scheduler") == old_head and not (mark / "head").exists()
    log = next((tmp_path / "home" / "Library" / "Logs" / "wizfactory").glob("*.log")).read_text()
    assert "skip: lock" in log and "skip: STOP file present" in log
