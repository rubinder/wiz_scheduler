# Software Factory — Design

**Date:** 2026-11-04
**Status:** Draft for owner review
**Repos:** `rubinder/wiz_scheduler` (app, public) and `rubinder/wiz_scheduler_marketing` (site)
**Builds on:** the GitHub issue agent system from PR #112 (branch `feat/agent-system`, closed unmerged; design in `docs/superpowers/specs/2026-09-14-github-issue-agent-system-design.md` on that branch) and the daily SEO guides design (marketing commit `9cac282`, `docs/superpowers/specs/2026-10-20-seo-guides-pipeline-design.md`).

## Intent

The owner stops babysitting day-to-day changes. Work goes in as GitHub issues and comes out as merged, deployed, verified changes in both repos. Every change is judged against the application's own architecture and against adversarial quality gates chosen for that change. When something slips through, the factory is tuned so that whole class of error is caught more often, instead of the instance being patched by hand.

Success looks like:
- `agent-ready` issues ship without the owner touching them, except changes in protected areas.
- One guide a day ships on the marketing site in 19 locales.
- Every slip produces a retro and a proposed factory change.
- The owner hears about exactly the things that need them, and nothing else.

## Owner decisions

1. **Merge on green, with protected areas.** The factory merges its own PR when CI is green, every selected gate passed, and no review thread is open. PRs touching a protected area pass every gate and then wait for the owner's merge. Protected areas:
   - `backend/alembic/`
   - billing and Stripe: `backend/services/billing*.py`, `backend/services/payroll_archive.py` (it refunds through Stripe), `backend/routers/billing.py`, `backend/routers/webhooks.py`
   - `backend/legal/` and `backend/routers/gdpr.py`
   - `terraform/` and `.github/`
   - auth and tenancy core: `backend/routers/auth.py`, `backend/dependencies.py`, `backend/tenant.py`, `backend/services/location_scope.py`
   - the factory's own files: `.claude/**`, `factory/**`
2. **Work intake.** The factory builds issues labelled `agent-ready`. It may propose issues, labelled `agent-proposed`, from its own findings. Those findings are bugs, CI failures, Search Console gaps, architecture violations and retros. A proposal is built only after the owner relabels it `agent-ready`.
3. **Runtime: the owner's Mac, on the owner's Claude subscription.** No API key and no per-use bill. The subscription's usage window is the budget.
4. **Architecture:** new and changed code must follow the architecture skill. Existing violations go on an allowlist, each with an `agent-proposed` cleanup issue. Old code is never refactored up front.
5. **Accessibility target: WCAG 2.2 AA.**
6. **Notifications:** a Mac notification for anything that needs the owner, plus a daily email digest.
7. **New dev dependencies approved:** `@axe-core/playwright` and `lighthouse`, both dev-only.
8. **Guides line:** adopt the 2026-10-20 guides design, with the changes in "Guides line" below.

## Architecture overview

```
launchd (every 30 min) ─► factory/tick.sh ─► preconditions ─► claude -p "/factory-tick"
                                                                  │
                                    factory/state.py picks ONE action (priority order)
        ┌──────────────┬──────────────┬──────────────┬─────────────┬──────────────┐
        Fix PR         Merge PR       Build issue    Guide         Propose / Retro
        (pr-tend)      (merge+verify) (issue-pipeline+gates) (guides-pipeline) (drafts)
```

All factory code lives in the app repo, under `factory/` and `.claude/`. The marketing repo gets only its own skill, CI boundary check and Lighthouse baseline.

### Where it runs

- Dedicated clones live at `~/factory/wiz_scheduler` and `~/factory/wiz_scheduler_marketing`, and the factory uses worktrees inside them. It never touches the owner's working checkouts.
- `factory/install.sh` creates the clones and installs a LaunchAgent, `~/Library/LaunchAgents/com.wizscheduler.factory.plist`, that runs every 30 minutes. It also writes `~/factory/config.toml`, which holds the notification address, quiet hours, the protected path list and the idle threshold.
- `factory/uninstall.sh` removes the LaunchAgent. It does not delete the clones.

### The tick (`factory/tick.sh`)

The tick exits quietly, and logs why, when any of these is true:
- the Mac is on battery (`pmset -g batt`)
- Docker is not running (`docker info`)
- a lock file is held by a live process
- a `paused_until` time in `~/factory/state.json` is in the future
- the owner is active: any Claude Code transcript outside `~/factory` was modified in the last 15 minutes

