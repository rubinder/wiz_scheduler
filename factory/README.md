# WizScheduler factory

Runs on the owner's Mac. Spec: docs/superpowers/specs/2026-11-04-software-factory-design.md.

## Install / stop

- Install: `factory/install.sh` (clones to ~/factory, installs the LaunchAgent).
- Stop now: `touch ~/factory/STOP`. Resume: `rm ~/factory/STOP`.
- Uninstall: `factory/uninstall.sh` (keeps the clones).
- Dry run: `factory/tick.sh --dry-run` prints the chosen action and gates and changes nothing.

## Verified runtime (2026-11-04)

- Workflow tool headless: ok past 10 min. With `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, a nested `claude -p` ran a Workflow whose one agent made two foreground Bash calls of 330 s each. It took 669 s (11 min 9 s) and returned `{"ok":true,"reply":"DONE"}`. Two caveats for workflow agents: the harness rejects a bare `sleep N` ("Blocked: standalone sleep"), and a command that would exceed the shell timeout gets backgrounded, after which the agent may end its turn early. Workflow scripts should return the agent's reply, not a constant, so an early exit is visible.
- Gmail connector headless: AVAILABLE (ToolSearch loaded the send_message schema; nothing was sent) — digest path: email
- Mac notifications: ok (`osascript display notification` exited 0; the banner itself is not visible to an agent, so the owner should confirm it appeared)
- Alerts reach the phone: every Mac notification is also queued and emailed at the end of its tick (`factory/outbox.py`, factory-tick section 4). Answer on GitHub, never by email: an owner comment on an `agent-blocked` issue that is newer than the factory's comment retries the issue once.
- Every tick (unless `~/factory/STOP` exists or another tick holds the lock) first resets both clones to the newest `main`, then runs preflight, so a fix to preflight lands even while ticks keep skipping. Owner activity is the last user/assistant message in any of the owner's Claude Code transcripts, not a file's mtime.
- Pipeline setup never reuses an `agent/issue-N-*` branch whose PR merged or closed: it removes that worktree and branch (locally and on GitHub) and starts fresh from `origin/main`.
- The clones push over HTTPS with `gh`'s login (`install.sh` sets `credential.https://github.com.helper` to `gh auth git-credential` in each clone).
- Permission mode: `bypassPermissions`, scoped by running only inside ~/factory clones.
- Usage-limit message: unknown — preflight falls back to now + 1 h. `claude -p --help` and `claude --help` document no usage-limit message format.
