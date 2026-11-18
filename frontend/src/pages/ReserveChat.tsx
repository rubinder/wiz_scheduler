import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  getLocationInfo, sendTurn, startConversation,
  type Hold, type LocationInfo, type TurnInput,
} from "../api/publicReservations";
import { ACTION_LABELS, actionsFor, inputHint, reduceTurn, type ChatMessage } from "../utils/chat";

// Diner-facing copy is English only and deliberately not in i18n (the AI host
// agent is English-only by design). Nothing is persisted: no token storage.

const MAX_CHARS = 1000;

export default function ReserveChat() {
  const { slug = "", locationId = "" } = useParams();
  const [info, setInfo] = useState<LocationInfo | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [expect, setExpect] = useState("text");
  const [hold, setHold] = useState<Hold | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [phone, setPhone] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [draft, setDraft] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const i = await getLocationInfo(slug, locationId);
        if (cancelled) return;
        setInfo(i);
        setMessages([{ from: "host", text: i.greeting }]);
        const c = await startConversation(slug, locationId);
        if (cancelled) return;
        setToken(c.conversation_token);
        setMessages((m) => [...m, { from: "host", text: c.reply_text }]);
        setExpect(c.expect);
      } catch {
        if (!cancelled) setLoadError(true);
      }
    })();
    return () => { cancelled = true; };
  }, [slug, locationId]);

  useEffect(() => {
    logRef.current?.scrollTo?.({ top: logRef.current.scrollHeight });
  }, [messages]);

  async function send(input: TurnInput, shown: string | null) {
    if (!token || sending || ended) return;
    setSending(true);
    setNotice(null);
    try {
      const out = await sendTurn(slug, locationId, token, input);
      setMessages((m) => reduceTurn(m, shown, out));
      setExpect(out.expect);
      setHold(out.hold);
      setCode(out.hold?.confirmation_code ?? null);
      setPhone(out.handoff?.phone ?? null);
      if (out.ended || out.handoff || out.expect === "handoff") setEnded(true);
      if (shown !== null) setDraft("");
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const detail = e instanceof ApiError ? (e.data as { code?: string } | undefined) : undefined;
      if (status === 409 && detail?.code === "turn_in_progress") setNotice("Still working on your last message…");
      else if (status === 429) setNotice("Too many messages, try again later");
      else if (status === 404) { setNotice("This chat has ended"); setEnded(true); }
      else setNotice("Something went wrong. Please try again.");
    } finally {
      setSending(false);
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (text) void send({ text }, text);
  }

  if (loadError) {
    return <main className="min-h-screen flex items-center justify-center p-4"><p>This booking page is not available.</p></main>;
  }

  const actions = ended ? [] : actionsFor(expect);
  const hint = inputHint(expect);
  const disabled = !token || sending || ended;

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col p-4">
      <h1 className="mb-3 text-xl font-semibold">{info?.location_name ?? "Reservations"}</h1>
      <div ref={logRef} role="log" aria-live="polite" aria-label="Conversation" className="flex-1 space-y-2 overflow-y-auto">
        {messages.map((m, i) => (
          <p key={i} className={m.from === "diner" ? "ms-auto w-fit max-w-[85%] rounded bg-blue-600 px-3 py-2 text-white" : "w-fit max-w-[85%] rounded bg-gray-100 px-3 py-2"}>
            {m.text}
          </p>
        ))}
      </div>
      {code && (
        <p className="my-3 text-center text-lg">Confirmation code: <strong className="text-2xl">{code}</strong></p>
      )}
      {phone && (
        <p className="my-3 text-center">Call us: <a className="underline" href={`tel:${phone}`}>{phone}</a></p>
      )}
      {hold && actions.length > 0 && (
        <p className="mt-3 text-center font-medium">{hold.local_date} at {hold.local_time}, party of {hold.party_size}</p>
      )}
      {actions.length > 0 && (
        <div className="my-2 flex flex-wrap justify-center gap-2">
          {actions.map((a) => (
            <button key={a} type="button" disabled={sending} onClick={() => void send({ action: a }, null)}
              className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
              {ACTION_LABELS[a]}
            </button>
          ))}
        </div>
      )}
      {notice && <p role="status" className="my-2 text-center text-sm text-red-700">{notice}</p>}
      <form onSubmit={submit} className="mt-2 flex gap-2">
        <label htmlFor="chat-input" className="sr-only">Your message</label>
        <input id="chat-input" className="flex-1 rounded border px-3 py-2" value={draft} maxLength={MAX_CHARS}
          disabled={disabled} placeholder={hint.placeholder} inputMode={hint.inputMode}
          onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
        <button type="submit" disabled={disabled || !draft.trim()} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">Send</button>
      </form>
    </main>
  );
}
