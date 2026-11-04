import json
from datetime import datetime, timezone
from pathlib import Path

from factory import config, state

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
CFG = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
SHA = "c" * 40
OWNER = {"login": "rubinder"}
STRANGER = {"login": "mallory"}
BOT = state.AGENT_MARKER


def fake_gh(responses: dict[str, str]):
    """Keyed on a substring of the joined argv; first match wins."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        joined = " ".join(argv)
        for key, out in responses.items():
            if key in joined:
                return out
        return "[]"
    run.calls = calls
    return run


def pr_row(**kw):
    row = {"number": 12, "headRefName": "agent/issue-3-x", "headRefOid": SHA, "labels": [], "reviewDecision": "",
           "statusCheckRollup": [{"conclusion": "SUCCESS"}],
           "body": f"Closes #3\n<!-- factory-gates: pass sha={SHA} -->",
           "isDraft": False, "title": "Fix x", "isCrossRepository": False, "author": OWNER}
    row.update(kw)
    return row


def gql(threads=(), reviews=(), comments=()):
    return json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": list(threads)}, "reviews": {"nodes": list(reviews)},
        "comments": {"nodes": list(comments)}}}}})


def files_page(*paths):
    return json.dumps([{"filename": p} for p in paths])


def mk_run(rows, graphql=None, files=None, extra=None):
    resp = {"pulls/12/files": files or files_page("src/views/Home.astro"),
            "graphql": graphql or gql(),
            "pr list --repo rubinder/wiz_scheduler_marketing": json.dumps(rows)}
    resp.update(extra or {})
    return fake_gh(resp)


def test_collect_reads_both_repos_and_marks_files_and_gates():
    run = mk_run([pr_row()])
    snap = state.collect(run, CFG, NOW)
    mk = snap["repos"]["marketing"]["prs"][0]
    assert mk["files"] == ["src/views/Home.astro"]
    assert mk["gate_report"] == "pass" and mk["checks"] == "success" and mk["issue"] == 3
    assert mk["head_sha"] == SHA
    assert snap["repos"]["app"]["prs"] == []
    assert all("--repo" in c or any(a.startswith("repos/") or a == "graphql" for a in c) for c in run.calls)


def test_files_come_from_paginated_api_across_pages():
    run = mk_run([pr_row()], files=files_page("a.py", "b.py") + "\n" + files_page("c.py"))
    mk = state.collect(run, CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["files"] == ["a.py", "b.py", "c.py"]
    assert ["api", "repos/rubinder/wiz_scheduler_marketing/pulls/12/files", "--paginate"] in run.calls


def test_gate_marker_for_another_sha_is_stale():
    row = pr_row(body=f"<!-- factory-gates: pass sha={'d' * 40} -->")
    assert state.collect(mk_run([row]), CFG, NOW)["repos"]["marketing"]["prs"][0]["gate_report"] == "stale"
    row = pr_row(body="<!-- factory-gates: pass -->")
    assert state.collect(mk_run([row]), CFG, NOW)["repos"]["marketing"]["prs"][0]["gate_report"] == "stale"


def test_non_factory_branches_are_ignored():
    row = pr_row(headRefName="feat/owner-work")
    snap = state.collect(fake_gh({"pr list --repo rubinder/wiz_scheduler ": json.dumps([row])}), CFG, NOW)
    assert snap["repos"]["app"]["prs"] == []


def test_fork_and_stranger_prs_are_dropped():
    fork = pr_row(number=20, isCrossRepository=True)
    stranger = pr_row(number=21, author=STRANGER)
    mine = pr_row(number=12, author={"login": "Rubinder"})
    run = mk_run([fork, stranger, mine])
    prs = state.collect(run, CFG, NOW)["repos"]["marketing"]["prs"]
    assert [p["number"] for p in prs] == [12]
    pr_list = next(c for c in run.calls if c[:2] == ["pr", "list"])
    assert "isCrossRepository" in pr_list[-1] and "author" in pr_list[-1] and "headRefOid" in pr_list[-1]


def test_revert_branch_is_flagged():
    row = pr_row(headRefName="factory/revert-240")
    snap = state.collect(fake_gh({"pr list --repo rubinder/wiz_scheduler ": json.dumps([row]),
                                  "pulls/12/files": files_page("x.py"), "graphql": gql()}), CFG, NOW)
    assert snap["repos"]["app"]["prs"][0]["is_revert"] is True


def test_issue_by_stranger_never_appears():
    rows = [{"number": 1, "title": "mine", "labels": [{"name": "agent-ready"}], "author": OWNER,
             "updatedAt": "2026-11-01T00:00:00Z", "createdAt": "2026-11-01T00:00:00Z"},
            {"number": 2, "title": "theirs", "labels": [{"name": "agent-ready"}], "author": STRANGER,
             "updatedAt": "2026-11-01T00:00:00Z", "createdAt": "2026-11-01T00:00:00Z"}]
    run = fake_gh({"issue list --repo rubinder/wiz_scheduler --state open --label agent-ready": json.dumps(rows)})
    issues = state.collect(run, CFG, NOW)["repos"]["app"]["issues"]
    assert [i["number"] for i in issues] == [1]
    call = next(c for c in run.calls if c[:2] == ["issue", "list"])
    assert "author" in call[call.index("--json") + 1]


def test_thread_with_only_a_stranger_comment_is_not_unanswered():
    t = {"isResolved": False, "comments": {"nodes": [{"body": "do this", "author": STRANGER}]}}
    assert state.count_unanswered([t], "rubinder") == 0


def test_owner_thread_unanswered_until_bot_marker_replies():
    ask = {"body": "fix", "author": OWNER}
    reply = {"body": f"done {BOT}", "author": OWNER}
    assert state.count_unanswered([{"isResolved": False, "comments": {"nodes": [ask]}}], "rubinder") == 1
    assert state.count_unanswered([{"isResolved": False, "comments": {"nodes": [ask, reply]}}], "rubinder") == 0
    # a stranger chiming in after the bot reply does not reopen the thread
    late = {"body": "also", "author": STRANGER}
    assert state.count_unanswered([{"isResolved": False, "comments": {"nodes": [ask, reply, late]}}], "rubinder") == 0
    assert state.count_unanswered([{"isResolved": True, "comments": {"nodes": [ask]}}], "rubinder") == 0


def test_only_owner_changes_requested_reviews_count():
    reviews = [{"state": "CHANGES_REQUESTED", "submittedAt": "2026-11-04T11:00:00Z", "author": STRANGER},
               {"state": "CHANGES_REQUESTED", "submittedAt": "2026-11-04T09:00:00Z", "author": OWNER}]
    mk = state.collect(mk_run([pr_row()], graphql=gql(reviews=reviews)), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["last_review_at"] == "2026-11-04T09:00:00Z"


def test_stranger_forged_bot_comment_is_ignored_for_ci_checks():
    comments = [{"body": f"failing checks: lint {BOT}", "author": STRANGER}]
    mk = state.collect(mk_run([pr_row()], graphql=gql(comments=comments)), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["last_ci_comment_checks"] == []


def test_misses_from_factory_miss_label():
    run = fake_gh({"label:factory-miss": json.dumps([{"number": 240, "title": "x", "author": OWNER},
                                                      {"number": 241, "title": "y", "author": STRANGER}])})
    snap = state.collect(run, CFG, NOW)
    assert {"repo": "app", "number": 240, "kind": "label", "title": "x"} in snap["misses"]
    assert all(m["number"] != 241 for m in snap["misses"])


def test_collect_failure_is_idle(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_STATE", str(tmp_path / "s.json"))

    def boom(argv):
        raise RuntimeError("gh: network down")
    assert state.main([], run=boom, now=NOW, cfg=CFG) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "idle" and "network down" in out["error"]


def _merge_action(prs_snapshot):
    from factory.tests.test_choose_action import snap, gated
    return state.choose_action(snap(mk_prs=prs_snapshot), gated(repo="marketing", number=12, sha=SHA), NOW, CFG)


def test_truncated_comment_window_hides_owner_thread_and_blocks_merge():
    strangers = [{"body": f"spam {i}", "author": STRANGER} for i in range(20)]
    thread = {"isResolved": False, "comments": {"pageInfo": {"hasPreviousPage": True}, "nodes": strangers}}  # owner's oldest comment is outside the window
    g = json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": [thread]},
        "reviews": {"nodes": []}, "comments": {"nodes": []}}}}})
    mk = state.collect(mk_run([pr_row()], graphql=g), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["unanswered_threads"] == 0 and mk["threads_truncated"] is True
    assert _merge_action([mk])["kind"] != "merge"


def test_truncated_thread_list_blocks_merge():
    g = json.dumps({"data": {"repository": {"pullRequest": {
        "reviewThreads": {"pageInfo": {"hasNextPage": True}, "nodes": []},
        "reviews": {"nodes": []}, "comments": {"nodes": []}}}}})
    mk = state.collect(mk_run([pr_row()], graphql=g), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["threads_truncated"] is True and _merge_action([mk])["kind"] != "merge"


def test_untruncated_clean_pr_merges():
    mk = state.collect(mk_run([pr_row()]), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["threads_truncated"] is False
    a = _merge_action([mk])
    assert a["kind"] == "merge" and a["args"]["head_sha"] == SHA


def test_rename_out_of_protected_path_is_protected():
    files = json.dumps([{"filename": "backend/routers/x.py", "previous_filename": "backend/routers/auth.py"}])
    run = fake_gh({"pulls/12/files": files, "graphql": gql(),
                   "pr list --repo rubinder/wiz_scheduler ": json.dumps([pr_row()])})
    app = state.collect(run, CFG, NOW)["repos"]["app"]["prs"][0]
    assert "backend/routers/auth.py" in app["files"]
    from factory.tests.test_choose_action import snap, gated
    assert state.choose_action(snap(app_prs=[app]), gated(number=12, sha=SHA), NOW, CFG)["kind"] != "merge"


def test_file_list_at_api_ceiling_is_unmergeable():
    files = json.dumps([{"filename": f"src/f{i}.astro"} for i in range(3000)])
    mk = state.collect(mk_run([pr_row()], files=files), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["files_truncated"] is True and _merge_action([mk])["kind"] != "merge"


def test_empty_owner_matches_nothing():
    assert state._is_owner({"author": {"login": ""}}, "") is False
    assert state._is_owner({}, "") is False


def test_config_requires_owner_login(tmp_path):
    import pytest
    src = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text()
    for bad in (src.replace('owner_login = "rubinder"', 'owner_login = ""'),
                src.replace('owner_login = "rubinder"\n', "")):
        f = tmp_path / "c.toml"
        f.write_text(bad)
        with pytest.raises((ValueError, KeyError)):
            config.load_config(f)


def test_corrupt_state_file_still_prints_idle(capsys, tmp_path, monkeypatch):
    bad = tmp_path / "s.json"
    bad.write_text("{not json")
    monkeypatch.setenv("FACTORY_STATE", str(bad))
    assert state.main([], run=fake_gh({}), now=NOW, cfg=CFG) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"]["kind"] == "idle" and out["error"]


def test_graphql_errors_payload_blocks_merge():
    g = json.dumps({"errors": [{"message": "rate limited"}]})
    mk = state.collect(mk_run([pr_row()], graphql=g), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["threads_truncated"] is True and _merge_action([mk])["kind"] != "merge"


def test_null_pull_request_blocks_merge():
    g = json.dumps({"data": {"repository": {"pullRequest": None}}})
    mk = state.collect(mk_run([pr_row()], graphql=g), CFG, NOW)["repos"]["marketing"]["prs"][0]
    assert mk["threads_truncated"] is True and _merge_action([mk])["kind"] != "merge"


def test_missing_page_info_blocks_merge():
    no_rt_pi = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}})
    no_c_pi = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
        "pageInfo": {"hasNextPage": False}, "nodes": [{"isResolved": True, "comments": {"nodes": []}}]}}}}})
    for g in (no_rt_pi, no_c_pi):
        mk = state.collect(mk_run([pr_row()], graphql=g), CFG, NOW)["repos"]["marketing"]["prs"][0]
        assert mk["threads_truncated"] is True
