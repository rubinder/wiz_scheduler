"""Run factory-merge.js and factory-verify.js against a scripted agent() in node."""
import json
import re
import subprocess
from pathlib import Path

from factory.gates import load_gates

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


def test_retro_scenarios():
    require_node()
    proc = subprocess.run(["node", str(Path(__file__).with_name("retro_harness.cjs")),
                           str(WORKFLOWS / "factory-retro.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout


def test_propose_scenarios():
    require_node()
    proc = subprocess.run(["node", str(Path(__file__).with_name("propose_harness.cjs")),
                           str(WORKFLOWS / "factory-propose.js")],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passing" in proc.stdout


def test_retro_known_gates_match_gate_files():
    text = (WORKFLOWS / "factory-retro.js").read_text()
    m = re.search(r"^const KNOWN_GATES = (\[[^\]]*\])$", text, re.M)
    assert m, "KNOWN_GATES not found in factory-retro.js"
    assert sorted(json.loads(m.group(1).replace("'", '"'))) == sorted(g.name for g in load_gates())


def test_retro_and_propose_agent_types_exist():
    agents = ROOT / ".claude" / "agents"
    for wf in ("factory-retro.js", "factory-propose.js"):
        for name in set(re.findall(r"agentType: '([a-z-]+)'", (WORKFLOWS / wf).read_text())):
            assert (agents / f"{name}.md").exists(), f"{wf} uses missing agent type {name}"


def test_retro_investigator_is_read_only():
    text = (ROOT / ".claude" / "agents" / "retro-investigator.md").read_text()
    tools = re.search(r"^tools: (.+)$", text, re.M).group(1)
    assert [t.strip() for t in tools.split(",")] == ["Read", "Grep", "Glob"]
    assert "Bash" not in text.split("---")[1]
    retro = (WORKFLOWS / "factory-retro.js").read_text()
    assert "agentType: 'code-reviewer'" not in retro
    assert retro.count("agentType: 'retro-investigator'") == 2