Otherwise it runs `caffeinate -i claude -p "/factory-tick"` from the app clone with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`. Without that variable, print mode kills background workflows at 10 minutes, a known quirk from #112. Logs go to `~/Library/Logs/wizfactory/YYYY-MM-DD.log`.

**Usage limit.** When a run ends on a usage-limit error, the tick writes `paused_until` (the reset time if one is reported, otherwise now + 1 h). The next tick resumes where the last left off: branches, worktrees and labels are the durable state, as in #112.

### Action selection (`factory/state.py`)

This extends #112's `state.py`. It snapshots both repos through `gh` and picks exactly one action, in this order:

1. **Fix.** A factory PR has red CI or unanswered review threads. Runs #112's `pr-tend` workflow unchanged.
2. **Merge.** A factory PR is green, its gate report says pass, no thread is open, and it touches no protected path. The action:
   - squash-merges the PR
   - watches the deploy run
   - runs the smoke check (below)
   - if the deploy or the smoke check fails, opens a revert PR, merges it on green, labels the original issue `factory-miss`, and notifies the owner.
3. **Verify.** A previous merge has no smoke result yet, for example because the tick ended mid-deploy. Runs the smoke check.
4. **Build.** The lowest-numbered `agent-ready` issue in either repo: #112's `issue-pipeline` plus the gates below.
5. **Guide.** Today's guide is not yet merged or blocked. Runs `guides-pipeline`.
6. **Retro.** An item labelled `factory-miss` has no retro yet.
7. **Propose.** Turns accumulated findings into `agent-proposed` issues, at most 3 per day, deduplicated against open issues by title and a key line.
8. **Idle.**

There is at most one in-flight Build per repo. A PR waiting on the owner does not block the next issue.

### Smoke check (`factory/smoke.py`)

- **App:** `GET https://app.wizscheduler.com/health` returns 200, `/` and `/login` return 200, and every route the PR changed, read from the diff, returns 200 or its expected auth redirect.
- **Marketing:** `/`, `/features`, the changed pages and, for a guide PR, the guide in every merged locale return 200, with a correct `lang`/`dir` and canonical.

## Gates

Each gate is a file, `factory/gates/<name>.md`, with front matter `triggers` (path globs), `mechanical` (commands) and `reviewer` (prompt section). It also says what it reads, what it checks, and what counts as a pass. A gate uses a mechanical check wherever the rule can be checked by machine. An AI reviewer covers what needs judgment.

The flow for each gate:
1. Run the mechanical check.
2. Run the reviewer, a fresh agent with a read-only tool set.
3. Run #112's refute pass on each blocking finding.
4. Send confirmed findings back to the implementer, for up to 3 rounds.
5. If findings remain, label the issue `agent-blocked` with a comment explaining why, and notify the owner.

### Always run

| Gate | Check |
|---|---|
| ci | Backend suite (Docker), `npm run build`, `npm test`. In marketing: `astro check`, `vitest`, `build`, `check:dist`. |
| correctness / conventions / tests | #112's three review lenses plus the refute pass. |
| architecture | Reviews the diff against the architecture skill; backed by the mechanical layering tests. Findings name the pattern. |

### Selected per change

| Gate | Triggers | Mechanical | Reviewer focus |
|---|---|---|---|
| tenancy-security | `backend/routers/**`, `backend/services/**`, `backend/models/**` | `test_tenant_filter_integration.py`, `test_rls_enforce_integration.py`, `test_body_id_ownership_integration.py`, `test_location_scope_*` | `company_id` scoping, RLS, IDs from another tenant, auth on new endpoints, secrets |
| accessibility | `frontend/src/{pages,components}/**`, `frontend/src/theme*`, `*.css`; marketing `src/{views,components,pages}/**` | axe (WCAG 2.2 AA tags) via `@axe-core/playwright` on the affected routes; `frontend/scripts/verify/contrast.mjs` | keyboard paths, focus order, labels, RTL |
| lighthouse | any marketing change; app `frontend/src/pages/{Login,Register}*` | `lighthouse` on the affected pages, mobile preset, 3 runs, median; fails if any category is below the stored baseline minus 2 points | none |
| i18n | `frontend/src/i18n/**`, any `.tsx` adding `t.` keys; marketing `src/i18n/**`, guides | the existing translation and logical-direction tests | translation quality, untranslated strings outside the checked sections, length overflow |
| copy-truth | marketing copy, guides, app `landing`/`features` i18n | none | every product claim is backed by app code or config, read at the current commit |
| migration-safety | `backend/alembic/**` (also protected) | `test_migrations.py`, 7shifts/Deputy compat tests, upgrade then downgrade on a scratch DB | data loss, locking, reversibility |

