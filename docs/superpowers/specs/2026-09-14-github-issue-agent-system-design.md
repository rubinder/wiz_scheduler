# GitHub Issue Agent System — Design

**Date:** 2026-09-14
**Status:** approved in conversation; implementation follows this spec.

## Goal

A local, in-repo multi-agent system that keeps working the GitHub backlog of
`rubinder/wiz_scheduler` without a human driving each step: it picks up
issues the owner has opted in, designs and implements them on a branch,
reviews its own work, opens a pull request, then keeps that PR healthy
(red CI, human review comments) until a human merges it.

Decisions already made by the owner:

| Decision | Choice |
|---|---|
| Runtime | Local Claude Code session in this repo. No GitHub Actions, no API key. |
| Merge policy | Agents open PRs. Only a human merges. Every push to `main` deploys to production, so this is a hard rule. |
| Issue scope | Only issues labeled `agent-ready`. Everything else is invisible to the system. |

## Non-goals

- Running unattended after the terminal closes. A `/loop` is session-scoped;
  when the session ends the loop stops and the next session resumes from
  GitHub state.
- Merging, force-pushing, or touching `main`, `terraform/**`, or `.github/**`.
- Working more than one issue at a time. Serial is simpler, and the
  per-issue pipeline already parallelises review internally.
- Persisting any state outside GitHub (labels, branches, PRs) and the
  branch's own committed files.

## Architecture

Three layers, all committed under `.claude/` so they travel with the repo.

```
/loop /work-issues                       (owner starts this once per session)
   │
   ▼
.claude/skills/work-issues/SKILL.md      driver: one action per tick
   │  injects `state.py` snapshot, picks the action, runs one workflow
   ├──► .claude/workflows/issue-pipeline.js   (args: {issue})
   │        triage → spec → plan → implement tasks → branch review → ship
   └──► .claude/workflows/pr-tend.js          (args: {pr, mode: "ci"|"review"})
            read failure / threads → fix → test → push → reply
                │
                ▼
        .claude/agents/*.md              issue-triager, spec-writer, planner,
                                          implementer, code-reviewer, pr-fixer
```

### Layer 1 — driver skill `/work-issues`

`.claude/skills/work-issues/SKILL.md`, with `argument-hint: [--dry-run]`.

At invocation it injects the output of
`python3 .claude/skills/work-issues/state.py` (see below), which is a JSON
snapshot of GitHub state plus a single chosen `action`. The skill body
tells Claude to:

1. If `action.kind == "idle"`, print one line ("nothing to do") and finish.
   Under a self-paced loop, schedule the next wake-up 20–30 minutes out.
2. If `--dry-run` was passed, print the action and finish.
3. Otherwise run the named workflow with the given args, wait for its
   notification, and report one paragraph: what was done, the PR or issue
   link, and what the next tick will look at.
4. Never run two workflows in one tick.

The skill is model-invocable (no `disable-model-invocation`) because
`/loop` only re-fires skills Claude is allowed to invoke.

### `state.py` — snapshot and decision

Stdlib only (`subprocess`, `json`, `sys`, `datetime`). Shells out to `gh`.
Always exits 0 and always prints JSON; failures become
`{"error": "...", "action": {"kind": "idle", "reason": "..."}}` so the
skill's dynamic-context injection never aborts.

Snapshot shape:

```json
{
  "generated_at": "2026-09-14T18:02:11Z",
  "issues": [
    {"number": 112, "title": "…", "labels": ["agent-ready"], "updated_at": "…"}
  ],
  "prs": [
    {
      "number": 115, "branch": "agent/issue-112-short-slug", "issue": 112,
      "checks": "success" | "failure" | "pending" | "none",
      "review_decision": "APPROVED" | "CHANGES_REQUESTED" | "REVIEW_REQUIRED" | "",
      "unanswered_threads": 2,
      "labels": ["agent-pr-open"],
      "last_agent_push_at": "…"
    }
  ],
  "action": {"kind": "…", "workflow": "…", "args": {…}, "reason": "…"}
}
```

