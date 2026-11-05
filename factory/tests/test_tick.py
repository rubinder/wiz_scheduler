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
