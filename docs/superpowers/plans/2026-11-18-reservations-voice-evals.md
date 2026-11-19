# AI Host Voice Evals and Tuning Implementation Plan (step 8 of 11)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The evaluation suite measures the voice channel end to end. Diner
lines pass through a speech-recognition stage, which is either a
deterministic noisy-transcript model or real Polly→Lex audio. Agent replies
pass through the real bridge and SSML rendering. The run records first-try
understanding and per-stage latency, and voice tuning knobs can be compared
run against run.

**Architecture:** A `VoiceChannel` adapter sits in the eval runner and
drives the same turn path a phone call uses. Bridge logic is reused in
process: `voice_bridge` turn handling plus `infra/voice/bridge/ssml.render`.
There is no HTTP server. A `Recognizer` protocol turns a diner's text line
into what the agent hears:

- `ExactRecognizer`: text passes through unchanged. CI uses it.
- `NoisyRecognizer`: deterministic, seeded confusions typical of telephone
  speech recognition. Runs offline in CI on the scripted diner; live with
  the simulated diner.
- `LexRecognizer`: Polly synthesizes the line as 8 kHz PCM, and Lex V2
  `RecognizeUtterance` runs against a configured bot alias. Live only,
  gated, cost-estimated.

Reports gain `first_try_understanding`, `asr_ms`, `tts_ms` and the
p50/p95 split. Voice tuning knobs live in the location's rules and in agent
settings. `run.py --compare` diffs two run files.

