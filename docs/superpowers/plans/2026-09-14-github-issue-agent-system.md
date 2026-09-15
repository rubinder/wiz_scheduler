# GitHub Issue Agent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/work-issues` driver skill, two saved Workflow scripts, and six custom agents that work `agent-ready` GitHub issues into reviewed pull requests and keep those PRs healthy, with all state on GitHub.

**Architecture:** The skill injects a JSON snapshot from `state.py` (pure decision function + `gh` collectors), runs exactly one workflow per tick, and reports. `issue-pipeline.js` is triage → spec → plan → serial implement-with-review → branch review → ship. `pr-tend.js` is read → fix → verify → push/reply. Agents are narrow-tool markdown definitions that inherit CLAUDE.md.

**Tech Stack:** Claude Code 2.1.x custom agents, skills, Workflow scripts (plain JS); Python 3.11 stdlib for `state.py`; `gh` CLI; pytest.

**Spec:** `docs/superpowers/specs/2026-09-14-github-issue-agent-system-design.md`

## Global Constraints

- Agents never push to `main`, never `--force`, never `gh pr merge`, never edit `terraform/**` or `.github/**`.
- Only issues labeled `agent-ready` are worked. Labels: `agent-ready`, `agent-working`, `agent-pr-open`, `agent-blocked`.
- Branch naming `agent/issue-<n>-<slug>`; worktree `.claude/worktrees/issue-<n>`.
- Every agent-posted comment/PR body ends with `<!-- wizbot -->`.
- `state.py` is stdlib-only, always exits 0, always prints JSON.
- Stale-run threshold: 3 hours without a commit on the branch.
- Caps: 3 fix rounds per task, 12 tasks per plan, 1 fix round for branch review, 1 for `pr-tend`.
- "Today" is UTC (`datetime.now(timezone.utc)`), enforced by `tests/test_utc_today.py`.

---

### Task 1: `choose_action` decision function (TDD)

**Files:**
- Create: `.claude/skills/work-issues/state.py`
- Test: `tests/test_agent_state.py`

**Interfaces:**
- Produces: `choose_action(snapshot: dict, now: datetime) -> dict` with keys `kind` (`pr-tend` | `issue-pipeline` | `idle`), `workflow`, `args`, `reason`. Constants `AGENT_MARKER = "<!-- wizbot -->"`, `STALE_HOURS = 3`, `BRANCH_PREFIX = "agent/"`.
- Snapshot input shape is the spec's: `issues[]` with `number, labels, updated_at`; `prs[]` with `number, branch, issue, checks, review_decision, unanswered_threads, labels, last_agent_push_at, last_review_at, last_commit_at`.

- [ ] Step 1: Write failing tests in `tests/test_agent_state.py` loading the module by path:

```python
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / ".claude/skills/work-issues/state.py"
spec = importlib.util.spec_from_file_location("agent_state", STATE)
state = importlib.util.module_from_spec(spec); spec.loader.exec_module(state)
NOW = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)

def pr(**kw):
    base = dict(number=50, branch="agent/issue-7-x", issue=7, checks="success",
                review_decision="", unanswered_threads=0, labels=["agent-pr-open"],
                last_agent_push_at="2026-09-14T10:00:00Z", last_review_at=None,
                last_commit_at="2026-09-14T10:00:00Z")
    base.update(kw); return base

def issue(**kw):
    base = dict(number=7, labels=["agent-ready"], updated_at="2026-09-01T00:00:00Z")
    base.update(kw); return base
```

Cases: failing CI wins over everything; `agent-blocked` PR is skipped; unanswered threads → review mode; CHANGES_REQUESTED with review after last push → review mode, before last push → not; oldest `agent-ready` issue chosen; issues with `agent-working`/`agent-blocked`/`agent-pr-open` skipped; stale `agent-working` (last commit > 3h) resumes; fresh `agent-working` skipped; empty → idle with reason.

