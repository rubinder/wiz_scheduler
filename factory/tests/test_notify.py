from factory import notify, statefile


def test_notify_passes_text_as_argv_not_script(tmp_path):
    calls = []
    assert notify.notify('A "quoted" title', "msg; rm -rf ~", "k1", run=lambda a: calls.append(a) or 0,
                         state_path=tmp_path / "s.json")
    argv = calls[0]
    assert argv[0] == "osascript"
    assert argv[-2:] == ['A "quoted" title', "msg; rm -rf ~"]
    assert not any("rm -rf" in a for a in argv[:-2])


def test_notify_dedupes_by_key(tmp_path):
    calls = []
    run = lambda a: calls.append(a) or 0
    p = tmp_path / "s.json"
    assert notify.notify("t", "m", "same", run=run, state_path=p)
    assert not notify.notify("t", "m", "same", run=run, state_path=p)
    assert len(calls) == 1
    assert "same" in statefile.load(p)["notified"]


def test_notify_without_key_always_sends(tmp_path):
    calls = []
    run = lambda a: calls.append(a) or 0
    notify.notify("t", "m", None, run=run, state_path=tmp_path / "s.json")
    notify.notify("t", "m", None, run=run, state_path=tmp_path / "s.json")
    assert len(calls) == 2
