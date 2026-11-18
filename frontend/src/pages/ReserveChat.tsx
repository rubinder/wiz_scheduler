import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  getLocationInfo, sendTurn, startConversation,
  type Expect, type Hold, type LocationInfo, type Proposal, type TurnInput,
} from "../api/publicReservations";
import { actionLabel, actionsFor, inputHint, noticeForError, proposalText, reduceTurn, sanitizeTel, startEnded, startErrorFor, type ChatMessage } from "../utils/chat";

// Diner-facing copy is English only and deliberately not in i18n (the AI host
// agent is English-only by design). Nothing is persisted: no token storage.

const MAX_CHARS = 1000;

export default function ReserveChat() {
  const { slug = "", locationId = "" } = useParams();
  const [info, setInfo] = useState<LocationInfo | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [expect, setExpect] = useState<Expect>("text");
  const [hold, setHold] = useState<Hold | null>(null);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [phone, setPhone] = useState<string | null>(null);
  const [ended, setEnded] = useState(false);
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [startError, setStartError] = useState<{ message: string; retry: boolean } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const tokenRef = useRef<string | null>(null);
  const inFlight = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState("");
  const logRef = useRef<HTMLDivElement>(null);

  // A different page means a different chat: drop everything from the last one.
  useEffect(() => {
    tokenRef.current = null;
    inFlight.current = false;
    setInfo(null); setToken(null); setMessages([]); setExpect("text"); setHold(null); setProposal(null);
    setCode(null); setPhone(null); setEnded(false); setSending(false); setNotice(null);
    setDraft(""); setStartError(null); setAttempt(0);
    return () => { tokenRef.current = null; };
  }, [slug, locationId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setStartError(null);
      try {
        const i = await getLocationInfo(slug, locationId);
        if (cancelled) return;
        setInfo(i);
        const c = await startConversation(slug, locationId);
        if (cancelled) return;
        tokenRef.current = c.conversation_token;
        setToken(c.conversation_token);
        // The start's reply is the greeting: shown once (the header names the location).
        setMessages([{ from: "host", text: c.reply_text }]);
        setExpect(c.expect);
        if (startEnded(c)) setEnded(true);
      } catch (e) {
        if (!cancelled) setStartError(startErrorFor(e instanceof ApiError ? e.status : 0));
      }
    })();
    return () => { cancelled = true; };
  }, [slug, locationId, attempt]);

  useEffect(() => {
    logRef.current?.scrollTo?.({ top: logRef.current.scrollHeight });
  }, [messages]);

  async function send(input: TurnInput, shown: string | null) {
    const mine = tokenRef.current;
    if (!mine || inFlight.current || ended) return;
    inFlight.current = true;
    setSending(true);
    setNotice(null);
    try {
      const out = await sendTurn(slug, locationId, mine, input);
      if (tokenRef.current !== mine) return;
      setMessages((m) => reduceTurn(m, shown, out));
      setExpect(out.expect);
      setHold(out.hold);
      setProposal(out.proposal ?? null);
      setCode(out.hold?.confirmation_code ?? null);
      setPhone(out.handoff?.phone ?? null);
      if (out.ended || out.handoff || out.expect === "handoff") setEnded(true);
      if (shown !== null) setDraft("");
    } catch (e) {
      if (tokenRef.current !== mine) return;
      const status = e instanceof ApiError ? e.status : 0;
      const detail = e instanceof ApiError ? (e.data as { code?: string } | undefined) : undefined;
      const r = noticeForError(status, detail);
      setNotice(r.notice);
      if (r.ended) setEnded(true);
    } finally {
      if (tokenRef.current === mine) {
        inFlight.current = false;
        setSending(false);
        setTimeout(() => inputRef.current?.focus(), 0);
      }
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (text) void send({ text }, text);
  }

  if (startError && !info) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center gap-3 p-4">
        <p role="alert">{startError.message}</p>
        {startError.retry && <button type="button" onClick={() => setAttempt((n) => n + 1)} className="rounded bg-blue-600 px-4 py-2 text-white">Retry</button>}
      </main>
    );
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
        <p className="my-3 text-center">
          Call us: {sanitizeTel(phone) ? <a className="underline" href={`tel:${sanitizeTel(phone)}`}>{phone}</a> : phone}
        </p>
      )}
      {hold && actions.length > 0 && (
        <p className="mt-3 text-center font-medium">{hold.local_date} at {hold.local_time}, party of {hold.party_size}</p>
      )}
      {!hold && proposal && actions.length > 0 && (
        <p className="mt-3 text-center font-medium">{proposalText(proposal)}</p>
      )}
      {actions.length > 0 && (
        <div className="my-2 flex flex-wrap justify-center gap-2">
          {actions.map((a) => (
            <button key={a} type="button" disabled={sending} onClick={() => void send({ action: a }, null)}
              className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">
              {actionLabel(a, expect)}
            </button>
          ))}
        </div>
      )}
      {startError && (
        <p role="alert" className="my-2 text-center text-sm text-red-700">
          {startError.message}{" "}
          {startError.retry && <button type="button" className="underline" onClick={() => setAttempt((n) => n + 1)}>Retry</button>}
        </p>
      )}
      {notice && <p role="status" className="my-2 text-center text-sm text-red-700">{notice}</p>}
      <form onSubmit={submit} className="mt-2 flex gap-2">
        <label htmlFor="chat-input" className="sr-only">Your message</label>
        <input id="chat-input" ref={inputRef} className="flex-1 rounded border px-3 py-2" value={draft} maxLength={MAX_CHARS}
          disabled={disabled} placeholder={hint.placeholder} inputMode={hint.inputMode}
          onChange={(e) => setDraft(e.target.value)} autoComplete="off" />
        <button type="submit" disabled={disabled || !draft.trim()} className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50">Send</button>
      </form>
    </main>
  );
}
