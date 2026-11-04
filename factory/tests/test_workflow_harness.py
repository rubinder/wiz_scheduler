"""Run factory-merge.js and factory-verify.js against a scripted agent() in node."""
import subprocess
from pathlib import Path

from factory.tests.test_pipeline_harness import require_node

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".claude" / "workflows"


def test_merge_and_verify_scenarios():
    require_node()
    proc = subprocess.run(["node", str(Path(__file__).with_name("workflow_harness.cjs")),
                           str(WORKFLOWS / "factory-merge.js"), str(WORKFLOWS / "factory-verify.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout


def test_guides_pipeline_scenarios():
    require_node()
    proc = subprocess.run(["node", str(Path(__file__).with_name("guides_harness.cjs")),
                           str(WORKFLOWS / "guides-pipeline.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout
