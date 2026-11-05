import json
from pathlib import Path

from factory import config, protected_check, state
from factory.tests.test_collect import fake_gh

CFG = config.load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
BRANCH = "agent/issue-3-x"


def gh_for(pr: int, file_json: str, branch: str = BRANCH):
    return fake_gh({"headRefName": branch, f"pulls/{pr}/files": file_json})


def files(*pairs):
    return json.dumps([{"filename": f, **({"previous_filename": prev} if prev else {})} for f, prev in pairs])


def test_unprotected_files_pass():
    run = gh_for(12, files(("backend/routers/employees.py", None)))
    assert protected_check.main(["app", "12", BRANCH], run=run, cfg=CFG) == 0
    assert run.calls[0] == ["pr", "view", "12", "--repo", "rubinder/wiz_scheduler", "--json", "headRefName", "--jq", ".headRefName"]
    assert run.calls[1] == ["api", "repos/rubinder/wiz_scheduler/pulls/12/files", "--paginate"]


def test_protected_file_fails():
    run = gh_for(12, files(("backend/routers/employees.py", None), ("backend/alembic/versions/1.py", None)))
    assert protected_check.main(["app", "12", BRANCH], run=run, cfg=CFG) == 1


def test_rename_out_of_protected_path_fails():
    run = gh_for(12, files(("backend/routers/x.py", "backend/routers/auth.py")))
    assert protected_check.main(["app", "12", BRANCH], run=run, cfg=CFG) == 1


def test_second_page_is_read():
    out = files(("README.md", None)) + "\n" + files(("factory/state.py", None))
    assert protected_check.main(["app", "12", BRANCH], run=gh_for(12, out), cfg=CFG) == 1


def test_marketing_legal_is_protected():
    run = gh_for(4, files(("src/content/legal/terms.md", None)))
    assert protected_check.main(["marketing", "4", BRANCH], run=run, cfg=CFG) == 1


def test_empty_truncated_or_erroring_list_is_unknown():
    assert protected_check.main(["app", "12", BRANCH], run=fake_gh({"headRefName": BRANCH}), cfg=CFG) == 2
    many = files(*[(f"docs/{i}.md", None) for i in range(state.MAX_FILES)])
    assert protected_check.main(["app", "12", BRANCH], run=gh_for(12, many), cfg=CFG) == 2

    def boom(argv):
        raise RuntimeError("gh down")
    assert protected_check.main(["app", "12", BRANCH], run=boom, cfg=CFG) == 2


def test_bad_arguments_are_rejected():
    assert protected_check.main(["app"], cfg=CFG) == 2
    assert protected_check.main(["app", "12"], cfg=CFG) == 2
    assert protected_check.main(["other", "12", BRANCH], cfg=CFG) == 2
    assert protected_check.main(["app", "12; rm", BRANCH], cfg=CFG) == 2
    assert protected_check.main(["app", "12", "agent/x; rm"], cfg=CFG) == 2
    assert protected_check.main(["app", "12", "agent/../x"], cfg=CFG) == 2


def test_head_branch_other_than_the_given_one_is_unknown():
    run = gh_for(12, files(("README.md", None)), branch="agent/issue-4-y")
    assert protected_check.main(["app", "12", BRANCH], run=run, cfg=CFG) == 2


GUIDE = "content/guide-2026-11-04-shift-swaps"


def test_clean_guide_pr_passes():
    import dataclasses
    cfg = dataclasses.replace(CFG, guide_locales=("en", "es"))
    run = gh_for(4, files(("src/content/guides/en/shift-swaps.md", None), ("public/guides/shift-swaps/x.png", None)), GUIDE)
    assert protected_check.main(["marketing", "4", GUIDE], run=run, cfg=cfg) == 0


def test_guide_pr_in_a_locale_the_site_lacks_fails():
    import dataclasses
    run = gh_for(4, files(("src/content/guides/xx/shift-swaps.md", None),), GUIDE)
    assert protected_check.main(["marketing", "4", GUIDE], run=run, cfg=dataclasses.replace(CFG, guide_locales=("en",))) == 1
    # LOCALES unreadable: nothing is inside the boundary.
    run = gh_for(4, files(("src/content/guides/en/shift-swaps.md", None),), GUIDE)
    assert protected_check.main(["marketing", "4", GUIDE], run=run, cfg=dataclasses.replace(CFG, guide_locales=())) == 1


def test_guide_pr_touching_a_page_fails():
    run = gh_for(4, files(("src/content/guides/a.md", None), ("src/views/Home.astro", None)), GUIDE)
    assert protected_check.main(["marketing", "4", GUIDE], run=run, cfg=CFG) == 1


def test_guide_pr_renaming_from_outside_guides_fails():
    run = gh_for(4, files(("src/content/guides/a.astro", "src/views/Home.astro")), GUIDE)
    assert protected_check.main(["marketing", "4", GUIDE], run=run, cfg=CFG) == 1


def test_guide_pr_in_the_app_repo_fails():
    run = gh_for(12, files(("src/content/guides/a.md", None)), GUIDE)
    assert protected_check.main(["app", "12", GUIDE], run=run, cfg=CFG) == 1
