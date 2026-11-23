"""The call capacity IAM policy (decision 103) scopes the quota actions to
this account's Amazon Connect quotas by resource ARN, not a condition key."""

import re
from pathlib import Path

VOICE_TF = Path(__file__).resolve().parents[2] / "terraform" / "voice.tf"


def _policy() -> str:
    text = VOICE_TF.read_text()
    m = re.search(r'data "aws_iam_policy_document" "voice_ecs_quotas" \{(.*?)\n\}\n', text, re.S)
    assert m, "voice_ecs_quotas policy missing"
    return m.group(1)


def test_quota_actions_are_scoped_to_connect_quotas():
    policy = _policy()
    stmt = re.search(r'sid = "ConnectQuotas"(.*?)\n  \}', policy, re.S).group(1)
    assert '"servicequotas:GetServiceQuota"' in stmt
    assert '"servicequotas:RequestServiceQuotaIncrease"' in stmt
    assert ("arn:aws:servicequotas:*:${data.aws_caller_identity.current.account_id}"
            ":connect/*") in stmt


def test_no_unverified_condition_key_and_no_support_api():
    policy = _policy()
    assert "servicequotas:service" not in policy
    assert "support:" not in policy


def test_request_increase_is_never_granted_on_star():
    policy = _policy()
    for stmt in re.findall(r"statement \{(.*?)\n  \}", policy, re.S):
        if "RequestServiceQuotaIncrease" in stmt:
            assert 'resources = ["*"]' not in stmt
