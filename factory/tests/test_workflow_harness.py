"""Run factory-merge.js and factory-verify.js against a scripted agent() in node."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".claude" / "workflows"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_merge_and_verify_scenarios():
    proc = subprocess.run(["node", str(Path(__file__).with_name("workflow_harness.cjs")),
                           str(WORKFLOWS / "factory-merge.js"), str(WORKFLOWS / "factory-verify.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_guides_pipeline_scenarios():
    proc = subprocess.run(["node", str(Path(__file__).with_name("guides_harness.cjs")),
                           str(WORKFLOWS / "guides-pipeline.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout
