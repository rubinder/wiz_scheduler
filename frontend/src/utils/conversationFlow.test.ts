import { describe, expect, it } from "vitest";
import type { Flow, FlowStep } from "../types/reservations";
import { defaultFlow, flowProblems, flowReducer, layoutFlow } from "./conversationFlow";
import type { FlowEditorState } from "./conversationFlow";

function flow(): Flow {
  return {
    version: 1,
    start: "greet",
    steps: [
      {
        id: "greet",
        title: "Greet",
        kind: "choice",
        prompt: "Table, or catering?",
        options: [
          { label: "Regular table", next: "when" },
          { label: "Catering", next: "staff" },
        ],
      },
      { id: "when", title: "When", kind: "ask", prompt: "When?", collect: "date_time", next: "book" },
      { id: "book", title: "Book", kind: "book", next: null },
      { id: "staff", title: "Staff", kind: "handoff", reason: "catering" },
    ],
  };
}

const codes = (f: Flow) => new Set(flowProblems(f).map((p) => p.code));
const step = (f: Flow, id: string) => f.steps.find((s) => s.id === id) as FlowStep;

describe("flowProblems (mirror of backend flows.flow_problems)", () => {
  it("accepts a well-formed flow and the default flow", () => {
    expect(flowProblems(flow())).toEqual([]);
    expect(flowProblems(defaultFlow())).toEqual([]);
  });

  it("reports a missing start, unknown targets and unreachable steps", () => {
    expect(codes({ ...flow(), start: "nope" })).toContain("missing_start");
    const f = flow();
    (step(f, "when") as { next: string }).next = "ghost";
    expect(flowProblems(f)).toContainEqual({ code: "unknown_target", step: "when", field: "next" });
    const g = flow();
    g.steps.push({ id: "orphan", title: "", kind: "end", message: "Bye" });
    expect(flowProblems(g).filter((p) => p.code === "unreachable").map((p) => p.step)).toEqual(["orphan"]);
  });

  it("needs a terminal step but allows cycles", () => {
    const f: Flow = {
      version: 1,
      start: "a",
      steps: [
        { id: "a", title: "", kind: "say", message: "Hi", next: "b" },
        { id: "b", title: "", kind: "ask", prompt: "Again?", collect: "free_text", next: "a" },
      ],
    };
    expect(codes(f)).toEqual(new Set(["no_terminal"]));
    const g = flow();
    (step(g, "when") as { next: string }).next = "greet";
    (step(g, "greet") as { options: { label: string; next: string }[] }).options.push({ label: "Book", next: "book" });
    expect(flowProblems(g)).toEqual([]);
  });

  it("checks option counts, text limits, empty text and control characters", () => {
    const f = flow();
    (step(f, "greet") as { options: unknown[] }).options = Array.from({ length: 7 }, () => ({ label: "x", next: "when" }));
    expect(codes(f)).toContain("bad_option_count");
    const g = flow();
    (step(g, "when") as { prompt: string }).prompt = "x".repeat(501);
    (step(g, "staff") as { reason: string }).reason = "";
    (step(g, "book") as { title: string }).title = "a\u0007b";
    expect(flowProblems(g)).toEqual(
      expect.arrayContaining([
        { code: "too_long", step: "when", field: "prompt" },
        { code: "empty_text", step: "staff", field: "reason" },
        { code: "control_chars", step: "book", field: "title" },
      ]),
    );
  });

  it("caps the flow at 30 steps", () => {
    const f = flow();
    for (let i = 0; i < 26; i++) f.steps.push({ id: `x${i}`, title: "", kind: "end", message: "x" });
    expect(codes(f)).not.toContain("too_many_steps");
    f.steps.push({ id: "x26", title: "", kind: "end", message: "x" });
    expect(codes(f)).toContain("too_many_steps");
  });
});

describe("defaultFlow", () => {
  it("is decision 100's flow: table or event first, events to staff", () => {
    const f = defaultFlow();
    const start = step(f, f.start);
    expect(start.kind).toBe("choice");
    const kinds = f.steps.map((s) => s.kind);
    expect(kinds).toEqual(expect.arrayContaining(["choice", "ask", "book", "handoff", "change_or_cancel"]));
    expect(f.steps.find((s) => s.kind === "handoff")).toMatchObject({ reason: "event" });
  });
});

