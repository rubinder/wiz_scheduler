from datetime import datetime, timezone
from pathlib import Path

from factory import config, state, statefile

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CFG = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
SHA = "a" * 40
PASS = f"<!-- factory-gates: pass sha={SHA} -->"


def pr(**kw):
    base = dict(number=50, branch="agent/issue-7-x", issue=7, checks="success", review_decision="",
                unanswered_threads=0, labels=["agent-pr-open"], last_agent_push_at="2026-11-04T10:00:00Z",
                last_review_at=None, last_commit_at="2026-11-04T10:00:00Z", failing_checks=[],
                last_ci_comment_checks=[], files=["backend/routers/employees.py"], gate_report="pass",
                is_draft=False, is_revert=False, title="t", head_sha=SHA)
    base.update(kw)
    return base


def issue(**kw):
    base = dict(number=7, title="t", labels=["agent-ready"], updated_at="2026-11-01T00:00:00Z",
                last_commit_at=None, created_at="2026-11-01T00:00:00Z")
    base.update(kw)
    return base


def snap(app_issues=(), app_prs=(), mk_issues=(), mk_prs=(), **kw):
    s = {"generated_at": "2026-11-04T12:00:00Z",
         "repos": {"app": {"issues": list(app_issues), "prs": list(app_prs), "main_red": False},
                   "marketing": {"issues": list(mk_issues), "prs": list(mk_prs), "main_red": False}},
         "misses": [], "guides_enabled": False, "gum_ready": False, "unfiled_findings": 0}
    s.update(kw)
    return s


def local(**kw):
    s = statefile.defaults()
    s.update(kw)
    return s


def gated(repo="app", number=50, sha=SHA, **kw):
    """Local state holding the factory's own gate-pass record for one PR head."""
    return local(gate_passes=[{"repo": repo, "pr": number, "sha": sha}], **kw)


def act(s, l=None):
    # Default: the factory has recorded a gate pass for PR 50 at SHA, so every
    # "not merged" test below fails for its own reason, not for a missing record.
    return state.choose_action(s, l or gated(), NOW, CFG)


def test_gate_report_parsing():
    assert state.gate_report(f"x\n{PASS}\n", SHA) == "pass"
    assert state.gate_report(f"<!-- factory-gates: fail sha={SHA} -->", SHA) == "fail"
    assert state.gate_report("no marker", SHA) == "none"
    assert state.gate_report(PASS, "b" * 40) == "stale"
    assert state.gate_report("<!-- factory-gates: pass -->", SHA) == "stale"
    assert state.gate_report(PASS, None) == "stale"


def test_idle_when_nothing():
    a = act(snap())
    assert a["kind"] == "idle" and a["reason"]


def test_red_ci_is_fixed_first_in_either_repo():
    a = act(snap(app_issues=[issue()], mk_prs=[pr(number=9, checks="failure", failing_checks=["check"])]))
    assert (a["kind"], a["repo"], a["workflow"], a["args"]["pr"], a["args"]["mode"]) == ("fix", "marketing", "pr-tend", 9, "ci")


def test_green_unprotected_pr_with_passing_gates_is_merged():
    a = act(snap(app_prs=[pr(head_sha=SHA)]), gated())
    assert (a["kind"], a["workflow"], a["args"]["pr"]) == ("merge", "factory-merge", 50)


def test_body_marker_without_local_gate_pass_is_not_merged():
    # The PR body is self-asserted: only the factory's own record lets it merge.
    assert act(snap(app_prs=[pr(head_sha=SHA)]), local())["kind"] == "idle"
    assert act(snap(app_prs=[pr(head_sha=SHA)]), gated(sha="b" * 40))["kind"] == "idle"
    assert act(snap(app_prs=[pr(head_sha=SHA)]), gated(repo="marketing"))["kind"] == "idle"
    assert act(snap(app_prs=[pr(head_sha=SHA)]), gated(number=51))["kind"] == "idle"


