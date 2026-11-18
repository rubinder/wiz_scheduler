"""Merging the voice Terraform creates nothing billable (#259 step 6).

A cheap textual check, no terraform binary: every resource and data source in
terraform/voice.tf is gated on var.voice_enabled, the agent reconcile
schedule on var.agent_reconcile_enabled, and both flags default to false.
"""

import re
from pathlib import Path

import pytest

TERRAFORM = Path(__file__).resolve().parents[2] / "terraform"

_BLOCK = re.compile(r'^(resource|data) "([a-z0-9_]+)" "([a-z0-9_]+)" \{\n', re.M)


def _blocks(path: Path) -> list[tuple[str, str]]:
    """(address, first argument line) for each top-level resource/data block."""
    text = path.read_text()
    out = []
    for m in _BLOCK.finditer(text):
        body = text[m.end():]
        first = next(line.strip() for line in body.splitlines() if line.strip())
        out.append((f"{m.group(1)}.{m.group(2)}.{m.group(3)}", first))
    return out


def _gated(first_line: str, flag: str) -> bool:
    count = re.fullmatch(
        rf"count\s*=\s*var\.{flag}\b[^?]*\?\s*1\s*:\s*0", first_line)
    for_each = re.fullmatch(
        rf"for_each\s*=\s*var\.{flag}\s*\?\s*\S+\s*:\s*\{{\}}", first_line)
    return bool(count or for_each)


@pytest.mark.parametrize(("filename", "flag"), [
    ("voice.tf", "voice_enabled"),
    ("agent_reconcile_schedule.tf", "agent_reconcile_enabled"),
])
def test_every_block_is_gated(filename: str, flag: str) -> None:
    blocks = _blocks(TERRAFORM / filename)
    assert blocks, f"no resource/data blocks parsed from {filename}"
    ungated = [addr for addr, first in blocks if not _gated(first, flag)]
    assert ungated == [], f"not gated on var.{flag}: {ungated}"


@pytest.mark.parametrize("flag", ["voice_enabled", "voice_provisioning_enabled",
                                  "agent_reconcile_enabled"])
def test_flags_default_false(flag: str) -> None:
    text = (TERRAFORM / "variables.tf").read_text()
    m = re.search(rf'variable "{flag}" \{{(.*?)\n\}}', text, re.S)
    assert m, f"variable {flag} missing"
    assert re.search(r"^\s*default\s*=\s*false\s*$", m.group(1), re.M)


def test_ecs_task_definition_unchanged_with_voice_off() -> None:
    """The task definition only gains voice entries through empty-when-off
    lists: a splat over the gated secret, or a `var.voice_enabled ? [...] : []`."""
    text = (TERRAFORM / "ecs.tf").read_text()
    assert "aws_secretsmanager_secret.voice_bridge[*].arn" in text
    assert "aws_secretsmanager_secret.voice_bridge[0]" not in text
    assert re.search(r"\], var\.voice_enabled \? \[", text)
    assert re.search(r"\] : \[\]\)", text)


def test_connect_can_transfer_out_and_flow_logs_expire() -> None:
    """The staff handoff is an outbound transfer, and the flow logs (caller
    numbers) live in a group Terraform owns with a retention."""
    text = (TERRAFORM / "voice.tf").read_text()
    instance = re.search(r'resource "aws_connect_instance" "voice" \{(.*?)\n\}', text, re.S)
    assert instance and re.search(r"outbound_calls_enabled\s*=\s*true", instance.group(1))
    assert "depends_on = [aws_cloudwatch_log_group.voice_connect_flows]" in instance.group(1)
    logs = re.search(
        r'resource "aws_cloudwatch_log_group" "voice_connect_flows" \{(.*?)\n\}', text, re.S)
    assert logs, "Connect flow-log group missing"
    assert 'name              = "/aws/connect/${local.voice_connect_alias}"' in logs.group(1)
    assert re.search(r"retention_in_days\s*=\s*30\b", logs.group(1))
    assert re.search(r"instance_alias\s*=\s*local\.voice_connect_alias", instance.group(1))
