import json

from factory import notify, outbox, statefile

SLUGS = {"app": "rubinder/wiz_scheduler", "marketing": "rubinder/wiz_scheduler_marketing"}


def test_every_notification_is_queued_for_email(tmp_path):
    p = tmp_path / "s.json"
    notify.notify("Factory blocked", "needs answers", "blocked-app-78", run=lambda a: 0, state_path=p)
    notify.notify("Factory blocked", "needs answers", "blocked-app-78", run=lambda a: 0, state_path=p)  # deduped
    out = statefile.load(p)["outbox"]
    assert [(o["title"], o["message"], o["key"]) for o in out] == [("Factory blocked", "needs answers", "blocked-app-78")]


def test_outbox_is_capped(tmp_path):
    p = tmp_path / "s.json"
    for i in range(notify.OUTBOX_MAX + 5):
        notify.notify("t", str(i), None, run=lambda a: 0, state_path=p)
    out = statefile.load(p)["outbox"]
    assert len(out) == notify.OUTBOX_MAX and out[-1]["message"] == str(notify.OUTBOX_MAX + 4)


def test_link_from_key_message_or_slug():
    assert outbox.link({"key": "blocked-app-78"}, SLUGS) == "https://github.com/rubinder/wiz_scheduler/issues/78"
    assert outbox.link({"key": "owner-marketing-22-stuck-0a1b2c3d4e5f"}, SLUGS).endswith("wiz_scheduler_marketing/issues/22")
    assert outbox.link({"key": "regate-app-5-0a1b2c3d4e5f"}, SLUGS).endswith("/wiz_scheduler/issues/5")
    assert outbox.link({"key": None, "message": "app PR #12 merged"}, SLUGS).endswith("/wiz_scheduler/issues/12")
    assert outbox.link({"message": "rubinder/wiz_scheduler issue #9: odd"}, SLUGS).endswith("/wiz_scheduler/issues/9")


def test_no_link_for_dates_or_foreign_repos():
    assert outbox.link({"key": "main-red-app-2026-11-05", "message": "app main CI is red"}, SLUGS) is None
    assert outbox.link({"key": "guide-2026-11-05", "message": "no guide"}, SLUGS) is None
    assert outbox.link({"message": "evil/repo issue #1"}, SLUGS) is None


def test_render_says_replies_go_on_github():
    body = outbox.render([{"title": "Factory blocked", "message": "app issue #78: Payroll\nWhy: too big",
                           "key": "blocked-app-78-20261106100914"}], SLUGS)
    assert body.startswith("Factory blocked: app #78\n\napp issue #78: Payroll\nWhy: too big\n\n"
                           "Open on GitHub: https://github.com/rubinder/wiz_scheduler/issues/78\n")
    assert "GitHub mobile app" in body and "Replies to this email are not read" in body
    assert "the app works" not in body  # "the app" reads as WizScheduler in this email


def test_render_separates_alerts_and_omits_missing_links():
    body = outbox.render([{"title": "A", "message": "one", "key": "blocked-app-1"},
                          {"title": "No guide today", "message": "two", "key": "guide-2026-11-05"}], SLUGS)
    assert body.count(outbox.RULE) == 2  # between the alerts and before the footer
    assert "No guide today\n\ntwo" in body and body.count("Open on GitHub") == 1


def test_subject_names_the_issue_and_counts_extra_alerts():
    assert outbox.subject([{"title": "A"}]) == "WizScheduler factory: A"
    assert outbox.subject([{"title": "A"}, {"title": "B"}, {"title": "C"}]) == "WizScheduler factory: A (+2 more)"
    assert outbox.subject([{"title": "Factory blocked", "key": "blocked-app-78-20261106100914"}], SLUGS) \
        == "WizScheduler factory: Factory blocked: app #78"


def test_write_then_sent_drops_only_what_was_written(tmp_path, capsys, monkeypatch):
    p = tmp_path / "s.json"
    monkeypatch.setattr(outbox, "load_config", lambda: type("C", (), {"repos": {}})())
    notify.notify("one", "m1", None, run=lambda a: 0, state_path=p)
    assert outbox.main(["write"], state_path=p, home=tmp_path) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["subject"] == "WizScheduler factory: one" and "m1" in open(written["path"]).read()
    notify.notify("two", "m2", None, run=lambda a: 0, state_path=p)  # arrives after the write
    outbox.main(["sent"], state_path=p, home=tmp_path)
    assert [o["title"] for o in statefile.load(p)["outbox"]] == ["two"]


def test_write_with_nothing_queued_prints_no_path(tmp_path, capsys):
    assert outbox.main(["write"], state_path=tmp_path / "s.json", home=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"path": None}


def test_failed_send_keeps_alerts_queued(tmp_path, capsys, monkeypatch):
    p = tmp_path / "s.json"
    monkeypatch.setattr(outbox, "load_config", lambda: type("C", (), {"repos": {}})())
    notify.notify("one", "m1", None, run=lambda a: 0, state_path=p)
    outbox.main(["write"], state_path=p, home=tmp_path)  # email fails: no `sent`
    outbox.main(["write"], state_path=p, home=tmp_path)
    assert "m1" in open(json.loads(capsys.readouterr().out.splitlines()[-1])["path"]).read()
