"""stack.sh behaviour with a stub FACTORY_HOME and stub docker/ps on PATH (no Docker needed)."""
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "stack.sh"


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(f'#!/bin/bash\necho "$@" >> {tmp_path}/docker.log\nexit 0\n')
    (bin_ / "ps").write_text('#!/bin/bash\necho "$STUB_PS"\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    e = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    e.update(PATH=f"{bin_}:{e['PATH']}", FACTORY_HOME=str(home), STUB_PS="")
    return e, home, tmp_path


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=env["FACTORY_HOME"])


def own(home, e, token="tok"):
    """Pretend a run holds the lock, and make this caller its owner."""
    (home / "stack.lock").mkdir(exist_ok=True)
    (home / "stack.lock" / "owner").write_text(token + "\n")
    e["STACK_OWNER"] = token


def test_down_kills_only_verified_process(env):
    e, home, _ = env
    own(home, e)
    victim = subprocess.Popen(["sleep", "60"])
    try:
        (home / "stack-api.pid").write_text(str(victim.pid))
        e["STUB_PS"] = "/usr/bin/some other program"
        assert run(e, "down").returncode == 0
        time.sleep(0.2)
        assert victim.poll() is None  # not ours: left alone
        assert not (home / "stack-api.pid").exists()  # stale file removed
        own(home, e)  # the first down released the lock

        (home / "stack-api.pid").write_text(str(victim.pid))
        e["STUB_PS"] = "python -m uvicorn backend.main:app --port 8000"
        assert run(e, "down").returncode == 0
        assert victim.wait(timeout=5) is not None
    finally:
        victim.kill()


def test_down_releases_lock_and_removes_env(env):
    e, home, _ = env
    own(home, e)
    (home / "stack.env").write_text("x")
    assert run(e, "down").returncode == 0
    assert not (home / "stack.lock").exists() and not (home / "stack.env").exists()


def test_down_without_matching_token_changes_nothing(env):
    e, home, tmp = env
    own(home, e, "theirs")
    (home / "stack.env").write_text("x")
    (home / "stack-api.pid").write_text("1")
    for token in (None, "wrong"):
        e.pop("STACK_OWNER", None)
        if token:
            e["STACK_OWNER"] = token
        assert run(e, "down").returncode == 3
    assert (home / "stack.lock" / "owner").read_text().strip() == "theirs"
    assert (home / "stack.env").exists() and (home / "stack-api.pid").exists()
    assert not (tmp / "docker.log").exists()


def test_up_when_locked_then_down_leaves_other_run_alone(env):
    e, home, tmp = env
    own(home, e, "theirs")
    (home / "stack.env").write_text("x")
    e.pop("STACK_OWNER")
    assert run(e, "up").returncode == 3
    assert run(e, "down").returncode == 3
    assert (home / "stack.lock" / "owner").read_text().strip() == "theirs"
    assert (home / "stack.env").exists() and not (tmp / "docker.log").exists()


def test_up_and_roundtrip_fail_fast_when_locked(env):
    e, home, tmp = env
    (home / "stack.lock").mkdir()
    for cmd in ("up", "migrate-roundtrip"):
        r = run(e, cmd)
        assert r.returncode == 3 and "stack.lock" in r.stderr
    assert not (tmp / "docker.log").exists()  # never reached docker


def test_roundtrip_releases_lock_on_failure(env):
    e, home, _ = env
    e["PY"] = "/bin/false"
    (home / "backend").mkdir()  # script does `cd backend` relative to cwd
    r = run(e, "migrate-roundtrip")
    assert r.returncode != 0
    assert not (home / "stack.lock").exists()


def test_up_clears_stale_pid_files_after_lock(env):
    e, home, _ = env
    (home / "stack-api.pid").write_text("1")
    e["PY"] = "/bin/false"
    (home / "backend").mkdir()
    run(e, "up")  # fails at alembic; the stale pid file must already be gone
    assert not (home / "stack-api.pid").exists()


def test_mid_up_failure_releases_its_own_lock_and_container(env):
    e, home, tmp = env
    e["PY"] = "/bin/false"
    (home / "backend").mkdir()
    r = run(e, "up")
    assert r.returncode != 0
    assert not (home / "stack.lock").exists() and not (home / "stack.env").exists()
    assert "rm -f wizfactory-pg" in (tmp / "docker.log").read_text()


