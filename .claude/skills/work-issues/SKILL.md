---
name: work-issues
description: One tick of the GitHub issue agent system. Snapshots GitHub state, picks exactly one action (fix red CI on an agent PR, answer human review threads on an agent PR, or start the next agent-ready issue), runs the matching saved workflow, and reports. Run it continuously with `/loop /work-issues`.
argument-hint: "[--dry-run]"
allowed-tools: Workflow, Bash(git worktree *), Bash(python3 *), Bash(gh *)
---

# /work-issues — one tick of the agent system

Arguments: `$ARGUMENTS`

## Snapshot (live, generated when this skill was invoked)

```!
python3 .claude/skills/work-issues/state.py
```

## What to do with it

The snapshot above is JSON. Its `action` field is the single decision for
this tick; do not second-guess it or look for more work.

1. **Clean up first.** For every path in `stale_worktrees`, run
   `git worktree remove --force <path>`. These belong to issues whose PR is
   merged or closed. Ignore failures.
2. **If `error` is present**, print it on one line and stop. The next tick
   retries.
3. **If `action.kind` is `idle`**, print one line: `idle — <action.reason>`.
   Stop. Under a self-paced loop, ask for the next wake-up 20 to 30 minutes
   out.
4. **If the arguments contain `--dry-run`**, print `would run <action.workflow>
   with <action.args> — <action.reason>` and stop. Do not run anything.
5. **Otherwise run exactly one workflow** with the Workflow tool:
   - `name`: `action.workflow` (`issue-pipeline` or `pr-tend`)
   - `args`: `action.args` plus three more keys:
     - `now`: the snapshot's `generated_at`
     - `repo_root`: the absolute path of this repository's main checkout
       (the current working directory)
     - `attribution`: the commit and PR attribution lines this session was
       given in its system reminder, verbatim, or an empty string if none
   Wait for the workflow's completion notification. Never start a second
   workflow in the same tick, even if the first finishes quickly.
6. **Report** in one short paragraph: the action taken, the outcome the
   workflow returned (`pr_opened` with the PR link, `pushed`, `blocked` with
   the reason, or `nothing_to_do`), and what the next tick will look at.
   If the workflow returned `blocked`, say plainly that the owner has to act
   and where the comment is. Under a self-paced loop, ask for the next
   wake-up about 5 minutes out after `pr_opened` or `pushed` (CI needs time
   to run), and 20 to 30 minutes out otherwise.

## Rules

- Only a human merges. This skill never merges, never touches `main`, and
  never removes `agent-blocked` from anything.
- One workflow per tick. If a workflow from an earlier tick is still
  running, report that and stop.
- Do not create issues, PRs, or comments yourself; the workflows do that
  with the `<!-- wizbot -->` marker so the snapshot can recognise them.
