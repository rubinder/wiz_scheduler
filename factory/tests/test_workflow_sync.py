"""The deploy/smoke/revert region is copied into both workflows (the Workflow
runtime has no imports); the two copies must never drift apart."""
import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".claude" / "workflows"
BEGIN, END = "// BEGIN shared-deploy-smoke-revert", "// END shared-deploy-smoke-revert"


def _region(name: str) -> str:
    text = (WORKFLOWS / name).read_text()
    assert text.count(BEGIN) == 1 and text.count(END) == 1, f"{name} needs exactly one shared region"
    region = text[text.index(BEGIN):text.index(END) + len(END)]
    # Both copies take the commit as a parameter named SHA; normalise it anyway
    # so a rename in one file is reported as the only difference it is.
    return re.sub(r"\bSHA\b", "<sha>", region)


def test_shared_region_is_identical_in_merge_and_verify():
    merge, verify = _region("factory-merge.js"), _region("factory-verify.js")
    assert len(merge) > 2000
    assert merge == verify


def test_agent_definitions_scope_every_gh_command_to_a_repo():
    """An unscoped `gh` resolves the repository from the shell's checkout, which may be the other repo."""
    agents = Path(__file__).resolve().parents[2] / ".claude" / "agents"
    for f in sorted(agents.glob("*.md")):
        text = f.read_text()
        assert "repos/{owner}" not in text, f.name
        for cmd in re.findall(r"`(gh [^`]*)`", text):
            if len(cmd.split()) <= 3:
                continue  # a bare name such as `gh pr merge` in a prohibition, not a command to run
            if re.match(r"gh (pr|issue|run|label|release|workflow) ", cmd):
                assert "--repo <owner>/<repo>" in cmd, (f.name, cmd)
            elif cmd.startswith("gh api "):
                assert re.search(r"\brepos/<owner>/<repo>/", cmd) or "graphql" in cmd, (f.name, cmd)