describe("layoutFlow", () => {
  it("puts steps in layers by distance from the start, with one edge per target", () => {
    const out = layoutFlow(flow());
    const layer = Object.fromEntries(out.nodes.map((n) => [n.id, n.layer]));
    expect(layer).toEqual({ greet: 0, when: 1, staff: 1, book: 2 });
    expect(out.edges.map((e) => `${e.from}->${e.to}`).sort()).toEqual(["greet->staff", "greet->when", "when->book"]);
    const when = out.nodes.find((n) => n.id === "when")!;
    const staff = out.nodes.find((n) => n.id === "staff")!;
    expect(when.y).toBe(staff.y);
    expect(when.x).not.toBe(staff.x);
    expect(out.width).toBeGreaterThan(0);
    expect(out.height).toBeGreaterThan(0);
  });

  it("labels option edges and places unreachable steps in a last layer", () => {
    const f = flow();
    f.steps.push({ id: "orphan", title: "", kind: "end", message: "Bye" });
    const out = layoutFlow(f);
    expect(out.edges.find((e) => e.to === "staff")?.label).toBe("Catering");
    expect(out.nodes.find((n) => n.id === "orphan")?.layer).toBe(3);
  });

  it("ignores edges to missing steps and terminates on cycles", () => {
    const f = flow();
    (step(f, "when") as { next: string }).next = "greet";
    (step(f, "book") as { next: string | null }).next = "ghost";
    const out = layoutFlow(f);
    expect(out.edges.some((e) => e.to === "ghost")).toBe(false);
    expect(out.nodes).toHaveLength(4);
  });
});

describe("flowReducer", () => {
  const state = (): FlowEditorState => ({ web: null, voice: flow() });

  it("switches a channel between the default and a custom flow", () => {
    let s = flowReducer(state(), { type: "useCustom", channel: "web" });
    expect(s.web).toEqual(defaultFlow());
    s = flowReducer(s, { type: "useDefault", channel: "web" });
    expect(s.web).toBeNull();
  });

  it("copies the web flow to voice as an independent copy", () => {
    let s = flowReducer({ web: flow(), voice: null }, { type: "copyWebToVoice" });
    expect(s.voice).toEqual(s.web);
    s = flowReducer(s, { type: "updateStep", channel: "voice", id: "when", patch: { title: "Changed" } });
    expect(step(s.web!, "when").title).toBe("When");
  });

  it("adds a step with a fresh unique id", () => {
    const s = flowReducer(state(), { type: "addStep", channel: "voice", kind: "say" });
    const added = s.voice!.steps[s.voice!.steps.length - 1];
    expect(added.kind).toBe("say");
    expect(new Set(s.voice!.steps.map((x) => x.id)).size).toBe(5);
  });

  it("removes a step and clears every link to it", () => {
    const s = flowReducer(state(), { type: "removeStep", channel: "voice", id: "book" });
    expect(s.voice!.steps.map((x) => x.id)).toEqual(["greet", "when", "staff"]);
    expect(step(s.voice!, "when")).toMatchObject({ next: "" });
  });

  it("removing the start step moves the start to the first remaining step", () => {
    const s = flowReducer(state(), { type: "removeStep", channel: "voice", id: "greet" });
    expect(s.voice!.start).toBe("when");
  });

  it("reorders steps", () => {
    let s = flowReducer(state(), { type: "moveStep", channel: "voice", id: "book", delta: -1 });
    expect(s.voice!.steps.map((x) => x.id)).toEqual(["greet", "book", "when", "staff"]);
    s = flowReducer(s, { type: "moveStep", channel: "voice", id: "greet", delta: -1 });
    expect(s.voice!.steps[0].id).toBe("greet");
  });

  it("changes a step's kind, keeping id and title and filling the new kind's fields", () => {
    const s = flowReducer(state(), { type: "changeKind", channel: "voice", id: "when", kind: "choice" });
    expect(step(s.voice!, "when")).toEqual({
      id: "when",
      title: "When",
      kind: "choice",
      prompt: "",
      options: [
        { label: "", next: "" },
        { label: "", next: "" },
      ],
    });
  });

  it("edits, adds and removes choice options", () => {
    let s = flowReducer(state(), { type: "addOption", channel: "voice", id: "greet" });
    expect((step(s.voice!, "greet") as { options: unknown[] }).options).toHaveLength(3);
    s = flowReducer(s, { type: "updateOption", channel: "voice", id: "greet", index: 2, patch: { label: "Other", next: "staff" } });
    expect((step(s.voice!, "greet") as { options: unknown[] }).options[2]).toEqual({ label: "Other", next: "staff" });
    s = flowReducer(s, { type: "removeOption", channel: "voice", id: "greet", index: 0 });
    expect((step(s.voice!, "greet") as { options: { label: string }[] }).options.map((o) => o.label)).toEqual([
      "Catering",
      "Other",
    ]);
  });

  it("sets the start step", () => {
    const s = flowReducer(state(), { type: "setStart", channel: "voice", id: "when" });
    expect(s.voice!.start).toBe("when");
  });

  it("loads the default flow into a custom channel", () => {
    const s = flowReducer(state(), { type: "loadDefault", channel: "voice" });
    expect(s.voice).toEqual(defaultFlow());
  });
});
