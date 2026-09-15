"""Tests for the /work-issues driver's snapshot + decision module.

`state.py` lives under `.claude/skills/work-issues/` (a hyphenated path, so
it is loaded by file path rather than imported). Everything here is pure:
`gh` is replaced by a fake runner keyed on the argument list.
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "work-issues" / "state.py"
_spec = importlib.util.spec_from_file_location("agent_state", STATE)
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)


def pr(**kw):
    base = dict(
        number=50, branch="agent/issue-7-x", issue=7, checks="success",
        review_decision="", unanswered_threads=0, labels=["agent-pr-open"],
        last_agent_push_at="2026-09-14T10:00:00Z", last_review_at=None,
        last_commit_at="2026-09-14T10:00:00Z", failing_checks=[],
        last_ci_comment_checks=[],
    )
    base.update(kw)
    return base


def issue(**kw):
    base = dict(number=7, title="t", labels=["agent-ready"],
                updated_at="2026-09-01T00:00:00Z", last_commit_at=None)
    base.update(kw)
    return base


def snap(issues=(), prs=()):
    return {"issues": list(issues), "prs": list(prs)}


# --- choose_action -----------------------------------------------------------

def test_idle_when_nothing_to_do():
    action = state.choose_action(snap(), NOW)
    assert action["kind"] == "idle"
    assert action["reason"]


def test_failing_ci_beats_everything():
    s = snap(issues=[issue()], prs=[pr(checks="failure", unanswered_threads=3)])
    action = state.choose_action(s, NOW)
    assert action["kind"] == "pr-tend"
    assert action["workflow"] == "pr-tend"
    assert action["args"]["pr"] == 50
    assert action["args"]["mode"] == "ci"


def test_blocked_pr_is_skipped():
    s = snap(prs=[pr(checks="failure", labels=["agent-pr-open", "agent-blocked"])])
    assert state.choose_action(s, NOW)["kind"] == "idle"


def test_repeat_failure_flag_set_when_same_checks_failed_last_time():
    s = snap(prs=[pr(checks="failure", failing_checks=["test"], last_ci_comment_checks=["test"])])
    assert state.choose_action(s, NOW)["args"]["repeat_failure"] is True


def test_repeat_failure_flag_false_for_new_failure():
    s = snap(prs=[pr(checks="failure", failing_checks=["build"], last_ci_comment_checks=["test"])])
    assert state.choose_action(s, NOW)["args"]["repeat_failure"] is False


def test_unanswered_threads_trigger_review_mode():
    s = snap(prs=[pr(unanswered_threads=1)])
    action = state.choose_action(s, NOW)
    assert action["kind"] == "pr-tend"
    assert action["args"]["mode"] == "review"


def test_changes_requested_after_last_push_triggers_review():
    s = snap(prs=[pr(review_decision="CHANGES_REQUESTED", last_review_at="2026-09-14T11:00:00Z")])
    assert state.choose_action(s, NOW)["args"]["mode"] == "review"


def test_changes_requested_before_last_push_is_already_handled():
    s = snap(prs=[pr(review_decision="CHANGES_REQUESTED", last_review_at="2026-09-14T09:00:00Z")])
    assert state.choose_action(s, NOW)["kind"] == "idle"


def test_pending_ci_is_not_a_failure():
    s = snap(prs=[pr(checks="pending")])
    assert state.choose_action(s, NOW)["kind"] == "idle"


def test_oldest_agent_ready_issue_is_chosen():
    s = snap(issues=[issue(number=9), issue(number=3), issue(number=12)])
    action = state.choose_action(s, NOW)
    assert action["kind"] == "issue-pipeline"
    assert action["workflow"] == "issue-pipeline"
    assert action["args"] == {"issue": 3}


def test_pr_work_beats_new_issue():
    s = snap(issues=[issue(number=3)], prs=[pr(unanswered_threads=1)])
    assert state.choose_action(s, NOW)["kind"] == "pr-tend"


def test_issues_with_other_agent_labels_are_skipped():
    s = snap(issues=[
        issue(number=1, labels=["agent-ready", "agent-blocked"]),
        issue(number=2, labels=["agent-ready", "agent-pr-open"]),
        issue(number=4, labels=["agent-ready", "agent-working"],
              last_commit_at="2026-09-14T11:30:00Z"),
    ])
    assert state.choose_action(s, NOW)["kind"] == "idle"


def test_stale_working_issue_is_resumed():
    s = snap(issues=[issue(number=4, labels=["agent-working"],
                           last_commit_at="2026-09-14T08:00:00Z")])
    action = state.choose_action(s, NOW)
    assert action["kind"] == "issue-pipeline"
    assert action["args"] == {"issue": 4}
    assert "stale" in action["reason"]


def test_working_issue_with_no_branch_yet_is_stale_only_after_threshold():
    # No branch/commit at all: fall back to the issue's updated_at (the label event).
    fresh = snap(issues=[issue(number=4, labels=["agent-working"],
                               updated_at="2026-09-14T11:00:00Z")])
    stale = snap(issues=[issue(number=4, labels=["agent-working"],
                               updated_at="2026-09-14T05:00:00Z")])
    assert state.choose_action(fresh, NOW)["kind"] == "idle"
    assert state.choose_action(stale, NOW)["kind"] == "issue-pipeline"


def test_new_issue_beats_stale_resume():
    s = snap(issues=[
        issue(number=4, labels=["agent-working"], last_commit_at="2026-09-14T01:00:00Z"),
        issue(number=9),
    ])
    assert state.choose_action(s, NOW)["args"] == {"issue": 9}


# --- collectors ---------------------------------------------------------------

def test_checks_from_rollup():
    f = state.checks_from_rollup
    assert f([]) == "none"
    assert f([{"conclusion": "SUCCESS"}, {"conclusion": "SKIPPED"}]) == "success"
    assert f([{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS", "conclusion": ""}]) == "pending"
    assert f([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == "failure"


def test_failing_check_names():
    rollup = [{"name": "test", "conclusion": "FAILURE"}, {"name": "build", "conclusion": "SUCCESS"},
              {"context": "legacy-status", "state": "ERROR"}]
    assert state.failing_check_names(rollup) == ["legacy-status", "test"]


def test_issue_number_from_body_or_branch():
    assert state.issue_for_pr("Fixes stuff\n\nCloses #12\n<!-- wizbot -->", "agent/issue-12-x") == 12
    assert state.issue_for_pr("no ref", "agent/issue-34-slug") == 34
    assert state.issue_for_pr("no ref", "feat/other") is None


def test_thread_is_unanswered_unless_last_comment_is_agent():
    agent_last = {"isResolved": False, "comments": {"nodes": [{"body": "done " + state.AGENT_MARKER}]}}
    human_last = {"isResolved": False, "comments": {"nodes": [{"body": "please fix"}]}}
    resolved = {"isResolved": True, "comments": {"nodes": [{"body": "please fix"}]}}
    assert state.count_unanswered([agent_last, human_last, resolved]) == 1


def test_checks_from_last_ci_comment():
    comments = [
        {"body": "hello"},
        {"body": "Fixed CI.\n\nfailing checks: test, build\n" + state.AGENT_MARKER},
        {"body": "human note"},
    ]
    assert state.last_ci_comment_checks(comments) == ["build", "test"]


def test_collect_builds_snapshot_from_fake_gh():
    calls = []

    def run(args):
        calls.append(args)
        if args[:2] == ["repo", "view"]:
            return json.dumps({"nameWithOwner": "acme/widgets"})
        if args[:2] == ["issue", "list"]:
            label = args[args.index("--label") + 1]
            if label == "agent-ready":
                return json.dumps([{"number": 7, "title": "T", "updatedAt": "2026-09-01T00:00:00Z",
                                    "labels": [{"name": "agent-ready"}]}])
            return "[]"
        if args[:2] == ["pr", "list"]:
            return json.dumps([{
                "number": 50, "headRefName": "agent/issue-8-y", "body": "Closes #8\n<!-- wizbot -->",
                "labels": [{"name": "agent-pr-open"}], "reviewDecision": "",
                "statusCheckRollup": [{"name": "test", "conclusion": "FAILURE"}],
                "commits": [{"committedDate": "2026-09-14T10:00:00Z"}],
            }])
        if args[0] == "api" and "graphql" in args:
            return json.dumps({"data": {"repository": {"pullRequest": {
                "reviewThreads": {"nodes": [{"isResolved": False, "comments": {"nodes": [{"body": "fix"}]}}]},
                "reviews": {"nodes": [{"state": "CHANGES_REQUESTED", "submittedAt": "2026-09-14T11:00:00Z"}]},
                "comments": {"nodes": [{"body": "old"}]},
            }}}})
        if args[0] == "api" and args[1].startswith("repos/") and "/branches/" in args[1]:
            return json.dumps({"commit": {"commit": {"committer": {"date": "2026-09-14T10:00:00Z"}}}})
        raise AssertionError(f"unexpected gh call {args}")

    snapshot = state.collect(run, NOW)
    assert snapshot["issues"] == [{"number": 7, "title": "T", "labels": ["agent-ready"],
                                   "updated_at": "2026-09-01T00:00:00Z", "last_commit_at": None}]
    p = snapshot["prs"][0]
    assert p["number"] == 50 and p["issue"] == 8 and p["checks"] == "failure"
    assert p["failing_checks"] == ["test"] and p["unanswered_threads"] == 1
    assert p["last_review_at"] == "2026-09-14T11:00:00Z"
    assert snapshot["action"]["args"]["mode"] == "ci"
    assert snapshot["generated_at"] == "2026-09-14T12:00:00Z"
    assert snapshot["repo"] == "acme/widgets"


def test_main_never_raises(capsys):
    def boom(args):
        raise RuntimeError("gh exploded")
    code = state.main([], run=boom, now=NOW)
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["action"]["kind"] == "idle"
    assert "gh exploded" in out["error"]


def test_stale_worktrees_are_those_without_an_open_pr_or_working_issue(tmp_path):
    (tmp_path / "issue-7").mkdir()
    (tmp_path / "issue-8").mkdir()
    (tmp_path / "issue-9").mkdir()
    (tmp_path / "marketing-restyle").mkdir()
    s = snap(issues=[issue(number=9, labels=["agent-working"])], prs=[pr(issue=7)])
    assert state.list_stale_worktrees(s, tmp_path) == [tmp_path / "issue-8"]