`choose_action(snapshot)` is a pure function so it can be unit tested
without `gh`. Priority order:

1. **`pr-tend` mode `ci`** — an open agent PR whose checks are `failure`
   and which is not labeled `agent-blocked`.
2. **`pr-tend` mode `review`** — an open agent PR with
   `unanswered_threads > 0`, or `review_decision == CHANGES_REQUESTED`
   with no agent push since the review. A thread is *unanswered* when its
   latest comment does not carry the agent marker (below).
3. **`issue-pipeline`** — the oldest open issue labeled `agent-ready` that
   carries none of `agent-working`, `agent-pr-open`, `agent-blocked`.
4. **`issue-pipeline` (resume)** — an issue labeled `agent-working` whose
   branch has no commit in the last 3 hours. A crashed or interrupted
   run; the pipeline is resumable (see below).
5. **`idle`**.

Ties inside a priority go to the lowest number (oldest).

Agent PRs are recognised by head branch prefix `agent/`. Agent-authored
comments are recognised by a trailing HTML marker `<!-- wizbot -->`,
because the agent posts with the owner's own `gh` account and there is no
other way to tell its comments from the owner's.

### Layer 2 — workflows

Both are saved scripts under `.claude/workflows/` so the driver invokes
them by name with `args`. They are plain JavaScript per the Workflow tool
contract (no `Date.now()`; timestamps arrive in `args`).

#### `issue-pipeline` — args `{issue, now}`

Phases, in order. Every stage is an `agent()` call with a JSON schema so
the script branches on data, not prose.

1. **Setup** (`pr-fixer` agent type, low effort). Fetch the issue with
   `gh issue view --json`. Ensure the worktree exists at
   `.claude/worktrees/issue-<n>` on branch `agent/issue-<n>-<slug>` from
   `origin/main` (create if absent; reuse if present). Symlink
   `frontend/node_modules` from the main checkout. Add `agent-working`,
   remove `agent-ready`. Return `{worktree, branch, issue_title, issue_body,
   existing_spec, existing_plan}`.
2. **Triage** (`issue-triager`, read-only). Returns
   `{verdict: "workable"|"too_big"|"needs_answers", questions[], decomposition[], summary}`.
   - `too_big`: more than roughly 12 plan-sized tasks, or requires changes
     under `terraform/**` or `.github/**`, or needs credentials or an
     external account the repo does not have.
   - `needs_answers`: the issue leaves a decision open that would produce
     materially different work.
   - On either non-workable verdict the script posts one issue comment
     (questions or proposed sub-issues) with the marker, swaps
     `agent-working` for `agent-blocked`, removes the worktree if it has
     no commits, and returns. The owner clears `agent-blocked` and re-adds
     `agent-ready` to retry.
3. **Spec** (`spec-writer`). Skipped when `existing_spec` is set. Writes
   `docs/superpowers/specs/<date>-issue-<n>-<slug>-design.md` in the
   worktree and commits it. Same structure as existing specs in that
   directory: goal, non-goals, design, data flow, error handling, testing.
4. **Plan** (`planner`, preloads `superpowers:writing-plans`). Skipped when
   `existing_plan` is set. Writes
   `docs/superpowers/plans/<date>-issue-<n>-<slug>.md` and commits it.
   Returns `{plan_path, tasks: [{index, title}]}`. If `tasks.length > 12`
   the script treats it as `too_big` (same handling as triage).
