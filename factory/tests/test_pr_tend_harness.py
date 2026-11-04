"""Run pr-tend.js against a scripted agent() in node."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PR_TEND = ROOT / ".claude" / "workflows" / "pr-tend.js"


def test_pr_tend_scenarios():
    if shutil.which("node") is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to run the workflow harnesses")
        pytest.skip("node is not installed")
    proc = subprocess.run(["node", str(Path(__file__).with_name("pr_tend_harness.cjs")), str(PR_TEND)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout
