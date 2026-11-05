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
- Permission mode: `bypassPermissions`, scoped by running only inside ~/factory clones.
- Usage-limit message: unknown — preflight falls back to now + 1 h. `claude -p --help` and `claude --help` document no usage-limit message format.
