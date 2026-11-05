import json

from factory import lighthouse


def test_median_of_three():
    runs = [{"performance": 0.80}, {"performance": 0.90}, {"performance": 0.85}]
    assert lighthouse.median(runs) == {"performance": 0.85}


def test_drop_of_more_than_two_points_fails():
    fails, _ = lighthouse.compare({"app:/login": {"performance": 87, "accessibility": 100}},
                                  {"app:/login": {"performance": 90, "accessibility": 100}})
    assert fails == ["app:/login performance 87 < baseline 90 - 2"]


def test_drop_within_two_points_passes_and_keeps_baseline():
    fails, new = lighthouse.compare({"app:/login": {"performance": 88}}, {"app:/login": {"performance": 90}})
    assert fails == [] and new["app:/login"]["performance"] == 90


def test_improvement_raises_baseline():
    _, new = lighthouse.compare({"app:/login": {"performance": 95}}, {"app:/login": {"performance": 90}})
    assert new["app:/login"]["performance"] == 95


def test_new_page_floors():
    fails, new = lighthouse.compare({"marketing:/guides/x": {"performance": 79, "accessibility": 95, "seo": 89}}, {})
    assert "marketing:/guides/x performance 79 < floor 80" in fails
    assert "marketing:/guides/x seo 89 < floor 90" in fails
    assert new == {}  # a failing new page sets no baseline


def test_crash_exits_2_and_leaves_baselines_alone(tmp_path, monkeypatch):
    path = tmp_path / "lighthouse.json"
    monkeypatch.setattr(lighthouse, "BASELINES", path)

    def boom(url):
        raise RuntimeError("chrome missing")

    monkeypatch.setattr(lighthouse, "run_once", boom)
    assert lighthouse.main(["--base", "http://x", "/login"]) == 2
    assert not path.exists()


def test_main_proposes_raises_and_never_writes_baselines(tmp_path, monkeypatch, capsys):
    path = tmp_path / "lighthouse.json"
    path.write_text(json.dumps({"app:/login": {"performance": 90, "seo": 99}}))
    proposed = tmp_path / "home" / "baselines-proposed.json"
    sent = []
    monkeypatch.setattr(lighthouse, "BASELINES", path)
    monkeypatch.setattr(lighthouse, "PROPOSED", proposed)
    monkeypatch.setattr(lighthouse, "_notify_owner", lambda raised: sent.append(raised))
    monkeypatch.setattr(lighthouse, "run_once", lambda url: {"performance": 95, "seo": 99})
    assert lighthouse.main(["--base", "http://x", "/login"]) == 0
    assert json.loads(path.read_text()) == {"app:/login": {"performance": 90, "seo": 99}}
    assert json.loads(proposed.read_text()) == {"app:/login": {"performance": 95}}
    assert sent == [{"app:/login": {"performance": 95}}]
    out = capsys.readouterr().out
    assert "commit" not in out and "factory/baselines" not in out


def test_baselines_path_is_module_relative():
    assert lighthouse.BASELINES.parts[-3:] == ("factory", "baselines", "lighthouse.json")


def test_atomic_write_survives_interruption(tmp_path, monkeypatch):
    path = tmp_path / "lighthouse.json"
    path.write_text(json.dumps({"a": {"seo": 90}}))

    def interrupted(src, dst):
        raise OSError("interrupted")

    monkeypatch.setattr(lighthouse.os, "replace", interrupted)
    try:
        lighthouse.write_atomic(path, {"a": {"seo": 99}})
    except OSError:
        pass
    assert json.loads(path.read_text()) == {"a": {"seo": 90}}
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_proposals_merge_with_earlier_ones(tmp_path, monkeypatch):
    path = tmp_path / "lighthouse.json"
    path.write_text(json.dumps({"app:/other": {"performance": 91}}))
    proposed = tmp_path / "baselines-proposed.json"
    proposed.write_text(json.dumps({"app:/other": {"performance": 97}}))
    monkeypatch.setattr(lighthouse, "BASELINES", path)
    monkeypatch.setattr(lighthouse, "PROPOSED", proposed)
    fails, raised = lighthouse.propose_baselines({"app:/login": {"performance": 95}, "app:/other": {"performance": 93}})
    assert fails == [] and raised == {"app:/login": {"performance": 95}, "app:/other": {"performance": 93}}
    assert json.loads(proposed.read_text()) == {"app:/other": {"performance": 97}, "app:/login": {"performance": 95}}
    assert json.loads(path.read_text()) == {"app:/other": {"performance": 91}}


def test_regression_proposes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "lighthouse.json"
    path.write_text(json.dumps({"app:/login": {"performance": 90}}))
    monkeypatch.setattr(lighthouse, "BASELINES", path)
    monkeypatch.setattr(lighthouse, "PROPOSED", tmp_path / "p.json")
    fails, raised = lighthouse.propose_baselines({"app:/login": {"performance": 80}, "app:/x": {"performance": 99}})
    assert fails and raised == {} and not (tmp_path / "p.json").exists()


def test_missing_binary_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lighthouse, "BASELINES", tmp_path / "lighthouse.json")
    monkeypatch.setattr(lighthouse, "LIGHTHOUSE_BIN", tmp_path / "nope")
    assert lighthouse.main(["--base", "http://x", "/login"]) == 2
    assert "npm ci in frontend" in capsys.readouterr().err


def test_binary_is_pinned_local_install():
    assert lighthouse.LIGHTHOUSE_BIN.parts[-4:] == ("frontend", "node_modules", ".bin", "lighthouse")


def test_failure_includes_stderr_tail_and_timeout(tmp_path, monkeypatch):
    import subprocess
    exe = tmp_path / "lighthouse"
    exe.write_text("")
    monkeypatch.setattr(lighthouse, "LIGHTHOUSE_BIN", exe)

    def fail(*a, **k):
        raise subprocess.CalledProcessError(1, a[0], stderr="x" * 900 + "CHROME DEAD")

    monkeypatch.setattr(lighthouse.subprocess, "run", fail)
    try:
        lighthouse.run_once("http://x/")
    except lighthouse.GateError as e:
        assert str(e).endswith("CHROME DEAD") and len(str(e)) < 600
    else:
        raise AssertionError

    seen = {}

    def ok(*a, **k):
        seen.update(k)
        return subprocess.CompletedProcess(a[0], 0, stdout=json.dumps(
            {"categories": {c: {"score": 1} for c in lighthouse.CATEGORIES}}), stderr="")

    monkeypatch.setattr(lighthouse.subprocess, "run", ok)
    lighthouse.run_once("http://x/")
    assert seen["timeout"] == 300
