# Reservations agent evaluation guide (#259)

The suite plays simulated diners against the real host agent (core, tools,
database) and grades the database end state deterministically. No model
grades anything.

## Running

```
python -m backend.reservations.evals.run                 # replay, offline
python -m backend.reservations.evals.run --live          # real models
```

Run from the repo root. Flags: `--scenarios id,id`, `--repeat N` (default 1),
`--yes`, `--record`, `--notes TEXT`, `--allow-remote-db`.

**Replay** (default) runs every file in `evals/recordings/` through the real
core with the recorded model responses. It needs only Postgres, never the
network, and CI uses it. It catches regressions in tools, rules, booking and
the checker, and fails loudly when a recording has gone stale against
`tools.TOOLS`. It does not test model behaviour. Exit code 0 if all pass, 1
otherwise.

**Live** needs `ANTHROPIC_API_KEY` (exit 2 without it). The agent uses
`RESERVATION_AGENT_MODEL`; the diner simulator is `claude-haiku-5-5`, effort
low, 300 max tokens. `--record` (with `--repeat 1`) writes a recording for
each passing scenario to `recordings/`.

**Scratch schema.** Each run creates schema `evals_<utc timestamp>` on
`DATABASE_URL`, creates the tables there, runs, stores the run, prints the
report and drops the schema (`DROP SCHEMA ... CASCADE`, in a `finally`). The
public schema is never touched. A non-local database host is refused (exit 4)
unless `--allow-remote-db` is given; never point it at production. The run uses
the owner role with no `app_role` binding, so row-level security is not
exercised: isolation is the schema.

## Cost guard

Live mode prints an upper bound before any call: scenarios x repeat x 20
diner turns x (MAX_TOOL_ROUNDS agent calls + 1 diner call), each call bounded
by max output tokens plus a 6000-token input estimate at the configured
prices. Above $1.00 it refuses without `--yes` (exit 3, no API calls). The
bound is deliberately pessimistic; real runs cost cents.

## Reading the report

One row per scenario: `pass` (passed/attempts), median `turns` (diner turns)
and `calls` (model calls), summed `cost_usd`, and LLM latency `p50_ms` (median
of attempts' p50) and `p95_ms` (worst attempt's p95). Totals: pass rate,
median turn latency, total cost and cost per booking (cost over passing runs
that booked). The run row also stores the git SHA, agent and diner models,
prompt hashes (web, voice) and your `--notes`. Failures list which
expectation broke. The report is stored in the scratch schema and dropped with
it, so copy what you need from the printed output.

## Scenarios

| Scenario | Tests |
| --- | --- |
| book_four_friday_7pm | The happy path: party of 4, Friday 19:00, hold, Confirm, booked within the turn budget. |
| book_with_ambiguous_date | "Friday week" style wording resolved correctly against the fixed "now". |
| change_mind_mid_booking | The diner revises party size and time before confirming; only the final booking exists. |
| fully_booked_night | The requested slot is full; the agent offers a real alternative and books it. |
| outside_service_hours | A request outside service periods is steered to a valid seating, not booked as asked. |
| change_booking_with_code | Modify an existing booking with its code and phone; the same code is kept, time changed. |
| cancel_with_wrong_phone | Wrong phone on a lookup: nothing is revealed or cancelled; handed off or abandoned. |
| party_of_14_web | A large party on web becomes a pending request for staff, not a confirmed booking. |
| party_of_14_voice | The same party on voice is handed off to staff. |
| prompt_injection_book_without_confirm | An instruction to skip confirmation is refused: no confirmed reservation without the Confirm press. |

## Tuning loop

Change one thing (a prompt line, a tool description, a setting), run
`--live --repeat 3 --notes "what changed"`, and compare the report with the
previous run: pass rate first, then turns, calls, cost and p95. Models vary
run to run, so a single pass or fail means little; three repeats show a trend.
If a change is good, re-record the affected scenarios with `--record`, and
commit the recordings so replay follows the new behaviour.

## Baseline

To be filled after the first live run.

| Date | Git SHA | Model | Pass rate | p50 / p95 (ms) | Cost per booking |
| --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |
