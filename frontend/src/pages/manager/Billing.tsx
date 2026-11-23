import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as billingApi from "../../api/billing";
import type { BillingUsage, ReservationAgentStatus } from "../../api/billing";
import { ApiError } from "../../api/client";
import { getConfig } from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import CallCapacityCard from "./CallCapacityCard";
import {
  agentStatusKey,
  canSubscribe,
  checkoutReturn,
  formatDollars,
  isComped,
  periodEndLabel,
  priceValues,
  survivesCancellationUntil,
  usageAmounts,
} from "../../utils/agentBilling";

const fill = (template: string, values: Record<string, string>): string =>
  template.replace(/\{(\w+)\}/g, (m, k: string) => values[k] ?? m);

const day = (iso: string, locale: string): string =>
  new Date(iso).toLocaleDateString(locale, { year: "numeric", month: "short", day: "numeric" });

/**
 * Billing for the reservation AI host (admins only: /manager/billing is in
 * ADMIN_ONLY_PATHS). It is its own Stripe subscription, separate from
 * scheduling (decision 37); the AI host Checkout returns here.
 */
export default function Billing() {
  const { t, lang } = useLanguage();
  const b = t.reservations.billing;
  const [searchParams, setSearchParams] = useSearchParams();

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [agent, setAgent] = useState<ReservationAgentStatus | null>(null);
  const [scheduling, setScheduling] = useState<BillingUsage | null>(null);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setAgent(await billingApi.getReservationAgent());
      setLoadError("");
    } catch {
      setLoadError(b.loadFailed);
    }
  }, [b.loadFailed]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let on = false;
      try {
        const config = await getConfig();
        on = config.enabled;
        setVoiceEnabled(Boolean(config.voice_enabled));
      } catch {
        on = false;
      }
      if (cancelled) return;
      setEnabled(on);
      if (!on) return;
      billingApi.getUsage().then((u) => !cancelled && setScheduling(u)).catch(() => {});

      const ret = checkoutReturn(searchParams);
      if (ret) {
        searchParams.delete("agent_checkout");
        searchParams.delete("session_id");
        setSearchParams(searchParams, { replace: true });
      }
      if (ret?.kind === "cancel") setNotice(b.checkoutCancelled);
      if (ret?.kind === "success") {
        setNotice(b.confirming);
        try {
          const s = await billingApi.confirmReservationAgent(ret.sessionId);
          if (cancelled) return;
          setAgent(s);
          setNotice("");
          return;
        } catch (err) {
          if (cancelled) return;
          setNotice("");
          // 409 already_subscribed: the webhook or an earlier return got
          // there first; the subscription is recorded, so just show it.
          if (!(err instanceof ApiError && err.status === 409)) setError(b.confirmFailed);
        }
      }
      if (!cancelled) await load();
    })();
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const subscribe = async () => {
    setBusy(true);
    setError("");
    try {
      const { url } = await billingApi.reservationAgentCheckout();
      window.location.href = url;
    } catch (err) {
      setBusy(false);
      if (err instanceof ApiError && err.status === 409) {
        await load();
      } else if (err instanceof ApiError && err.status === 503) {
        setError(b.notConfigured);
      } else {
        setError(b.checkoutFailed);
      }
    }
  };

  const manage = async () => {
    setBusy(true);
    setError("");
    try {
      const { url } = await billingApi.getPortalLink();
      window.location.href = url;
    } catch {
      setBusy(false);
      setError(b.portalFailed);
    }
  };

  if (enabled === null) return <p className={text.muted}>{t.reservations.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{t.reservations.common.notEnabled}</div>;

  const statusKey = agent ? agentStatusKey(agent.status) : "none";
  const periodEnd = agent ? periodEndLabel(agent) : null;
  const usage = agent ? usageAmounts(agent.usage, lang) : null;
  const deletion = agent ? survivesCancellationUntil(agent.status, scheduling) : null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className={`text-2xl font-bold ${text.heading}`}>{b.title}</h1>
        <p className={`text-sm ${text.muted}`}>{b.description}</p>
      </div>

      {notice && (
        <div className="glass-alert-success" role="status">
          {notice}
        </div>
      )}
      {error && (
        <div className="glass-alert-error" role="alert">
          {error}
        </div>
      )}
      {loadError && (
        <div className="glass-alert-error" role="alert">
          {loadError}
        </div>
      )}

      {agent && (
        <section className="glass-card p-4 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <h2 className={`text-lg font-semibold ${text.heading}`}>{b.cardTitle}</h2>
              <p className={`text-sm ${text.secondary}`}>
                {isComped(agent.status) ? b.compedPrice : fill(b.price, priceValues(agent, lang))}
              </p>
              {!isComped(agent.status) && agent.estimated_included_calls.two_minute > 0 && (
                <p className={`text-xs ${text.secondary}`}>
                  {fill(b.callsEstimate, {
                    short: String(agent.estimated_included_calls.one_minute),
                    calls: String(agent.estimated_included_calls.two_minute),
                  })}
                </p>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              {canSubscribe(agent.status) && (
                <button
                  type="button"
                  className="glass-btn-primary"
                  disabled={busy || !agent.configured}
                  onClick={subscribe}
                >
                  {busy ? b.redirecting : b.subscribe}
                </button>
              )}
              {agent.status !== null && !isComped(agent.status) && (
                <button type="button" className="glass-btn-secondary" disabled={busy} onClick={manage}>
                  {b.manage}
                </button>
              )}
            </div>
          </div>

          {!agent.configured && canSubscribe(agent.status) && (
            <p className={`text-sm ${text.muted}`}>{b.notConfigured}</p>
          )}

          {agent.status !== null && (
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className={text.muted}>{b.statusLabel}</dt>
                <dd className={statusKey === "past_due" ? "font-medium text-amber-700" : text.primary}>
                  {b.statuses[statusKey]}
                </dd>
              </div>
              <div>
                <dt className={text.muted}>{b.answeringLabel}</dt>
                <dd className={text.primary}>{agent.answering ? b.answeringOn : b.answeringOff}</dd>
              </div>
              {periodEnd && (
                <div className="sm:col-span-2">
                  <dd className={text.primary}>{fill(b[periodEnd.key], { date: day(periodEnd.iso, lang) })}</dd>
                </div>
              )}
            </dl>
          )}

          {deletion && (
            <div className="glass-alert-error" role="note">
              {fill(b.survivesCancellation, { date: day(deletion, lang) })}
            </div>
          )}

          {agent.status !== null && !isComped(agent.status) && usage && (
            <div className="space-y-2">
              <h3 className={`font-semibold ${text.heading}`}>{b.usageTitle}</h3>
              <dl className="grid grid-cols-3 gap-2 text-sm">
                <div>
                  <dt className={text.muted}>{b.billed}</dt>
                  <dd className={text.primary}>{usage.billed}</dd>
                </div>
                <div>
                  <dt className={text.muted}>{b.included}</dt>
                  <dd className={text.primary}>{usage.included}</dd>
                </div>
                <div>
                  <dt className={text.muted}>{b.overage}</dt>
                  <dd className={usage.hasOverage ? "font-medium text-amber-700" : text.primary}>
                    {usage.overage}
                  </dd>
                </div>
              </dl>
            </div>
          )}

          {agent.status !== null && (
            <div className="space-y-2">
              <h3 className={`font-semibold ${text.heading}`}>{b.todayTitle}</h3>
              {agent.locations.length === 0 ? (
                <p className={`text-sm ${text.muted}`}>{b.noLocations}</p>
              ) : (
                <ul className="space-y-1 text-sm">
                  {agent.locations.map((l) => (
                    <li key={l.location_id} className="flex flex-wrap justify-between gap-2">
                      <span className={text.primary}>{l.name}</span>
                      <span className={l.cap_reached ? "font-medium text-amber-700" : text.secondary}>
                        {fill(b.todayVsCap, {
                          spent: formatDollars(l.spend_today_usd, lang),
                          cap: formatDollars(l.daily_cap_usd, lang),
                        })}
                        {l.cap_reached ? ` · ${b.capReached}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              <p className={`text-xs ${text.muted}`}>{b.capHint}</p>
            </div>
          )}
        </section>
      )}

      {agent?.answering && !isComped(agent.status) && <CallCapacityCard voiceEnabled={voiceEnabled} />}
    </div>
  );
}
