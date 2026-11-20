"""Merging the voice Terraform creates nothing billable (#259 step 6).

A cheap textual check, no terraform binary: every resource and data source in
terraform/voice.tf is gated on var.voice_enabled, the agent reconcile
schedule on var.agent_reconcile_enabled, the FTC ingest schedule on
var.ftc_ingest_enabled, the analytics lake (#259 step 10) on
var.analytics_enabled, and every flag defaults to false.
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
    ("ftc_ingest_schedule.tf", "ftc_ingest_enabled"),
    ("analytics.tf", "analytics_enabled"),
])
def test_every_block_is_gated(filename: str, flag: str) -> None:
    blocks = _blocks(TERRAFORM / filename)
    assert blocks, f"no resource/data blocks parsed from {filename}"
    ungated = [addr for addr, first in blocks if not _gated(first, flag)]
    assert ungated == [], f"not gated on var.{flag}: {ungated}"


@pytest.mark.parametrize("flag", ["voice_enabled", "voice_provisioning_enabled",
                                  "agent_reconcile_enabled", "ftc_ingest_enabled",
                                  "analytics_enabled"])
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


def test_screening_resources_exist_and_are_voice_gated() -> None:
    addrs = {addr for addr, _ in _blocks(TERRAFORM / "voice.tf")}
    for addr in ("resource.aws_lambda_function.voice_screen",
                 "resource.aws_cloudwatch_log_group.voice_screen",
                 "resource.aws_lambda_permission.voice_screen_connect",
                 "resource.aws_connect_lambda_function_association.voice_screen"):
        assert addr in addrs, f"{addr} missing"
    text = (TERRAFORM / "voice.tf").read_text()
    fn = re.search(r'resource "aws_lambda_function" "voice_screen" \{(.*?)\n\}', text, re.S)
    assert fn
    body = fn.group(1)
    assert 'handler          = "screen.handler"' in body
    assert re.search(r"timeout\s*=\s*3\b", body)
    assert re.search(r"memory_size\s*=\s*512\b", body)
    assert "filename         = var.voice_bridge_zip" in body
    perm = re.search(
        r'resource "aws_lambda_permission" "voice_screen_connect" \{(.*?)\n\}', text, re.S)
    assert perm and 'principal     = "connect.amazonaws.com"' in perm.group(1)
    assert "source_arn    = aws_connect_instance.voice[0].arn" in perm.group(1)


def test_contact_flow_screens_first_and_fails_open() -> None:
    text = (TERRAFORM / "voice" / "contact_flow.json.tftpl").read_text()
    assert 'StartAction = "screen"' in text
    assert 'Type       = "InvokeLambdaFunction"' in text
    assert 'ResponseValidation         = { ResponseType = "STRING_MAP" }' in text
    assert 'InvocationTimeLimitSeconds = "3"' in text
    assert '{ NextAction = "set-voice", ErrorType = "NoMatchingError" }' in text
    assert re.search(r'NextAction = "disconnect", Condition = \{ Operator = "Equals", '
                     r'Operands = \["block"\] \}', text)


def test_ftc_schedule_default() -> None:
    text = (TERRAFORM / "variables.tf").read_text()
    m = re.search(r'variable "ftc_ingest_schedule" \{(.*?)\n\}', text, re.S)
    assert m and 'default     = "cron(0 18 ? * MON *)"' in m.group(1)
    sched = (TERRAFORM / "ftc_ingest_schedule.tf").read_text()
    assert "schedule_expression = var.ftc_ingest_schedule" in sched
    assert '["python", "-m", "backend.scripts.run_ftc_ingest"]' in sched


def test_ecs_task_definition_unchanged_with_analytics_off() -> None:
    """ANALYTICS_BUCKET joins the environment only through an
    `var.analytics_enabled ? [...] : []` list, and the lake is referenced only
    inside it."""
    text = (TERRAFORM / "ecs.tf").read_text()
    m = re.search(r"var\.analytics_enabled \? \[(.*?)\] : \[\]\)", text, re.S)
    assert m, "ANALYTICS_BUCKET is not behind a conditional concat"
    assert '"ANALYTICS_BUCKET"' in m.group(1)
    assert "aws_s3_bucket.analytics_lake[0].id" in m.group(1)
    outside = text[:m.start()] + text[m.end():]
    assert "analytics" not in outside.replace("analytics.tf", "")


def test_analytics_schedule_and_retention_defaults() -> None:
    text = (TERRAFORM / "variables.tf").read_text()
    m = re.search(r'variable "analytics_schedule" \{(.*?)\n\}', text, re.S)
    assert m and 'default     = "cron(30 6 * * ? *)"' in m.group(1)
    m = re.search(r'variable "analytics_retention_days" \{(.*?)\n\}', text, re.S)
    assert m and re.search(r"default\s*=\s*365\b", m.group(1))
    tf = (TERRAFORM / "analytics.tf").read_text()
    assert "schedule_expression = var.analytics_schedule" in tf
    assert '["python", "-m", "backend.scripts.run_agent_analytics"]' in tf
    assert "days = var.analytics_retention_days" in tf
    results = re.search(
        r'resource "aws_s3_bucket_lifecycle_configuration" "analytics_results" \{(.*?)\n\}',
        tf, re.S)
    assert results and re.search(r"days = 30\b", results.group(1))


def test_glue_tables_are_parquet_with_dt_projection() -> None:
    tf = (TERRAFORM / "analytics.tf").read_text()
    m = re.search(r'resource "aws_glue_catalog_table" "analytics" \{(.*?)\n\}', tf, re.S)
    assert m
    body = m.group(1)
    assert "for_each = var.analytics_enabled ? local.analytics_columns : {}" in body
    assert 'table_type    = "EXTERNAL_TABLE"' in body
    assert "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe" in body
    assert "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat" in body
    assert "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat" in body
    for line in ('"projection.enabled"          = "true"',
                 '"projection.dt.type"          = "date"',
                 '"projection.dt.format"        = "yyyy-MM-dd"',
                 '"projection.dt.range"         = "2026-11-01,NOW"',
                 '"projection.dt.interval"      = "1"',
                 '"projection.dt.interval.unit" = "DAYS"'):
        assert line in body, line
    assert "/${each.key}/dt=$${dt}/" in body
    assert re.search(r'partition_keys \{\s*name = "dt"\s*type = "string"', body)


def test_task_role_reaches_the_lake_prefix_only() -> None:
    tf = (TERRAFORM / "analytics.tf").read_text()
    m = re.search(r'data "aws_iam_policy_document" "ecs_task_analytics" \{(.*?)\n\}', tf, re.S)
    assert m
    resources = re.findall(r"^\s*resources = \[(.*)\]$", m.group(1), re.M)
    assert resources == [
        '"${aws_s3_bucket.analytics_lake[0].arn}/${local.analytics_prefix}/*"',
        "aws_s3_bucket.analytics_lake[0].arn",
    ]
    assert 'values   = ["${local.analytics_prefix}/*"]' in m.group(1)