**Lighthouse baselines** live in `factory/baselines/lighthouse.json`, one entry per URL. A merge that improves a score raises the baseline in the same PR. A new page gets a floor of 90 for every category, except Performance, which gets 80.

**Selection** (`factory/select_gates.py`):
1. Path globs pick most gates deterministically.
2. A small, fast model reads the diff summary and may add gates the globs missed, for example a `utils/` change that alters rendering. It may never remove a gate.

The selected gates and the reason for each are written into the PR body under "Gates". Owner corrections to the selection become glob or rule changes through the Retro loop.

## The architecture skill

- **App:** `.claude/skills/architecture/SKILL.md`, plus `references/<context>.md` for each bounded context.
- **Marketing:** a smaller `.claude/skills/architecture/SKILL.md` covering copy sourced from the app's i18n via `port:copy`, `src/lib/links.ts` as the locale source of truth, content collections, and the guides boundary. Its stale `CLAUDE.md` (lint script, locale count, test path) is corrected in the same change.

### Bounded contexts

Each mapping is verified against the code while the skill is written.

- **Identity & Tenancy:** auth, users, `tenant.py`, RLS, `location_scope`, email verification.
- **Workforce:** employees, availability, preferences, affinities, roles, `services/archive.py`.
- **Scheduling:** `scheduling/` (LangGraph and local scheduler), schedules, `schedule_lock`, open shifts.
- **Billing & Plans:** `services/billing`, `services/plan`, Stripe webhooks, `OwnershipGroup`.
- **Payroll:** `payroll_export`, retention, the payroll archive.
- **Check-In, Legal/GDPR:** their own modules.
- **Imports:** 7shifts and Deputy, the anti-corruption layer for external systems.

### Named patterns

Each pattern has "use when", a real example (file and symbol) and the anti-pattern it prevents.

- **Aggregate root:** `OwnershipGroup` owns plan, billing and lifecycle.
- **Single evaluator:** one function per domain question: `availability_check.is_available`, `preferences.annotate_preference_violations`, `plan.assert_can_add`. Never a second copy, including the frontend.
- **Anti-corruption layer:** external models (7shifts, Deputy, Stripe) are translated at the edge into domain terms.
- **Thin router:** a router parses, authorizes and calls a service. Rules live in services.
- **Archive, never delete**, and **location-local time** as a value never converted per employee.
- **Fail closed:** money, deletion and legal records.
- **UTC today.**

### Mechanical layering tests

- **Backend:** `tests/test_architecture.py` is an AST sweep, in the style of `test_utc_today.py`. It fails when:
  - a router imports a router
  - a model imports a service or router
  - a Stripe charge or refund call appears outside `services/billing*`
  - `archived_at` is assigned outside `services/archive.py`
  - `date.today()` is used, which stays in its existing test.
- **Frontend:** `frontend/src/architecture.test.ts` fails when:
  - `fetch(` appears outside `src/api/`
  - `new Date("YYYY-MM-DD")`-style parsing of a date-only string appears
  - an availability evaluation appears in the frontend.
- **Ratchet:** existing violations live in `tests/architecture_allowlist.json` (and the frontend equivalent), each with the issue number of its cleanup. A test fails on any violation not on the list, and fails if a listed violation no longer exists, so the list only shrinks.

## Guides line

This adopts the 2026-10-20 guides design (marketing `9cac282`) with every owner decision in it unchanged:
- fully hands-off
- one topic a day in all 19 locales
- edits only under `src/content/guides/**`
- shared ASCII slugs
- legal scope kept from the source guide
- the gate drops a failing locale, and a failing source guide blocks the day
- interlinking between guides

These changes fit it to the factory:
- **Runs as the factory's Guide action** on the Mac, not in GitHub Actions. There is no GitHub App, OAuth secret or auto-merge setup; the factory merges on green itself.
- **Data:** `gum` installed locally (`brew` or its release binary) and authorized once against Search Console. Keyword Planner stays optional.
- **Gates:** the design's quality gate, plus copy-truth against the app code (not only `backend/config.py`), plus lighthouse, accessibility and `check:dist`.
- **Boundary:** a marketing CI step fails any `content/guide-*` PR that touches a path outside `src/content/guides/**` or guide assets.
- **Backstop:** a branch-protection rule on marketing `main` requiring the `check` job. The owner sets it once.
- **One-time build:** the guides collection, routes, hreflang, sitemap rule, Article JSON-LD and footer link. This is the factory's first marketing issue, and the owner reviews it because it touches layouts and config.

