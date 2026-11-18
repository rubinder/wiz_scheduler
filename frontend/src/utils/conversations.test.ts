import { describe, expect, it } from "vitest";
import type { AgentEventOut } from "../types/reservations";
import { contentText, formatUsd, latencyOf, prettyJson, transcriptItems } from "./conversations";

function ev(seq: number, type: string, payload: Record<string, unknown>, extra: Partial<AgentEventOut> = {}): AgentEventOut {
  return {
    seq,
    type,
    payload,
    latency_ms: {},
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    cost_usd: "0.000000",
    created_at: "2030-01-15T23:00:00+00:00",
    ...extra,
  };
}

describe("contentText", () => {
  it("joins text blocks and skips other block types", () => {
    expect(
      contentText([
        { type: "thinking", thinking: "hmm" },
        { type: "text", text: "Hello" },
        { type: "tool_use", id: "t", name: "find_slots", input: {} },
        { type: "text", text: "there" },
      ]),
    ).toBe("Hello\nthere");
  });

  it("drops the per-turn context line only when asked", () => {
    const content = [
      { type: "text", text: "[Now: 2030-01-15 18:00, Tuesday]" },
      { type: "text", text: "Table for two?" },
    ];
    expect(contentText(content, true)).toBe("Table for two?");
    expect(contentText(content)).toContain("[Now:");
  });

  it("accepts a plain string and tolerates junk", () => {
    expect(contentText("hi")).toBe("hi");
    expect(contentText(null)).toBe("");
    expect(contentText([null, 3, { type: "text", text: 5 }])).toBe("");
  });
});

describe("latencyOf", () => {
  it("sums numeric parts and is null when none", () => {
    expect(latencyOf({ llm: 400, tools: 20 })).toBe(420);
    expect(latencyOf({})).toBeNull();
    expect(latencyOf({ llm: "x" })).toBeNull();
    expect(latencyOf(null)).toBeNull();
  });
});

describe("transcriptItems", () => {
  it("maps events in seq order to transcript items", () => {
    const items = transcriptItems([
      ev(3, "tool_call", { tool_use_id: "t1", name: "find_slots", input: { party_size: 2 } }),
      ev(1, "user_turn", { content: [{ type: "text", text: "[Now: x]" }, { type: "text", text: "Table for two?" }] }),
      ev(2, "assistant_turn", { content: [{ type: "text", text: "Sure." }] }, { latency_ms: { llm: 420 }, cost_usd: "0.000150" }),
      ev(4, "tool_result", { content: [{ type: "tool_result", content: "{}" }] }, { latency_ms: { tools: 12 } }),
      ev(5, "action", { action: "confirm_hold", result: {}, reply: "You're booked." }),
      ev(6, "handoff", { reason: "diner_requested" }),
      ev(7, "error", { error: "internal" }),
      ev(8, "mystery", {}),
    ]);
    expect(items.map((i) => i.kind)).toEqual(["diner", "host", "tool", "toolResult", "action", "handoff", "error"]);
    expect(items[0]).toMatchObject({ text: "Table for two?", latencyMs: null });
    expect(items[1]).toMatchObject({ text: "Sure.", latencyMs: 420, costUsd: "0.000150" });
    expect(items[2]).toMatchObject({ name: "find_slots", detail: prettyJson({ party_size: 2 }) });
    expect(items[3]).toMatchObject({ latencyMs: 12 });
    expect(items[4]).toMatchObject({ name: "confirm_hold", text: "You're booked." });
    expect(items[5]).toMatchObject({ reason: "diner_requested" });
    expect(items[6]).toMatchObject({ reason: "internal" });
  });

  it("keeps markup in model text as literal text", () => {
    const [item] = transcriptItems([ev(1, "assistant_turn", { content: [{ type: "text", text: "<b>hi</b>" }] })]);
    expect(item).toMatchObject({ text: "<b>hi</b>" });
  });
});

describe("formatUsd", () => {
  it("trims trailing zeros but keeps two decimals", () => {
    expect(formatUsd("0.000150")).toBe("$0.00015");
    expect(formatUsd("0.000000")).toBe("$0.00");
    expect(formatUsd("1.250000")).toBe("$1.25");
    expect(formatUsd("3")).toBe("$3.00");
    expect(formatUsd("bad")).toBe("$bad");
  });
});