def test_up_exit_3_does_not_release_the_held_lock(env):
    e, home, _ = env
    own(home, e, "theirs")
    assert run(e, "up").returncode == 3
    assert (home / "stack.lock" / "owner").read_text().strip() == "theirs"


def test_no_unpinned_npx_anywhere():
    import re
    root = SCRIPT.parent
    for f in [SCRIPT, *(root / "gates").glob("*.md")]:
        for m in re.finditer(r"\bnpx\b(?!\s+--no-install\b)", f.read_text()):
            raise AssertionError(f"{f.name}: npx without --no-install near offset {m.start()}")


def test_refuses_non_local_incoming_database_url(env):
    e, home, tmp = env
    e["DATABASE_URL"] = "postgresql+asyncpg://u:p@db.prod.example.com:5432/x"
    for cmd in ("up", "migrate-roundtrip"):
        r = run(e, cmd)
        assert r.returncode == 2 and "not local" in r.stderr
    assert not (tmp / "docker.log").exists()


def _dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


def test_dead_owner_lock_is_reclaimed_and_up_proceeds(env):
    e, home, tmp = env
    own(home, e, "theirs")
    (home / "stack.lock" / "pid").write_text(str(_dead_pid()))
    (home / "stack.env").write_text("x")
    (home / "backend").mkdir()
    e["PY"] = "/bin/false"  # up fails later, at alembic; reclaim happened before that
    r = run(e, "up")
    assert "reclaiming stale" in r.stderr
    log = (tmp / "docker.log").read_text()
    assert "rm -f wizfactory-pg" in log and "run -d" in log  # teardown, then the new run's postgres
    assert not list(home.glob("stack.lock*"))  # stale dir removed, own lock released by the trap


def test_live_owner_lock_still_exits_3(env):
    e, home, tmp = env
    own(home, e, "theirs")
    (home / "stack.lock" / "pid").write_text(str(os.getpid()))
    assert run(e, "up").returncode == 3
    assert (home / "stack.lock" / "owner").read_text().strip() == "theirs"
    assert not (tmp / "docker.log").exists()


def test_missing_pid_on_fresh_lock_exits_3_but_old_one_is_reclaimed(env):
    e, home, tmp = env
    own(home, e, "theirs")
    assert run(e, "up").returncode == 3
    assert (home / "stack.lock").exists()
    old = time.time() - 3 * 3600
    os.utime(home / "stack.lock", (old, old))
    (home / "backend").mkdir()
    e["PY"] = "/bin/false"
    assert "reclaiming stale" in run(e, "up").stderr


def test_background_servers_do_not_inherit_the_tick_lock(env):
    # tick.sh holds its lock on fd 9; a server left running must never keep it.
    e, home, tmp = env
    probe = 'if { true >&9; } 2>/dev/null; then echo open; else echo closed; fi'
    py = tmp / "py"
    py.write_text(f'#!/bin/bash\ncase "$*" in *uvicorn*) {probe} > {tmp}/api-fd9;; esac\nexit 0\n')
    (tmp / "bin" / "npx").write_text(f'#!/bin/bash\n{probe} > {tmp}/vite-fd9\n')
    (tmp / "bin" / "curl").write_text("#!/bin/bash\nexit 0\n")
    for f in (py, tmp / "bin" / "npx", tmp / "bin" / "curl"):
        f.chmod(0o755)
    (home / "backend").mkdir()
    (home / "frontend").mkdir()
    e["PY"] = str(py)
    r = subprocess.run(["bash", "-c", f'exec 9>"{tmp}/tick.lock"; bash "{SCRIPT}" up'],
                       capture_output=True, text=True, env=e, cwd=home)
    assert r.returncode == 0, r.stderr
    for _ in range(40):
        if (tmp / "api-fd9").exists() and (tmp / "vite-fd9").exists():
            break
        time.sleep(0.05)
    assert (tmp / "api-fd9").read_text().strip() == "closed"
    assert (tmp / "vite-fd9").read_text().strip() == "closed"
