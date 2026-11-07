"""Run issue-pipeline.js's gate and ship phases against a scripted agent() in node."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from factory.gates import load_gates

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / ".claude" / "workflows" / "issue-pipeline.js"


def require_node() -> None:
    """Skip locally without node; in CI a missing node is a failure, not a silent skip."""
    if shutil.which("node") is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI to run the workflow harnesses")
        pytest.skip("node is not installed")


def _js_list(name: str) -> list[str]:
    m = re.search(rf"^const {name} = (\[[^\]]*\])$", PIPELINE.read_text(), re.M)
    assert m, f"{name} not found in issue-pipeline.js"
    return json.loads(m.group(1).replace("'", '"'))


def test_gate_constants_match_gate_files():
    gates = load_gates()
    assert sorted(_js_list("KNOWN_GATES")) == sorted(g.name for g in gates)
    assert sorted(_js_list("ALWAYS_GATES")) == sorted(g.name for g in gates if g.always)
    assert sorted(_js_list("NO_REVIEWER_GATES")) == sorted(g.name for g in gates if not g.reviewer)


def test_issue_pipeline_gate_scenarios():
    require_node()
    proc = subprocess.run(["node", str(Path(__file__).with_name("pipeline_harness.cjs")), str(PIPELINE)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout


def test_guides_pipeline_gate_constants_match_gate_files():
    text = (ROOT / ".claude" / "workflows" / "guides-pipeline.js").read_text()
    gates = load_gates()
    for name, want in (("KNOWN_GATES", [g.name for g in gates]), ("ALWAYS_GATES", [g.name for g in gates if g.always])):
        m = re.search(rf"^const {name} = (\[[^\]]*\])$", text, re.M)
        assert m, name
        assert sorted(json.loads(m.group(1).replace("'", '"'))) == sorted(want)


def test_triager_keys_split_approval_on_the_pipelines_split_marker():
    """The triager recognises a split proposal by the marker the too_big comment carries; the two must not drift."""
    m = re.search(r"^const SPLIT_MARKER = '([^']+)'$", PIPELINE.read_text(), re.M)
    assert m, "SPLIT_MARKER not found in issue-pipeline.js"
    triager = (ROOT / ".claude" / "agents" / "issue-triager.md").read_text()
    assert "split_approved" in triager
    assert m.group(1) in triager