5. **Implement** — serial over tasks, because plan tasks depend on each
   other. For each task:
   - `implementer` (preloads `superpowers:test-driven-development`).
     Prompt built from the superpowers implementer template: task text,
     worktree path, test commands, "commit when green", report status
     `DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT`.
   - `code-reviewer` (read-only) reviews `git diff <task_base>..HEAD`
     against the task text. Returns
     `{approved, findings: [{severity, file, line, what, why, fix}]}`.
   - Up to 3 fix rounds: `implementer` resumed with the Critical and
     Important findings, then a scoped re-review. Minor findings are
     recorded, not fixed.
   - `BLOCKED` or `NEEDS_CONTEXT` or a 3-round failure ends the pipeline
     as `needs_answers` with the implementer's message as the comment.
   Tasks whose commits already exist on the branch (resume case) are
   detected by a "progress" call: `code-reviewer` reads the plan and
   `git log` and returns the index of the first unfinished task.
6. **Branch review** — whole diff `origin/main..HEAD`. Three
   `code-reviewer` calls in parallel with distinct lenses: correctness,
   CLAUDE.md conventions (roles never hardcoded, plan limits, logical
   Tailwind utilities, UTC today, timezone-aware timestamps), and tests.
   Findings are deduplicated by file and line, then each Critical or
   Important finding gets one adversarial `code-reviewer` call prompted to
   refute it. Surviving findings go to one `implementer` fix pass and one
   scoped re-review. Residual findings are listed in the PR body under
   "Known gaps", never silently dropped.
7. **Ship** (`pr-fixer`). Run the full backend and frontend suites one
   last time; if red, stop as `BLOCKED`. Push the branch, open the PR with
   `gh pr create` (title from the issue, body from a template: summary,
   `Closes #<n>`, what was tested with the exact commands, review
   findings fixed and parked, the wizbot marker, and the attribution
   lines the session was given). Swap `agent-working` for `agent-pr-open`
   on the issue. Return `{pr_number, pr_url}`.

The script returns a short structured summary the driver relays.

#### `pr-tend` — args `{pr, mode, now}`

1. **Read** (`pr-fixer`, read-only tools). Resolve the PR's branch and
   worktree (create the worktree from the remote branch if this session
   has none). For `ci`: `gh pr checks` and `gh run view --log-failed` for
   each failing run; return the failing tests and the relevant log tail.
   For `review`: the unanswered review threads via the GraphQL
   `reviewThreads` connection; return each thread's path, line, and
   comment text.
2. **Fix** (`pr-fixer`). For `ci`: reproduce locally, fix, run the
   covering tests and then the full suite, commit. For `review`: address
   each thread in code where the reviewer is right; where the reviewer is
   wrong, do not change code. Returns per-thread
   `{thread_id, action: "changed"|"declined", reply}`.
3. **Verify** (`code-reviewer`) reviews only the new commits. One fix
   round on Critical findings.
4. **Push and reply** (`pr-fixer`). `git push` (never `--force`). For
   `review`: reply on each thread with the marker; never resolve threads,
   the human does. For `ci`: one PR comment listing what was fixed. If CI
   fails again on the next tick for the same reason twice, label the PR
   `agent-blocked` and comment, so the driver stops retrying.

### Layer 3 — custom agents

All in `.claude/agents/`. They inherit the project `CLAUDE.md`, so the
repo's conventions bind them without restating. Each system prompt states
the role, the hard rules, and the report contract. `model` is left to
inherit unless noted.

| Agent | Tools | Notes |
|---|---|---|
| `issue-triager` | Read, Grep, Glob, Bash(gh *), Bash(git log *), Bash(git diff *) | Read-only. Estimates size against the plan-task yardstick, spots infra or credential needs. |
| `spec-writer` | Read, Grep, Glob, Write, Edit, Bash | Writes one spec file, commits it. Never edits source. |
| `planner` | Read, Grep, Glob, Write, Edit, Bash | `skills: [superpowers:writing-plans]`. Writes one plan file, commits it. |
| `implementer` | Read, Grep, Glob, Write, Edit, Bash | `skills: [superpowers:test-driven-development]`. Works only inside the given worktree. Never dispatches subagents. |
| `code-reviewer` | Read, Grep, Glob, Bash(git *), Bash(gh *) | Read-only; never mutates the worktree. `effort: high`. |
| `pr-fixer` | Read, Grep, Glob, Write, Edit, Bash | Setup, CI/review fixes, push, PR creation, comments. |

