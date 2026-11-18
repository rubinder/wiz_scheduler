import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import {
  eraseDinerData,
  exportDinerData,
  getConfig,
  getConversation,
  listConversations,
  markConversationSpam,
} from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { ConversationDetailOut, ConversationOut } from "../../types/reservations";
import { formatUsd, transcriptItems } from "../../utils/conversations";
import type { TranscriptItem } from "../../utils/conversations";
import { numberErrorKey } from "../../utils/numberRules";
import { errorCode, formatLocalTime, localDateString } from "../../utils/reservations";
import { border, text } from "../../theme";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
    </label>
  );
}

const fill = (template: string, values: Record<string, string | number>): string =>
  template.replace(/\{(\w+)\}/g, (m, k: string) => (k in values ? String(values[k]) : m));

/** Hand a downloaded file to the browser (revoked after a delay so Safari/Firefox finish reading it). */
function saveJson(data: unknown, filename: string): void {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * The AI host's conversations for one location and local day. Diner and
 * model text is rendered only as React text nodes, never as HTML.
 */
export default function ReservationsConversations() {
  const { t, lang } = useLanguage();
  const r = t.reservations;
  const c = r.conversations;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationsFailed, setLocationsFailed] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [date, setDate] = useState("");
  /** The location `date` was defaulted for; the list waits until it matches. */
  const [dateFor, setDateFor] = useState("");
  const [outcome, setOutcome] = useState("");
  const [outcomes, setOutcomes] = useState<string[]>([]);
  const [list, setList] = useState<ConversationOut[] | null>(null);
  const [listError, setListError] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetailOut | null>(null);
  const [detailError, setDetailError] = useState("");
  const [dinerPhone, setDinerPhone] = useState("");
  const [dinerBusy, setDinerBusy] = useState(false);
  const [dinerResult, setDinerResult] = useState("");
  const [dinerError, setDinerError] = useState("");
  const [spamBusy, setSpamBusy] = useState(false);
  /** Result of "Mark as spam" for the open conversation: a notice, or an error. */
  const [spamNotice, setSpamNotice] = useState("");
  const [spamError, setSpamError] = useState("");

  /** "location|date|outcome" being shown; responses for any other view are dropped. */
  const currentView = useRef("");
  const listSeq = useRef(0);
  const detailSeq = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  selectedIdRef.current = selectedId;

  const location = locations.find((l) => l.id === locationId) ?? null;
  const tz = location?.timezone ?? "UTC";

  const errorText = (e: unknown): string => {
    const detail = e instanceof ApiError ? e.data : null;
    const code = errorCode(detail);
    const own = numberErrorKey(code, detail);
    if (own) return r.numbers[own];
    return (code && (r.errors as Record<string, string>)[code]) || r.errors.generic;
  };

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setEnabled(cfg.enabled);
        setOutcomes(cfg.conversation_outcomes ?? []);
        if (!cfg.enabled) return;
        const locs = await listLocations();
        setLocations(locs);
        if (locs.length > 0) setLocationId(locs[0].id);
      } catch (e) {
        setEnabled(true);
        setLocationsFailed(true);
        setListError(errorText(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new location starts on that location's today.
  useEffect(() => {
    if (!location) return;
    setDate(localDateString(new Date(), location.timezone));
    setDateFor(location.id);
  }, [location]);

  useEffect(() => {
    const view = `${locationId}|${date}|${outcome}`;
    currentView.current = view;
    const seq = ++listSeq.current;
    detailSeq.current += 1;
    setList(null);
    setListError("");
    setSelectedId(null);
    setDetail(null);
    setDetailError("");
    if (!locationId || !date || dateFor !== locationId) return;
    const latest = () => seq === listSeq.current && currentView.current === view;
    listConversations(locationId, date, outcome || undefined)
      .then((out) => {
        if (latest()) setList(out);
      })
      .catch((e) => {
        if (latest()) setListError(errorText(e));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId, date, dateFor, outcome]);

  const open = async (id: string) => {
    const view = currentView.current;
    const seq = ++detailSeq.current;
    const latest = () => seq === detailSeq.current && currentView.current === view;
    setSelectedId(id);
    setDetail(null);
    setDetailError("");
    setSpamNotice("");
    setSpamError("");
    try {
      const out = await getConversation(id);
      if (latest()) setDetail(out);
    } catch (e) {
      if (latest()) setDetailError(errorText(e));
    }
  };

  const markSpam = async () => {
    if (!detail || spamBusy || !window.confirm(r.numbers.confirmMarkSpam)) return;
    const id = detail.id;
    setSpamBusy(true);
    setSpamNotice("");
    setSpamError("");
    try {
      const rule = await markConversationSpam(id);
      if (selectedIdRef.current !== id) return;
      setSpamNotice(rule.created ? r.numbers.markedSpam : r.numbers.alreadyBlocked);
    } catch (e) {
      if (selectedIdRef.current === id) setSpamError(errorText(e));
    } finally {
      setSpamBusy(false);
    }
  };

  const items = useMemo(() => transcriptItems(detail?.events ?? []), [detail]);

  const dinerAction = async (kind: "export" | "erase") => {
    const phone = dinerPhone.trim();
    const d = c.dinerData;
    if (!locationId || !phone || dinerBusy) return;
    if (kind === "erase" && !window.confirm(d.confirmErase)) return;
    setDinerBusy(true);
    setDinerResult("");
    setDinerError("");
    try {
      if (kind === "export") {
        const out = await exportDinerData(locationId, phone);
        saveJson(out, `diner-data-${locationId}-${phone.replace(/[^0-9]/g, "")}.json`);
        setDinerResult(fill(d.exported, { reservations: out.reservations.length, conversations: out.conversations.length }));
      } else {
        const out = await eraseDinerData(locationId, phone);
        const summary = fill(d.erased, {
          scrubbed: out.reservations_scrubbed,
          deleted: out.conversations_deleted,
          closed: out.conversations_closed_in_flight,
          busy: out.conversations_still_in_flight,
          lookups: out.lookup_failures_deleted,
        });
        const again = out.conversations_closed_in_flight + out.conversations_still_in_flight > 0;
        const rules = fill(r.numbers.erasedRules, {
          deleted: out.number_rules_deleted,
          remaining: out.company_rules_remaining,
        });
        setDinerResult([summary, rules, again ? d.eraseAgain : ""].filter(Boolean).join(" "));
      }
    } catch (e) {
      setDinerError(errorText(e));
    } finally {
      setDinerBusy(false);
    }
  };

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const outcomeName = (o: string | null): string =>
    o ? ((c.outcomes as Record<string, string>)[o] ?? o) : c.inProgress;
  const channelName = (ch: string): string => (c.channels as Record<string, string>)[ch] ?? ch;
  const time = (iso: string) => formatLocalTime(iso, tz, lang);
  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;

  const meta = (item: TranscriptItem): ReactNode => {
    const parts: string[] = [];
    if (item.latencyMs !== null) parts.push(fill(c.latency, { ms: item.latencyMs }));
    if (Number(item.costUsd) > 0) parts.push(formatUsd(item.costUsd));
    return parts.length ? <span className={`text-xs ${text.placeholder}`}>{parts.join(" · ")}</span> : null;
  };

  const bubble = (item: TranscriptItem): ReactNode => {
    switch (item.kind) {
      case "diner":
      case "host":
        return (
          <div className={item.kind === "diner" ? "me-8" : "ms-8"}>
            <div className={`flex items-baseline justify-between gap-2 text-xs font-semibold ${text.muted}`}>
              <span>{item.kind === "diner" ? c.diner : c.host}</span>
              {meta(item)}
            </div>
            <p
              className={`whitespace-pre-wrap rounded border p-2 text-sm ${border.default} ${
                item.kind === "host" ? "bg-sage-light/30" : ""
              } ${text.primary}`}
            >
              {item.text || <span className={text.placeholder}>{c.noReplyText}</span>}
            </p>
          </div>
        );
      case "tool":
      case "toolResult":
        return (
          <details className={`ms-8 rounded border p-2 text-xs ${border.default}`}>
            <summary className={`flex cursor-pointer items-baseline justify-between gap-2 ${text.muted}`}>
              <span>{item.kind === "tool" ? fill(c.toolCall, { name: item.name }) : c.toolResult}</span>
              {meta(item)}
            </summary>
            <pre className={`mt-2 overflow-x-auto whitespace-pre-wrap break-all ${text.primary}`}>{item.detail}</pre>
          </details>
        );
      case "action":
        return (
          <div className="ms-8">
            <div className={`flex items-baseline justify-between gap-2 text-xs font-semibold ${text.muted}`}>
              <span>{fill(c.action, { name: item.name })}</span>
              {meta(item)}
            </div>
            {item.text && (
              <p className={`whitespace-pre-wrap rounded border p-2 text-sm ${border.default} ${text.primary}`}>
                {item.text}
              </p>
            )}
          </div>
        );
      case "handoff":
        return <p className={`text-xs italic ${text.muted}`}>{fill(c.handoff, { reason: item.reason })}</p>;
      case "error":
        return <p className="text-xs text-red-700">{fill(c.error, { reason: item.reason })}</p>;
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className={`text-2xl font-bold ${text.heading}`}>{c.title}</h1>
        <p className={`text-sm ${text.muted}`}>{c.description}</p>
      </div>

      {locations.length === 0 && !locationsFailed ? (
        <div className="glass-card p-6">{c.noLocations}</div>
      ) : (
        <div className="glass-card flex flex-wrap items-end gap-3 p-4">
          <Field label={c.location}>
            <select className={input} value={locationId} onChange={(e) => setLocationId(e.target.value)}>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={c.date}>
            <input type="date" className={input} value={date} onChange={(e) => setDate(e.target.value)} />
          </Field>
          <Field label={c.outcome}>
            <select className={input} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              <option value="">{c.allOutcomes}</option>
              {outcomes.map((o) => (
                <option key={o} value={o}>
                  {outcomeName(o)}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}

      {locationId && (
        <section className="glass-card space-y-2 p-4" aria-labelledby="diner-data-title">
          <h2 id="diner-data-title" className={`text-lg font-semibold ${text.heading}`}>
            {c.dinerData.title}
          </h2>
          <p className={`text-sm ${text.muted}`}>{c.dinerData.description}</p>
          <div className="flex flex-wrap items-end gap-2">
            <Field label={c.dinerData.phone}>
              <input
                type="tel"
                className={input}
                value={dinerPhone}
                placeholder="+12125550100"
                aria-describedby="diner-phone-hint"
                onChange={(e) => setDinerPhone(e.target.value)}
                autoComplete="off"
              />
            </Field>
            <button
              type="button"
              className="glass-btn-secondary text-sm"
              disabled={dinerBusy || !dinerPhone.trim()}
              onClick={() => void dinerAction("export")}
            >
              {c.dinerData.export}
            </button>
            <button
              type="button"
              className="glass-btn-danger text-sm"
              disabled={dinerBusy || !dinerPhone.trim()}
              onClick={() => void dinerAction("erase")}
            >
              {c.dinerData.erase}
            </button>
          </div>
          <p id="diner-phone-hint" className={`text-xs ${text.placeholder}`}>
            {c.dinerData.phoneHint}
          </p>
          {dinerResult && (
            <p role="status" className={`text-sm ${text.primary}`}>
              {dinerResult}
            </p>
          )}
          {dinerError && (
            <p role="alert" className="text-sm text-red-700">
              {dinerError}
            </p>
          )}
        </section>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <section className="glass-card space-y-2 p-4">
          <h2 className={`text-lg font-semibold ${text.heading}`}>{c.list}</h2>
          {listError && <p className="text-sm text-red-700">{listError}</p>}
          {!listError && list === null && locationId && <p className={text.muted}>{r.common.loading}</p>}
          {list !== null && list.length === 0 && <p className={`text-sm ${text.muted}`}>{c.none}</p>}
          <ul className="space-y-2">
            {(list ?? []).map((conv) => (
              <li key={conv.id}>
                <button
                  type="button"
                  onClick={() => void open(conv.id)}
                  aria-pressed={selectedId === conv.id}
                  className={`w-full rounded border p-2 text-start text-sm ${
                    selectedId === conv.id ? "border-sage-dark bg-sage-light/30" : border.default
                  }`}
                >
                  <div className={`flex justify-between gap-2 font-medium ${text.primary}`}>
                    <span>{time(conv.started_at)}</span>
                    <span>{outcomeName(conv.outcome)}</span>
                  </div>
                  <div className={`flex justify-between gap-2 text-xs ${text.muted}`}>
                    <span>
                      {channelName(conv.channel)} · {fill(c.turns, { n: conv.turn_count })}
                    </span>
                    <span>{fill(c.cost, { cost: formatUsd(conv.cost_usd) })}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section className="glass-card space-y-3 p-4">
          <h2 className={`text-lg font-semibold ${text.heading}`}>{c.transcript}</h2>
          {selectedId === null && <p className={`text-sm ${text.muted}`}>{c.selectOne}</p>}
          {detailError && <p className="text-sm text-red-700">{detailError}</p>}
          {selectedId !== null && !detail && !detailError && <p className={text.muted}>{r.common.loading}</p>}
          {detail && (
            <>
              <div className={`flex flex-wrap gap-x-4 gap-y-1 text-xs ${text.muted}`}>
                <span>
                  {time(detail.started_at)}
                  {detail.ended_at ? ` – ${time(detail.ended_at)}` : ""}
                </span>
                <span>{channelName(detail.channel)}</span>
                <span>{outcomeName(detail.outcome)}</span>
                <span>{fill(c.totalCost, { cost: formatUsd(detail.cost_usd) })}</span>
                {detail.reservation_id && <span>{fill(c.reservation, { id: detail.reservation_id })}</span>}
              </div>
              {detail.spam_markable && (
                <div className="space-y-1">
                  <button
                    type="button"
                    className="glass-btn-danger text-sm"
                    disabled={spamBusy}
                    onClick={() => void markSpam()}
                  >
                    {r.numbers.markSpam}
                  </button>
                  {spamNotice && (
                    <p role="status" className={`text-sm ${text.primary}`}>
                      {spamNotice}{" "}
                      <Link to="/manager/reservations/numbers" className="underline">
                        {r.numbers.viewLists}
                      </Link>
                    </p>
                  )}
                  {spamError && (
                    <p role="alert" className="text-sm text-red-700">
                      {spamError}
                    </p>
                  )}
                </div>
              )}
              <ol className="space-y-2">
                {items.map((item) => (
                  <li key={item.seq}>{bubble(item)}</li>
                ))}
              </ol>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
