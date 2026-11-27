/**
 * Conversation flow editor helpers (decision 102): the default flow as an
 * editable starting point, a mirror of the server's validation
 * (backend/reservations/flows.py `flow_problems`, same codes) for inline
 * problems before saving, the diagram's layered layout, and the editor's
 * state reducer. The server's 422 `invalid_flow` stays the authority.
 */
import type { Flow, FlowChannel, FlowOption, FlowProblem, FlowStep, FlowStepKind } from "../types/reservations";

export const MAX_STEPS = 30;
export const MAX_TEXT = 500;
export const MAX_LABEL = 80;
export const MAX_TITLE = 80;
export const MIN_OPTIONS = 2;
export const MAX_OPTIONS = 6;
const STEP_ID = /^[a-z0-9][a-z0-9_-]{0,39}$/;
// Controls (Cc), format characters (Cf: bidi overrides and isolates,
// zero-width space, word joiner, BOM) and the line and paragraph separators
// (Zl, Zp), as the server rejects them. ZWNJ, ZWJ, LRM and RLM (U+200C to
// U+200F) are allowed: Persian, Urdu, Arabic and Indic text and ZWJ emoji need them.
const CONTROL = /(?![\u200c-\u200f])[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u;
const TERMINAL: ReadonlySet<FlowStepKind> = new Set(["book", "handoff", "end", "change_or_cancel"]);

/**
 * Decision 100's default flow as an editable custom flow. The texts are what
 * the host says, so they are English like the host (it replies in English only).
 */
/** A save the server refused because the flows changed since they were loaded (409 flow_conflict). */
export function isFlowConflict(status: number, data: unknown): boolean {
  return (
    status === 409 && typeof data === "object" && data !== null && (data as { code?: unknown }).code === "flow_conflict"
  );
}

export function defaultFlow(): Flow {
  return {
    version: 1,
    start: "greet",
    steps: [
      {
        id: "greet",
        title: "Table or event",
        kind: "choice",
        prompt: "Are you booking a table, or is this about an event or private party?",
        options: [
          { label: "Regular table", next: "when" },
          { label: "Event / private party", next: "event" },
          { label: "Change or cancel a booking", next: "change" },
        ],
      },
      {
        id: "when",
        title: "Date, time and party size",
        kind: "ask",
        prompt: "What day and roughly what time, and for how many people?",
        collect: "date_time",
        next: "book",
      },
      { id: "book", title: "Book the table", kind: "book", next: null },
      { id: "event", title: "Event to staff", kind: "handoff", reason: "event" },
      { id: "change", title: "Change or cancel", kind: "change_or_cancel" },
    ],
  };
}

/** [field, target] for every edge out of the step. */
export function stepTargets(step: FlowStep): [string, string][] {
  switch (step.kind) {
    case "choice":
      return step.options.map((o, i) => [`options.${i}.next`, o.next]);
    case "ask":
    case "say":
      return [["next", step.next]];
    case "book":
      return step.next == null ? [] : [["next", step.next]];
    default:
      return [];
  }
}

/** [field, text, limit, required] for every text field of the step. */
function stepTexts(step: FlowStep): [string, string, number, boolean][] {
  const out: [string, string, number, boolean][] = [["title", step.title, MAX_TITLE, false]];
  if (step.kind === "ask" || step.kind === "choice") out.push(["prompt", step.prompt, MAX_TEXT, true]);
  if (step.kind === "choice") step.options.forEach((o, i) => out.push([`options.${i}.label`, o.label, MAX_LABEL, true]));
  if (step.kind === "say" || step.kind === "end") out.push(["message", step.message, MAX_TEXT, true]);
  if (step.kind === "handoff") out.push(["reason", step.reason, MAX_LABEL, true]);
  return out;
}

const problem = (code: string, step: string | null = null, field: string | null = null): FlowProblem => ({
  code,
  step,
  field,
});

/** Every problem with the flow; [] when valid. Cycles are allowed. */
export function flowProblems(flow: Flow): FlowProblem[] {
  const out: FlowProblem[] = [];
  if (flow.steps.length > MAX_STEPS) out.push(problem("too_many_steps"));
  const seen = new Set<string>();
  for (const s of flow.steps) {
    if (!STEP_ID.test(s.id)) out.push(problem("bad_id", s.id, "id"));
    if (seen.has(s.id)) out.push(problem("duplicate_id", s.id, "id"));
    seen.add(s.id);
  }
  if (!seen.has(flow.start)) out.push(problem("missing_start", null, "start"));
  for (const s of flow.steps) {
    if (s.kind === "choice" && (s.options.length < MIN_OPTIONS || s.options.length > MAX_OPTIONS))
      out.push(problem("bad_option_count", s.id, "options"));
    for (const [field, target] of stepTargets(s)) if (!seen.has(target)) out.push(problem("unknown_target", s.id, field));
    for (const [field, value, limit, required] of stepTexts(s)) {
      if (required && value.trim() === "") out.push(problem("empty_text", s.id, field));
      if (value.length > limit) out.push(problem("too_long", s.id, field));
      if (CONTROL.test(value)) out.push(problem("control_chars", s.id, field));
    }
  }
  if (!flow.steps.some((s) => TERMINAL.has(s.kind))) out.push(problem("no_terminal"));
  if (seen.has(flow.start)) {
    const reached = reachable(flow);
    const reported = new Set<string>();
    for (const s of flow.steps) {
      if (!reached.has(s.id) && !reported.has(s.id)) {
        reported.add(s.id);
        out.push(problem("unreachable", s.id));
      }
    }
  }
  return out;
}

/** Step ids by distance from the start (breadth first); missing targets are skipped. */
function depths(flow: Flow): Map<string, number> {
  const byId = new Map(flow.steps.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  if (!byId.has(flow.start)) return depth;
  depth.set(flow.start, 0);
  const queue = [flow.start];
  while (queue.length > 0) {
    const id = queue.shift()!;
    for (const [, target] of stepTargets(byId.get(id)!)) {
      if (byId.has(target) && !depth.has(target)) {
        depth.set(target, depth.get(id)! + 1);
        queue.push(target);
      }
    }
  }
  return depth;
}

function reachable(flow: Flow): Set<string> {
  return new Set(depths(flow).keys());
}

// ── Diagram layout ──

export const NODE_W = 168;
export const NODE_H = 56;
const GAP_X = 32;
const GAP_Y = 48;
const PAD = 16;
/** Room on the right for arrows that curve round the side (back links, self-loops). */
const RIGHT_MARGIN = 40;

export interface LayoutNode {
  id: string;
  layer: number;
  x: number;
  y: number;
}

export interface LayoutEdge {
  from: string;
  to: string;
  /** The option label for a choice's edge. */
  label: string | null;
}

export interface FlowLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

/**
 * A simple layered layout: one row per distance from the start, steps in
 * stored order within a row, unreachable steps in a last row. Coordinates
 * are the node's top-left corner in SVG units.
 */
export function layoutFlow(flow: Flow): FlowLayout {
  const depth = depths(flow);
  const last = depth.size > 0 ? Math.max(...depth.values()) + 1 : 0;
  const rows = new Map<number, string[]>();
  for (const s of flow.steps) {
    if (flow.steps.findIndex((x) => x.id === s.id) !== flow.steps.indexOf(s)) continue; // duplicate id
    const layer = depth.get(s.id) ?? last;
    rows.set(layer, [...(rows.get(layer) ?? []), s.id]);
  }
  const widest = Math.max(1, ...[...rows.values()].map((r) => r.length));
  const inner = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X;
  const width = inner + RIGHT_MARGIN;
  const nodes: LayoutNode[] = [];
  for (const [layer, ids] of [...rows.entries()].sort((a, b) => a[0] - b[0])) {
    const rowWidth = ids.length * NODE_W + (ids.length - 1) * GAP_X;
    const left = (inner - rowWidth) / 2;
    ids.forEach((id, i) => nodes.push({ id, layer, x: left + i * (NODE_W + GAP_X), y: PAD + layer * (NODE_H + GAP_Y) }));
  }
  const ids = new Set(nodes.map((n) => n.id));
  const edges: LayoutEdge[] = [];
  for (const s of flow.steps) {
    if (s.kind === "choice") {
      for (const o of s.options) if (ids.has(o.next)) edges.push({ from: s.id, to: o.next, label: o.label });
    } else {
      for (const [, target] of stepTargets(s)) if (ids.has(target)) edges.push({ from: s.id, to: target, label: null });
    }
  }
  const layers = rows.size === 0 ? 0 : Math.max(...rows.keys()) + 1;
  const height = PAD * 2 + Math.max(1, layers) * NODE_H + Math.max(0, layers - 1) * GAP_Y;
  return { nodes, edges, width, height };
}

/** A step's label in dropdowns and the diagram: its title, or "3. Untitled step". */
export function stepLabel(flow: Flow, id: string, untitled: string): string {
  const index = flow.steps.findIndex((s) => s.id === id);
  const title = index >= 0 ? flow.steps[index].title.trim() : "";
  return title || `${index + 1}. ${untitled}`;
}

// ── Editor state ──

/**
 * The flow per channel (null = default), and the custom drafts set aside
 * by switching to the default, so switching back loses no unsaved edits.
 */
export type FlowEditorState = Record<FlowChannel, Flow | null> & {
  stash?: Partial<Record<FlowChannel, Flow>>;
};

type StepPatch = Partial<{ title: string; prompt: string; collect: string; next: string | null; message: string; reason: string }>;

export type FlowAction =
  | { type: "load"; state: FlowEditorState }
  | { type: "useDefault"; channel: FlowChannel }
  | { type: "useCustom"; channel: FlowChannel }
  | { type: "loadDefault"; channel: FlowChannel }
  | { type: "copyWebToVoice" }
  | { type: "addStep"; channel: FlowChannel; kind: FlowStepKind }
  | { type: "removeStep"; channel: FlowChannel; id: string }
  | { type: "moveStep"; channel: FlowChannel; id: string; delta: number }
  | { type: "updateStep"; channel: FlowChannel; id: string; patch: StepPatch }
  | { type: "changeKind"; channel: FlowChannel; id: string; kind: FlowStepKind }
  | { type: "addOption"; channel: FlowChannel; id: string }
  | { type: "removeOption"; channel: FlowChannel; id: string; index: number }
  | { type: "updateOption"; channel: FlowChannel; id: string; index: number; patch: Partial<FlowOption> }
  | { type: "setStart"; channel: FlowChannel; id: string };

/** A step of `kind` with empty fields, keeping id and title. */
export function blankStep(id: string, title: string, kind: FlowStepKind): FlowStep {
  switch (kind) {
    case "ask":
      return { id, title, kind, prompt: "", collect: "free_text", next: "" };
    case "choice":
      return {
        id,
        title,
        kind,
        prompt: "",
        options: [
          { label: "", next: "" },
          { label: "", next: "" },
        ],
      };
    case "book":
      return { id, title, kind, next: null };
    case "change_or_cancel":
      return { id, title, kind };
    case "say":
      return { id, title, kind, message: "", next: "" };
    case "handoff":
      return { id, title, kind, reason: "" };
    case "end":
      return { id, title, kind, message: "" };
  }
}

function freshId(flow: Flow): string {
  const used = new Set(flow.steps.map((s) => s.id));
  let n = flow.steps.length + 1;
  while (used.has(`step-${n}`)) n += 1;
  return `step-${n}`;
}

/** A step with every link to `id` cleared (an unset dropdown). */
function unlink(step: FlowStep, id: string): FlowStep {
  switch (step.kind) {
    case "choice":
      return { ...step, options: step.options.map((o) => (o.next === id ? { ...o, next: "" } : o)) };
    case "ask":
    case "say":
      return step.next === id ? { ...step, next: "" } : step;
    case "book":
      return step.next === id ? { ...step, next: null } : step;
    default:
      return step;
  }
}

function mapStep(flow: Flow, id: string, fn: (s: FlowStep) => FlowStep): Flow {
  return { ...flow, steps: flow.steps.map((s) => (s.id === id ? fn(s) : s)) };
}

function editFlow(flow: Flow, action: FlowAction): Flow {
  switch (action.type) {
    case "addStep": {
      const id = freshId(flow);
      return { ...flow, steps: [...flow.steps, blankStep(id, "", action.kind)] };
    }
    case "removeStep": {
      const steps = flow.steps.filter((s) => s.id !== action.id).map((s) => unlink(s, action.id));
      const start = flow.start === action.id ? (steps[0]?.id ?? "") : flow.start;
      return { ...flow, start, steps };
    }
    case "moveStep": {
      const from = flow.steps.findIndex((s) => s.id === action.id);
      const to = from + action.delta;
      if (from < 0 || to < 0 || to >= flow.steps.length) return flow;
      const steps = [...flow.steps];
      const [moved] = steps.splice(from, 1);
      steps.splice(to, 0, moved);
      return { ...flow, steps };
    }
    case "updateStep":
      return mapStep(flow, action.id, (s) => ({ ...s, ...action.patch }) as FlowStep);
    case "changeKind":
      return mapStep(flow, action.id, (s) => (s.kind === action.kind ? s : blankStep(s.id, s.title, action.kind)));
    case "addOption":
      return mapStep(flow, action.id, (s) =>
        s.kind === "choice" ? { ...s, options: [...s.options, { label: "", next: "" }] } : s,
      );
    case "removeOption":
      return mapStep(flow, action.id, (s) =>
        s.kind === "choice" ? { ...s, options: s.options.filter((_, i) => i !== action.index) } : s,
      );
    case "updateOption":
      return mapStep(flow, action.id, (s) =>
        s.kind === "choice"
          ? { ...s, options: s.options.map((o, i) => (i === action.index ? { ...o, ...action.patch } : o)) }
          : s,
      );
    case "setStart":
      return { ...flow, start: action.id };
    default:
      return flow;
  }
}

const clone = (flow: Flow): Flow => JSON.parse(JSON.stringify(flow)) as Flow;

export function flowReducer(state: FlowEditorState, action: FlowAction): FlowEditorState {
  switch (action.type) {
    case "load":
      return action.state;
    case "useDefault": {
      const draft = state[action.channel];
      if (!draft) return state;
      return { ...state, [action.channel]: null, stash: { ...state.stash, [action.channel]: draft } };
    }
    case "useCustom":
      return state[action.channel]
        ? state
        : { ...state, [action.channel]: state.stash?.[action.channel] ?? defaultFlow() };
    case "loadDefault":
      return { ...state, [action.channel]: defaultFlow() };
    case "copyWebToVoice":
      return { ...state, voice: state.web ? clone(state.web) : null };
    default: {
      const flow = state[action.channel];
      return flow ? { ...state, [action.channel]: editFlow(flow, action) } : state;
    }
  }
}