- [ ] Step 2: Run `backend/.venv/bin/python -m pytest tests/test_agent_state.py -q` → fails (file missing).
- [ ] Step 3: Implement `choose_action` and helpers `_parse_ts`, `_is_stale` in `state.py`.
- [ ] Step 4: Tests pass. Also run `tests/test_utc_today.py`.
- [ ] Step 5: Commit `feat(agents): decision function for the issue driver`.

### Task 2: `state.py` collectors and CLI

**Files:**
- Modify: `.claude/skills/work-issues/state.py`
- Test: `tests/test_agent_state.py` (add)

**Interfaces:**
- `collect(run) -> dict` where `run(args: list[str]) -> str` executes `gh` and returns stdout. Default `run` is `subprocess.run(["gh", *args], ...)`. Tests pass a fake `run` keyed on the argument list.
- `main(argv)` prints `json.dumps(snapshot)`; wraps everything in `try/except` producing `{"error": ..., "action": {"kind": "idle", ...}}`.
- Also `list_stale_worktrees(snapshot, worktree_dirs) -> list[Path]` for worktrees whose PR is merged/closed (used by the driver for cleanup).

`gh` calls (all `--json` or GraphQL):
- issues: `gh issue list --state open --label agent-ready|agent-working|agent-blocked --json number,title,labels,updatedAt --limit 100` (three calls, merged by number).
- prs: `gh pr list --state open --search "head:agent/" --json number,headRefName,labels,reviewDecision,statusCheckRollup,commits,body --limit 50`; `issue` parsed from `Closes #N` in body or from the branch name.
- threads: GraphQL `repository.pullRequest(number).reviewThreads(first:100){nodes{isResolved comments(last:1){nodes{body createdAt}}}}` and `reviews(last:20){nodes{state submittedAt}}`.
- `checks` derived from `statusCheckRollup`: any FAILURE/ERROR → `failure`; any PENDING/IN_PROGRESS/QUEUED → `pending`; all SUCCESS/SKIPPED/NEUTRAL → `success`; empty → `none`.
- `last_commit_at` from the last commit in `commits`; `last_agent_push_at` = same (agent is the only pusher on `agent/` branches; a human push counts as an answer too, which is acceptable).

- [ ] Step 1: Add tests for `checks` derivation, thread marker detection, `Closes #N` parsing, and `main` error path (fake `run` raising → idle JSON, exit 0).
- [ ] Step 2: Fail → implement → pass → commit `feat(agents): GitHub state snapshot for the issue driver`.

### Task 3: Custom agents