def test_mixed_protected_pr_waits_for_owner():
    a = act(snap(app_prs=[pr(files=["backend/routers/employees.py", "backend/alembic/versions/0060.py"])]))
    assert a["kind"] == "idle"


def test_green_pr_without_gate_report_is_not_merged():
    assert act(snap(app_prs=[pr(gate_report="none")]))["kind"] == "idle"
    assert act(snap(app_prs=[pr(gate_report="fail")]))["kind"] == "idle"


def test_pending_checks_draft_or_blocked_pr_is_not_merged():
    assert act(snap(app_prs=[pr(checks="pending")]))["kind"] == "idle"
    assert act(snap(app_prs=[pr(is_draft=True)]))["kind"] == "idle"
    assert act(snap(app_prs=[pr(labels=["agent-blocked"])]))["kind"] == "idle"


def test_changes_requested_pr_is_not_merged():
    a = act(snap(app_prs=[pr(review_decision="CHANGES_REQUESTED", last_review_at="2026-11-04T09:00:00Z")]))
    assert a["kind"] != "merge"


def test_revert_pr_is_never_auto_reverted():
    a = act(snap(app_prs=[pr(is_revert=True, branch="factory/revert-240", head_sha=SHA)]), gated())
    assert a["kind"] == "merge" and a["args"]["is_revert"] is True


def test_pending_verify_beats_build():
    l = local(pending_verify=[{"repo": "app", "pr": 240, "sha": "abc", "result": None}])
    a = act(snap(app_issues=[issue()]), l)
    assert (a["kind"], a["workflow"], a["args"]["pr"]) == ("verify", "factory-verify", 240)


def test_build_picks_oldest_ready_issue_across_repos():
    a = act(snap(app_issues=[issue(number=300, created_at="2026-11-03T00:00:00Z")],
                 mk_issues=[issue(number=20, created_at="2026-11-02T00:00:00Z")]))
    assert (a["kind"], a["repo"], a["workflow"], a["args"]["issue"]) == ("build", "marketing", "issue-pipeline", 20)


def test_one_build_in_flight_per_repo():
    a = act(snap(app_issues=[issue(number=5, labels=["agent-working"], last_commit_at="2026-11-04T11:30:00Z"),
                             issue(number=6)]))
    assert a["kind"] == "idle"


def test_checkpointed_build_resumes_next_tick():
    l = local(checkpointed=[{"repo": "app", "issue": 5}])
    a = act(snap(app_issues=[issue(number=5, labels=["agent-working"], last_commit_at="2026-11-04T11:50:00Z")]), l)
    assert (a["kind"], a["args"]["issue"]) == ("build", 5)


def test_stale_working_issue_resumes():
    a = act(snap(app_issues=[issue(number=5, labels=["agent-working"], last_commit_at="2026-11-04T07:00:00Z")]))
    assert (a["kind"], a["args"]["issue"]) == ("build", 5)


def test_pr_awaiting_owner_does_not_block_next_issue():
    a = act(snap(app_prs=[pr(files=["backend/alembic/versions/1.py"])], app_issues=[issue(number=8)]))
    assert (a["kind"], a["args"]["issue"]) == ("build", 8)


def test_guide_runs_when_enabled_and_not_done_today():
    s = snap(guides_enabled=True, gum_ready=True)
    assert act(s)["kind"] == "guide"
    assert act(s, local(guide={"date": "2026-11-04", "status": "merged"}))["kind"] == "idle"
    assert act(s, local(guide={"date": "2026-11-04", "status": "blocked"}))["kind"] == "idle"
    assert act(s, local(guide={"date": "2026-11-03", "status": "merged"}))["kind"] == "guide"


def test_guide_skipped_without_gum():
    assert act(snap(guides_enabled=True, gum_ready=False))["kind"] == "idle"


def test_guide_in_flight_pr_blocks_a_second_guide():
    s = snap(guides_enabled=True, gum_ready=True,
             mk_prs=[pr(branch="content/guide-2026-11-04-x", checks="pending")])
    assert act(s)["kind"] == "idle"