Hard rules repeated in every agent prompt:

- Never run `git push` to `main`, `git push --force`, `gh pr merge`, or
  edit anything under `terraform/` or `.github/`.
- Never ask the user a question; report `BLOCKED` with the question
  instead. The workflow turns it into an issue comment.
- Every comment or PR body you post ends with `<!-- wizbot -->`.

## Data flow and state

All durable state is on GitHub:

| Where | Meaning |
|---|---|
| label `agent-ready` | owner opted the issue in |
| label `agent-working` | a pipeline run owns it (or crashed owning it) |
| label `agent-pr-open` | a PR exists; the issue is done from the system's view |
| label `agent-blocked` | the system needs the owner; never retried until the label is removed |
| branch `agent/issue-<n>-<slug>` | all commits, including the spec and plan |
| PR body / comments with `<!-- wizbot -->` | what the system did and why |

Local, disposable: the worktree at `.claude/worktrees/issue-<n>` (already
gitignored). It is removed by the driver on the tick after it sees the
PR merged or closed; until then it stays so `pr-tend` is fast.

Test commands used inside a worktree `W`, with `R` the main checkout:

```
R/backend/.venv/bin/python -m pytest tests/ -x -q         (cwd = W)
cd W/frontend && npm run build && npm test                (node_modules symlinked from R)
```

## Error handling

- `state.py` never raises to the skill; every failure becomes an `idle`
  action with a reason.
- A workflow agent returning `null` (skipped or died) is treated as
  `BLOCKED` for that stage; the pipeline posts what it knows and labels
  `agent-blocked`. Nothing is retried blindly.
- Fix-round caps: 3 per task, 1 for the branch review, 1 per `pr-tend`
  run. Same CI failure twice across ticks blocks the PR.
- Resume: a stale `agent-working` issue re-enters the pipeline; setup
  reuses the branch, discards any uncommitted changes an interrupted run
  left behind (an agent worktree holds only agent output), spec and plan
  are skipped if their committed files exist, and the progress call skips
  tasks already committed.
- The driver runs exactly one workflow per tick, so two ticks can never
  race on the same issue as long as the owner runs one loop. If a fixed
  interval fires while a workflow is still running, the driver sees the
  running workflow's issue as `agent-working` with a fresh commit and
  skips it.

## Testing

- `tests/test_agent_state.py` — unit tests for `choose_action` and the
  snapshot parsers in `state.py` (loaded with `importlib` from the skills
  directory; no `gh` calls). Covers every priority rule, the marker
  detection, and the stale-run threshold.
- `/work-issues --dry-run` — prints the chosen action without running a
  workflow; used to confirm wiring against the live repo.
- End-to-end: a small throwaway issue is created, labeled `agent-ready`,
  run through `/work-issues`, and the resulting PR is inspected. The PR is
  closed unmerged and the issue closed afterwards.
- Workflow scripts have no unit test harness; they are validated by the
  end-to-end run and by keeping all branching on schema-validated fields.

## Files

```
.claude/agents/issue-triager.md
.claude/agents/spec-writer.md
.claude/agents/planner.md
.claude/agents/implementer.md
.claude/agents/code-reviewer.md
.claude/agents/pr-fixer.md
.claude/workflows/issue-pipeline.js
.claude/workflows/pr-tend.js
.claude/skills/work-issues/SKILL.md
.claude/skills/work-issues/state.py
tests/test_agent_state.py
CLAUDE.md                      (new section: "Agent system")
```

GitHub labels created once with `gh label create`: `agent-ready`,
`agent-working`, `agent-pr-open`, `agent-blocked`.
