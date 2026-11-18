/**
 * Turns an AI host conversation's event log into transcript items for the
 * manager view. Payloads are recorded model/diner data: everything here is
 * read defensively and rendered by the page as plain text, never HTML.
 */
import type { AgentEventOut } from "../types/reservations";

export type TranscriptItem =
  | { kind: "diner"; seq: number; text: string; latencyMs: number | null; costUsd: string }
  | { kind: "host"; seq: number; text: string; latencyMs: number | null; costUsd: string }
  | { kind: "tool"; seq: number; name: string; detail: string; latencyMs: number | null; costUsd: string }
  | { kind: "toolResult"; seq: number; detail: string; latencyMs: number | null; costUsd: string }
  | { kind: "action"; seq: number; name: string; text: string; latencyMs: number | null; costUsd: string }
  | { kind: "handoff"; seq: number; reason: string; latencyMs: number | null; costUsd: string }
  | { kind: "error"; seq: number; reason: string; latencyMs: number | null; costUsd: string };

// The per-turn "[Now: 2030-01-15 18:00, Tuesday]" line core.py prepends to
// each diner turn: context for the model, not something the diner typed.
const CONTEXT_LINE = /^\[Now: [^\]]*\]$/;

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** The text blocks of a payload's `content` (a string or a list of blocks), joined. */
export function contentText(content: unknown, dropContextLine = false): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((b): b is { type: string; text: string } => {
      if (!b || typeof b !== "object") return false;
      const block = b as { type?: unknown; text?: unknown };
      return block.type === "text" && typeof block.text === "string";
    })
    .map((b) => b.text)
    .filter((t) => !(dropContextLine && CONTEXT_LINE.test(t.trim())))
    .join("\n")
    .trim();
}

/** Total milliseconds recorded on an event (llm and/or tools), or null when none. */
export function latencyOf(latency: Record<string, unknown> | null | undefined): number | null {
  const values = Object.values(latency ?? {}).filter(
    (v): v is number => typeof v === "number" && Number.isFinite(v),
  );
  return values.length ? values.reduce((a, b) => a + b, 0) : null;
}

/** Pretty JSON for a collapsed tool panel; never throws. */
export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? null, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

export function transcriptItems(events: AgentEventOut[]): TranscriptItem[] {
  const out: TranscriptItem[] = [];
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const base = { seq: e.seq, latencyMs: latencyOf(e.latency_ms), costUsd: e.cost_usd };
    switch (e.type) {
      case "user_turn":
        out.push({ kind: "diner", ...base, text: contentText(p.content, true) });
        break;
      case "assistant_turn":
        // tool_use blocks are recorded again as tool_call events; only text here.
        out.push({ kind: "host", ...base, text: contentText(p.content) });
        break;
      case "tool_call":
        out.push({ kind: "tool", ...base, name: str(p.name), detail: prettyJson(p.input) });
        break;
      case "tool_result":
        out.push({ kind: "toolResult", ...base, detail: prettyJson(p.content) });
        break;
      case "action":
        out.push({ kind: "action", ...base, name: str(p.action), text: str(p.reply) });
        break;
      case "handoff":
        out.push({ kind: "handoff", ...base, reason: str(p.reason) });
        break;
      case "error":
        out.push({ kind: "error", ...base, reason: str(p.error) });
        break;
      default:
        break;
    }
  }
  return out;
}

/** "$0.000150" -> "$0.00015"; keeps at least two decimals. Input is the API's decimal string. */
export function formatUsd(cost: string): string {
  const m = /^(-?\d+)(?:\.(\d*))?$/.exec(cost.trim());
  if (!m) return `$${cost}`;
  const frac = (m[2] ?? "").replace(/0+$/, "").padEnd(2, "0");
  return `$${m[1]}.${frac}`;
}