def test_retro_for_unhandled_miss():
    s = snap(misses=[{"repo": "app", "number": 240, "kind": "revert", "title": "x"}])
    assert (act(s)["kind"], act(s)["args"]["number"]) == ("retro", 240)
    assert act(s, local(retros_done=["app#240"]))["kind"] == "idle"


def test_propose_capped_at_three_per_day():
    s = snap(unfiled_findings=2)
    assert act(s)["kind"] == "propose"
    assert act(s, local(proposals={"date": "2026-11-04", "count": 3}))["kind"] == "idle"
    assert act(s, local(proposals={"date": "2026-11-03", "count": 3}))["kind"] == "propose"


def test_pr_with_no_file_list_is_not_merged():
    assert act(snap(app_prs=[pr(files=[])]))["kind"] == "idle"
    assert act(snap(app_prs=[pr(files=None)]))["kind"] == "idle"


def test_stale_gate_report_is_not_merged():
    assert act(snap(app_prs=[pr(gate_report="stale")]))["kind"] == "idle"


def test_merge_action_carries_head_sha():
    a = act(snap(app_prs=[pr(head_sha=SHA)]), gated())
    assert a["kind"] == "merge" and a["args"]["head_sha"] == SHA


def test_merge_action_carries_branch():
    a = act(snap(app_prs=[pr(head_sha=SHA)]), gated())
    assert a["args"]["branch"] == "agent/issue-7-x"


GUIDE = "content/guide-2026-11-04-shift-swaps"


def test_clean_guide_pr_is_merged():
    p = pr(branch=GUIDE, files=["src/content/guides/en/shift-swaps.md", "public/guides/shift-swaps/hero.png"])
    a = act(snap(mk_prs=[p]), gated(repo="marketing"))
    assert (a["kind"], a["repo"], a["args"]["pr"], a["args"]["branch"]) == ("merge", "marketing", 50, GUIDE)


def test_guide_pr_touching_a_page_is_not_merged():
    p = pr(branch=GUIDE, files=["src/content/guides/en/shift-swaps.md", "src/views/Home.astro"])
    assert act(snap(mk_prs=[p]), gated(repo="marketing"))["kind"] == "idle"


def test_guide_pr_renaming_from_outside_guides_is_not_merged():
    # _pr_files lists previous_filename next to filename.
    p = pr(branch=GUIDE, files=["src/content/guides/home.astro", "src/views/Home.astro"])
    assert act(snap(mk_prs=[p]), gated(repo="marketing"))["kind"] == "idle"


def test_guide_pr_in_the_app_repo_is_not_merged():
    p = pr(branch=GUIDE, files=["src/content/guides/en/shift-swaps.md"])
    assert act(snap(app_prs=[p]), gated())["kind"] == "idle"


def test_boundary_blocked_guide_pr_awaits_owner():
    stray = pr(number=60, branch=GUIDE, files=["src/content/guides/en/a.md", "src/views/Home.astro"])
    clean = pr(number=61, branch=GUIDE, files=["src/content/guides/en/b.md"])
    protected = pr(number=62, files=["src/content/legal/terms.md"])
    rows = state.awaiting_owner(snap(mk_prs=[stray, clean, protected], app_prs=[pr(number=63, branch=GUIDE)]), CFG)
    assert rows == [
        {"repo": "app", "pr": 63, "title": "t", "protected": ["backend/routers/employees.py"], "reason": "guide outside boundary"},
        {"repo": "marketing", "pr": 60, "title": "t", "protected": ["src/views/Home.astro"], "reason": "guide outside boundary"},
        {"repo": "marketing", "pr": 62, "title": "t", "protected": ["src/content/legal/terms.md"], "reason": "touches a protected path"},
    ]
    # Not green or not gated: not ready for the owner yet.
    assert state.awaiting_owner(snap(mk_prs=[{**stray, "checks": "pending"}]), CFG) == []