## Tuning loop (Retro)

- **What counts as a miss:**
  - a revert
  - an issue that ends `agent-blocked`
  - an owner-applied `factory-miss` label on an issue or PR (the owner's "this slipped through")
- **Retro output** goes to `factory/retros/YYYY-MM-DD-<n>.md`:
  - what slipped
  - the error class
  - which gate should have caught it
  - why it didn't
  - a proposed change: a new mechanical test, a sharper skill rule or example, a gate trigger, or reviewer wording.
- The proposal ships as a PR touching `factory/` or `.claude/`. That path is protected, so the owner approves every change to how the factory works.
- The weekly digest lists the misses by class, so recurring classes are visible.

## Notifications

- **Immediate Mac notification** (`osascript -e 'display notification …'`, no dependency) for:
  - a protected PR ready for the owner
  - `agent-blocked`
  - a revert
  - a smoke failure
  - a retro PR
  - the usage-limit pause lasting more than 6 h
- **Daily digest email** at 08:00 local time:
  - merged PRs
  - PRs waiting on the owner
  - blocked items
  - proposals
  - misses
  - usage-limit pauses
  - gate pass and fail counts

  It is sent through the Gmail connector available to Claude Code. If the connector is unavailable in headless mode, the digest is written to `~/factory/digests/YYYY-MM-DD.md` and the Mac notification links to it. Plan task 1 verifies which path works.
- GitHub comments still carry #112's `<!-- wizbot -->` marker. The factory and the owner share one `gh` login, so GitHub's own notifications cannot be relied on.

## Safety rules (kept from #112, plus new ones)

- **From #112:**
  - The factory never force-pushes and never commits to `main` directly.
  - It never edits protected paths without the PR waiting for the owner.
  - It never puts GitHub-sourced text into shell commands; that text is wrapped as untrusted data.
- **No secrets in commits, PRs or issues.** The app repo is public. Security findings go to the private S3 audit bucket, never into issues, following the existing security process.
- **Legal text** is protected, and copy about legal terms is checked by copy-truth.
- **No new runtime dependencies** without the owner's approval; the factory proposes them as issues.
- **Kill switch:** `touch ~/factory/STOP` makes every tick exit immediately.

## Testing the factory

- `factory/state.py` and `select_gates.py` get pytest unit tests: snapshot fixtures go in, the chosen action or gate set comes out.
- Each gate gets a fixture diff that must trigger it and one that must not.
- The architecture tests are run against a planted violation to prove they fail.
- The first live runs work through a sequence of small `agent-ready` issues, one exercising each action, with the factory in `--dry-run` mode for its first 3 ticks. Dry-run prints the chosen action and selected gates and changes nothing.

## Rollout order

1. The factory core:
   - revive #112's agents and workflows
   - `tick.sh` and the LaunchAgent
   - `state.py` with Merge, Verify and Smoke
   - notifications
   - the kill switch
   - dry run
2. The architecture skill and the layering tests, with the allowlist and its cleanup proposals.
3. The gates and the selector, including the axe and Lighthouse tooling and baselines.
4. The Retro loop.
5. The marketing repo: its skill, CI boundary check, branch protection (owner), the one-time guides build, then the Guide action.

Each step is its own PR, protected because it touches `.claude/` or `factory/`, merged by the owner.

## Non-goals

- Running in the cloud or 24/7. The Mac runtime is the decision; moving later means wrapping `tick.sh` in a GitHub Actions job with `CLAUDE_CODE_OAUTH_TOKEN`.
- Refactoring existing code up front.
- Deciding the product roadmap. Proposals always wait for the owner.
- A dashboard. GitHub, the digest and `factory/retros/` are the record.

## Risks

- **The owner's usage window.** The factory shares it with the owner's own sessions. Mitigated by the idle check and the pause on the limit. If the factory still starves the owner, the next step is quiet hours in `config.toml`.
- **Mac asleep or closed.** Work is delayed, never lost; state is durable in git and GitHub.
- **Merge-on-green trust.** The protected list, the smoke check plus automatic revert, and the Retro loop together bound the damage. The protected list can grow through retros.
- **The Gmail connector in headless mode.** Unverified; the file fallback is defined above.