**Files:** create the six `.claude/agents/*.md` from the spec table. Each has frontmatter (`name`, `description`, `tools`, optional `skills`, `effort`) and a body: role, hard rules (verbatim from Global Constraints), working-directory rule ("work only inside the worktree path given in your prompt; use absolute paths"), and the report contract (structured fields the workflow's schema will demand).

- [ ] Step 1: Write the files.
- [ ] Step 2: Verify: `claude agents` (or `/agents` in a session) lists all six without frontmatter errors. Fallback: dispatch `issue-triager` via the Agent tool with a trivial prompt and confirm it runs.
- [ ] Step 3: Commit `feat(agents): custom agent definitions`.

### Task 4: `issue-pipeline.js`

**Files:** create `.claude/workflows/issue-pipeline.js`.

Structure (all branching on schema fields):
```js
export const meta = { name: 'issue-pipeline', description: '…', phases: [Setup, Triage, Spec, Plan, Implement, Review, Ship] }
const n = args.issue; const now = args.now
const setup = await agent(setupPrompt(n), {agentType:'pr-fixer', phase:'Setup', schema: SETUP})
const triage = await agent(triagePrompt(setup), {agentType:'issue-triager', phase:'Triage', schema: TRIAGE})
if (triage.verdict !== 'workable') return await block(setup, triage)
if (!setup.existing_spec) await agent(specPrompt(setup, triage), {agentType:'spec-writer', phase:'Spec', schema: PATH})
const plan = setup.existing_plan ? await agent(readPlanPrompt(setup), {...}) : await agent(planPrompt(setup), {agentType:'planner', phase:'Plan', schema: PLAN})
if (plan.tasks.length > 12) return await block(setup, {verdict:'too_big', ...})
const start = (await agent(progressPrompt(setup, plan), {agentType:'code-reviewer', phase:'Implement', schema: PROGRESS})).next_task_index
for (const task of plan.tasks.slice(start)) { … implementer → reviewer → ≤3 fix rounds … }
// branch review: parallel 3 lenses → dedupe → adversarial refute per finding → 1 fix pass → re-review
const ship = await agent(shipPrompt(setup, residual), {agentType:'pr-fixer', phase:'Ship', schema: SHIP})
return { status:'pr_opened', pr: ship.pr_number, url: ship.pr_url, residual }
```
`block()` posts the comment with the marker, swaps labels, returns `{status:'blocked', reason}`.

- [ ] Step 1: Write the script with all prompts inline (task text, worktree path, test commands, report contract).
- [ ] Step 2: Syntax check: `node --check .claude/workflows/issue-pipeline.js` (ESM export is valid under `--check` with `.mjs`; copy to a temp `.mjs` for the check).
- [ ] Step 3: Commit `feat(agents): issue-pipeline workflow`.

### Task 5: `pr-tend.js`

**Files:** create `.claude/workflows/pr-tend.js` with phases Read, Fix, Verify, Push. Args `{pr, mode, now}`. Same schema-driven style; the Push stage replies per thread with the marker for `review`, one PR comment for `ci`; blocks the PR with `agent-blocked` when `args.repeat_failure` is true (driver sets it when the same failing check name appears in the last wizbot CI comment).

- [ ] Steps: write, `node --check`, commit `feat(agents): pr-tend workflow`.

### Task 6: `/work-issues` skill

**Files:** create `.claude/skills/work-issues/SKILL.md`.

Frontmatter: `name: work-issues`, `description`, `argument-hint: "[--dry-run]"`, `allowed-tools: Workflow(issue-pipeline) Workflow(pr-tend) Bash(git worktree *) Bash(gh *)`.
Body: injected `!`python3 ${CLAUDE_SKILL_DIR}/state.py`` block, then the four numbered rules from the spec (idle → one line; `--dry-run` → print action; else run the one workflow named in `action.workflow` with `action.args` plus `now` from the snapshot's `generated_at`, wait, report; never two workflows). Cleanup rule: for each path in `stale_worktrees`, `git worktree remove --force <path>`.

- [ ] Step 1: Write it. Step 2: run `/work-issues --dry-run` in-session and confirm it prints an `idle` action against the live repo (no agent-ready issues yet). Step 3: commit `feat(agents): /work-issues driver skill`.

### Task 7: Labels and docs

- [ ] Create the four labels with `gh label create <name> --color <hex> --description <text>` (idempotent with `--force`).
- [ ] Add an "Agent system" section to `CLAUDE.md`: how to start (`/loop /work-issues`), the labels, the hard rules, where the pieces live, how to unblock an issue.
- [ ] Commit `docs: agent system section`.

### Task 8: End-to-end check

- [ ] Create a throwaway issue ("agent smoke test: add a `tests/test_agent_smoke.py` asserting `1 + 1 == 2`") labeled `agent-ready`.
- [ ] Run `/work-issues` once. Confirm: worktree created, spec+plan committed, implementation committed, PR opened with `Closes #N` and the marker, issue relabeled `agent-pr-open`.
- [ ] Run `/work-issues --dry-run` again → idle (PR green, no threads).
- [ ] Close the PR unmerged, delete the branch, close the issue, remove the worktree. Record findings in the PR description of `feat/agent-system`.

### Task 9: Open the PR for `feat/agent-system`

- [ ] Full test suite green; push; `gh pr create` with summary, testing evidence, and the attribution lines.