**Tech Stack:** Python. The eval package already exists
(`backend/reservations/evals/`). boto3 is already installed and is used only
in the live recognizer. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-17-reservation-agent-design.md`
section 8 (evaluation, voice runs) and section 12 step 8. This step closes
decision 35.

## Global Constraints

- CI stays offline and free. No AWS call and no Anthropic call without
  `--live`. The Lex recognizer is used only with
  `--recognizer lex --live`, and needs `EVAL_LEX_BOT_ID`,
  `EVAL_LEX_BOT_ALIAS_ID` and `EVAL_LEX_LOCALE` (default `en_US`).
- The cost guard covers voice too. The estimate adds Polly at
  `EVAL_POLLY_USD_PER_MILLION_CHARS = 16.00` (neural) and Lex at
  `EVAL_LEX_USD_PER_SPEECH_REQUEST = 0.004`, both settings. The existing
  refusal above $1.00 without `--yes` stands.
- Every run file keeps working with the existing reader, and new fields are
  additive. Run files go to `evals-runs/` (gitignored), per decision 36.
- `NoisyRecognizer` is deterministic for a given seed (default `seed=259`),
  so a noisy offline run is reproducible in CI.
- **First-try understanding** is the share of diner turns after which the
  agent did not ask the diner to repeat or clarify the same fact. A
  repeat/clarify is detected structurally, not with a model:
  - the agent's reply `expect`/`hints.elicit` asks again for a slot the
    diner just supplied, or
  - the reply is `voice.not_caught()` text.
- Turns into the bridge use the same seq dedupe and action matching as
  production. The eval must not bypass `voice.action_matches`.

## Review Focus

1. **A noisy run that turns "four" into "for" must not book a party of 4
   on an unconfirmed guess.** The checker's "no unconfirmed booking" rule
   still holds. Tested in Task 1.
2. **The Lex recognizer never runs in CI or without `--live`**, even when
   AWS credentials are present in the environment. Tested in Task 2.
3. **A recognizer failure (Lex error, empty transcript) counts as a
   not-understood turn, not a crash**, and the run continues. Tested in
   Task 2.
4. **`--compare` refuses run files whose scenario sets differ** unless
   `--allow-mismatch` is passed, because a pass rate over different sets is
   not comparable. Tested in Task 3.
5. **Voice tuning knobs default to the current behaviour.** A run with
   defaults reproduces the existing recordings exactly (CI replay stays
   green). Tested in Task 4.

---

### Task 1: Voice channel and noisy recognizer in the eval runner

**Files:**
- `backend/reservations/evals/voice.py`: `VoiceChannel`, `Recognizer`,
  `ExactRecognizer`, `NoisyRecognizer`.
- `runner.py`: when a scenario has channel `voice` and a recognizer is
  set, run turns through `VoiceChannel`.
- `run.py`: `--recognizer exact|noisy|lex` (default `exact`) and `--seed`.
- Tests.

**Interfaces:**
- `class Recognizer(Protocol): async def hear(self, text: str) -> Heard`
  - `Heard(text: str | None, asr_ms: int | None, tts_ms: int | None, error: str | None)`
- `NoisyRecognizer(seed: int, rate: float = 0.15)`:
  - word-level confusions:
    four↔for, two↔to/too, eight↔ate, Friday↔fried, seven↔heaven, the
    "teen"↔"ty" pairs, a dropped single digit in long digit runs, and
    confirmation-code letter confusions B↔D↔E↔P↔V, M↔N, S↔F.
  - Each eligible token flips with probability `rate`, using
    `random.Random(seed)` scoped per scenario and turn.
- `VoiceChannel.turn(...)` calls the voice bridge turn function in process
  with `{contact_id, dialed, caller, seq, text|action}` and returns the
  bridge reply. When the reply carries a `confirm_prompt`, a scripted or
  simulated diner answers yes or no as Lex confirmation (`action`), the
  same way the Lambda does.
- SSML: every agent reply is rendered through `ssml.render` and parsed as
  XML. A failure is recorded as a scenario error (`ssml_invalid`).

- [ ] Write tests:
  - exact recognizer through the voice channel reproduces the text-channel
    outcome on the voice scenarios with recordings;
  - noisy recognizer is deterministic per seed;
  - a "for"/"four" confusion never yields an unconfirmed booking (checker);
  - repeat detection on `not_caught` and on a re-elicited slot;
  - an SSML parse failure is recorded.
- [ ] Commit `feat(evals): voice channel through the bridge with a noisy recognizer (#259)`

### Task 2: Live Polly→Lex recognizer

**Files:**
- `backend/reservations/evals/lex_recognizer.py`
- `run.py`: config checks and the cost estimate.
- Tests with a fake boto3 client.

**Interfaces:**
- `LexRecognizer(bot_id, alias_id, locale, voice_id="Joanna", client_factory=...)`:
  1. `polly.synthesize_speech(Text=line, OutputFormat="pcm", SampleRate="8000", VoiceId, Engine="neural")`
     → `tts_ms`.
  2. `lexv2-runtime.recognize_utterance(botId, botAliasId, localeId, sessionId=<scenario run id>, requestContentType="audio/l16; rate=8000; channels=1", responseContentType="text/plain; charset=utf-8", inputStream=pcm)`
     → `asr_ms`.
  3. Decode `inputTranscript`. It is base64 and gzip compressed in
     RecognizeUtterance responses; handle both compressed and plain.
  - Errors → `Heard(text=None, error=<class name>)`.
- `run.py` refuses `--recognizer lex` without `--live`, or when the
  `EVAL_LEX_*` settings are missing (exit 2).
- [ ] Write tests:
  - request parameters;
  - transcript decoding (gzip+base64 and plain);
  - error → not understood, and the run continues;
  - refusal without `--live` or without config, with AWS credentials set in
    the environment;
  - the cost estimate includes Polly and Lex.
- [ ] Commit `feat(evals): live Polly-to-Lex recognizer for voice runs (#259)`

### Task 3: Voice metrics, run config and comparison

**Files:**
- `runner.py` and `store.py`: per-turn stage latencies and
  `first_try_understanding`.
- `run.py`: `--compare A B [--allow-mismatch]`.
- `report.py`, or the existing report function: a voice section.
- Tests.

**Interfaces:**
- **Run meta** gains:
  - `recognizer`
  - `seed`
  - `lex: {bot_id, alias_id, locale}`, recorded only for lex runs
  - `voice_knobs` (Task 4)
  - `prompt_version`: hash of the system prompt text, already present if
    step 4 records it; check first
- **Report voice section:**
  - first-try understanding %
  - p50/p95 for `asr`, `tts`, `llm`, `tools` and `turn_total`
  - repeats per scenario
  - SSML errors
- **Compare** prints a two-column table:
  - pass rate
  - first-try understanding
  - p50/p95 turn latency
  - cost per booking
  - deltas
  - per-scenario pass/fail flips
- [ ] Write tests:
  - metrics computed from a synthetic run file;
  - compare output on two fixture runs;
  - mismatch refusal;
  - an old run file without voice fields still reads.
- [ ] Commit `feat(evals): voice latency, first-try understanding and run comparison (#259)`

### Task 4: Voice tuning knobs

**Files:**
- `backend/reservations/agent/prompt.py`: voice section.
- `backend/reservations/rules.py`: voice settings.
- `run.py`: `--knob key=value` overrides recorded in meta.
- Tests.

**Knobs** (all default to current behaviour):
- `voice_reply_max_sentences` (int, default 0 = unlimited). The prompt asks
  for at most N sentences on voice.
- `voice_read_back` (bool, default false). Before confirming, the prompt
  asks the agent to read back numbers it heard (party size, time, phone)
  in one sentence.
- `voice_confirm_style` (`"question"` | `"statement"`, default
  `"question"`). Changes the server-built `confirm_prompt` phrasing only.

**Interfaces:**
- Knobs live in `RulesSchema.voice` (round-trip through the rules editor is
  not needed in this step: they stay API-only and are documented).
- The eval `--knob` override applies to the seeded location's rules.

- [ ] Write tests:
  - defaults leave the prompt text byte-identical, so CI replay stays green;
  - each knob changes the prompt or `confirm_prompt` as specified;
  - overrides are recorded in run meta.
- [ ] Commit `feat(evals): voice tuning knobs with run-recorded overrides (#259)`

### Task 5: Docs and the tuning procedure

- Decisions log:
  - decision 35 is closed by this step;
  - the noisy recognizer model and why it is deterministic;
  - first-try understanding is detected structurally;
  - the live tuning round is the owner's to run, because it spends money.
    Give an estimate per run of the 11 scenarios: Claude about $X per
    `cost_estimate`, Polly/Lex about cents.
- `docs/reservations-evals.md`, or extend the existing eval README if there
  is one:
  - how to run exact, noisy and lex;
  - how to compare;
  - the recommended tuning round: baseline, then `voice_reply_max_sentences=2`,
    then `voice_read_back=true`, each noisy-live and lex-live, then compare
    and record results in the decisions log.
- Owner checklist: run the tuning round and record the results table.
- [ ] Commit `docs(evals): voice evaluation and the tuning round (#259)`
