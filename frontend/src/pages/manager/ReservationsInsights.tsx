import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactElement, ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import { getConfig, getInsights, getRules } from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { InsightsOut } from "../../types/reservations";
import { isArchived, withArchivedMark } from "../../utils/archived";
import {
  chartRows,
  coverPeriods,
  DASH,
  defaultRange,
  fillDays,
  formatPercent,
  formatPoints,
  formatUsdAmount,
  handoffTotals,
  isDefaultRange,
  latestValue,
  PALETTE,
  periodColor,
  pointsDelta,
  rangeKpis,
  seriesMean,
  sevenDayKpis,
} from "../../utils/insights";
import type { Kpis } from "../../utils/insights";
import { errorCode } from "../../utils/reservations";
import { border, text } from "../../theme";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
    </label>
  );
}

/** A chart with a visually hidden one-line text summary for screen readers. */
function ChartCard({ title, summary, children }: { title: string; summary: string; children: ReactElement }) {
  return (
    <section className="glass-card space-y-2 p-4">
      <h2 className={`text-lg font-semibold ${text.heading}`}>{title}</h2>
      <p className="sr-only">{summary}</p>
      <div dir="ltr" aria-hidden="true" style={{ width: "100%", height: 260 }}>
        <ResponsiveContainer>{children}</ResponsiveContainer>
      </div>
    </section>
  );
}

const fill = (template: string, values: Record<string, string | number>): string =>
  template.replace(/\{(\w+)\}/g, (m, k: string) => (k in values ? String(values[k]) : m));

/**
 * AI host Insights (#259 step 10): one location's nightly metrics by UTC
 * day. The API returns raw daily rows; every ratio, 7-day comparison and gap
 * comes from the pure helpers in utils/insights.
 */
export default function ReservationsInsights() {
  const { t, lang } = useLanguage();
  const r = t.reservations;
  const s = r.insights;

  /** UTC today; the newest metrics day is yesterday, because the job runs nightly. */
  const today = new Date().toISOString().slice(0, 10);
  const initial = defaultRange(today);
  const yesterday = initial.to;
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationsFailed, setLocationsFailed] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [data, setData] = useState<InsightsOut | null>(null);
  /** The location rules' service period names, in order ([] if unavailable). */
  const [periodOrder, setPeriodOrder] = useState<string[]>([]);
  const [error, setError] = useState("");
  const seq = useRef(0);

  const errorText = (e: unknown): string => {
    const code = errorCode(e instanceof ApiError ? e.data : null);
    if (code === "invalid_range") return s.rangeInvalid;
    return (code && (r.errors as Record<string, string>)[code]) || r.errors.generic;
  };

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setEnabled(cfg.enabled);
        if (!cfg.enabled) return;
        // Archived locations too: their metrics are history.
        const locs = await listLocations(true);
        setLocations(locs);
        const first = locs.find((l) => !isArchived(l)) ?? locs[0];
        if (first) setLocationId(first.id);
      } catch (e) {
        setEnabled(true);
        setLocationsFailed(true);
        setError(errorText(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const mine = ++seq.current;
    setData(null);
    setError("");
    if (!locationId || !from || !to) return;
    // The period order is cosmetic: an archived location's rules 404, and then covers sort alphabetically.
    const order = getRules(locationId)
      .then((out) => out.rules.service_periods.map((p) => p.name))
      .catch(() => [] as string[]);
    Promise.all([getInsights(locationId, from, to), order])
      .then(([out, names]) => {
        if (mine !== seq.current) return;
        setPeriodOrder(names);
        setData(out);
      })
      .catch((e) => {
        if (mine === seq.current) setError(`${s.loadFailed} ${errorText(e)}`);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId, from, to]);

  const view = useMemo(() => {
    if (!data) return null;
    const periods = coverPeriods(data.days, periodOrder);
    const covers = periods.map((p) => ({
      period: p,
      total: data.days.reduce((acc, d) => acc + (d.covers_by_period[p] ?? 0), 0),
    }));
    return {
      kpis: sevenDayKpis(data.days, data.from, data.to),
      range: rangeKpis(data.days),
      rows: chartRows(fillDays(data.days, data.from, data.to), periods),
      handoffs: handoffTotals(data.days),
      periods,
      covers,
    };
  }, [data, periodOrder]);

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const pct = (v: unknown) => formatPercent(typeof v === "number" ? v : null, lang);
  const usd = (v: unknown) => formatUsdAmount(typeof v === "number" ? v : null, lang);
  const axisDay = (d: string) => d.slice(5);

  /** A cleared date input keeps the last valid value; `to` never passes yesterday. */
  const pickFrom = (v: string) => {
    if (v) setFrom(v);
  };
  const pickTo = (v: string) => {
    if (v) setTo(v > yesterday ? yesterday : v);
  };
  const count = (k: Kpis | null) => (k ? String(k.conversations) : DASH);
  const summary = (series: string, latest: string, range: string) => fill(s.summary, { series, latest, range });
  const ms = (v: number | null) => (v === null ? DASH : String(Math.round(v)));

  const tile = (label: string, value: string, previous: string, change?: string) => (
    <div key={label} className={`rounded border p-3 ${border.default}`}>
      <div className={`text-xs ${text.muted}`}>{label}</div>
      <div className={`text-2xl font-semibold ${text.heading}`}>{value}</div>
      <div className={`text-xs ${text.muted}`}>
        {fill(s.previous, { value: previous })}
        {change !== undefined && change !== DASH && <> · {fill(s.points, { value: change })}</>}
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      <div>
        <h1 className={`text-2xl font-bold ${text.heading}`}>{s.title}</h1>
        <p className={`text-sm ${text.muted}`}>{s.description}</p>
      </div>

      {locations.length === 0 && !locationsFailed ? (
        <div className="glass-card p-6">{s.noLocations}</div>
      ) : (
        <div className="glass-card flex flex-wrap items-end gap-3 p-4">
          <Field label={s.location}>
            <select className={input} value={locationId} onChange={(e) => setLocationId(e.target.value)}>
              {locations.map((l) => (
                <option key={l.id} value={l.id}>
                  {withArchivedMark(l.name, l, t.common.archived)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={s.from}>
            <input type="date" className={input} value={from} max={to} onChange={(e) => pickFrom(e.target.value)} />
          </Field>
          <Field label={s.to}>
            <input type="date" className={input} value={to} min={from} max={yesterday} onChange={(e) => pickTo(e.target.value)} />
          </Field>
        </div>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      {data && view && data.days.length === 0 && (
        <div className="glass-card p-6">{isDefaultRange(data.from, data.to, today) ? s.empty : s.noData}</div>
      )}

      {data && view && data.days.length > 0 && (
        <>
          <section className="glass-card space-y-3 p-4">
            <h2 className={`text-lg font-semibold ${text.heading}`}>{s.kpiTitle}</h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              {tile(
                s.conversations,
                count(view.kpis.current),
                count(view.kpis.previous),
              )}
              {tile(
                s.resolution,
                formatPercent(view.kpis.current?.resolution ?? null, lang),
                formatPercent(view.kpis.previous?.resolution ?? null, lang),
                formatPoints(pointsDelta(view.kpis.current?.resolution ?? null, view.kpis.previous?.resolution ?? null), lang),
              )}
              {tile(
                s.conversion,
                formatPercent(view.kpis.current?.conversion ?? null, lang),
                formatPercent(view.kpis.previous?.conversion ?? null, lang),
                formatPoints(pointsDelta(view.kpis.current?.conversion ?? null, view.kpis.previous?.conversion ?? null), lang),
              )}
              {tile(
                s.spamBlocked,
                formatPercent(view.kpis.current?.spamBlocked ?? null, lang),
                formatPercent(view.kpis.previous?.spamBlocked ?? null, lang),
                formatPoints(pointsDelta(view.kpis.current?.spamBlocked ?? null, view.kpis.previous?.spamBlocked ?? null), lang),
              )}
              {tile(
                s.costPerBooking,
                formatUsdAmount(view.kpis.current?.costPerBooking ?? null, lang),
                formatUsdAmount(view.kpis.previous?.costPerBooking ?? null, lang),
              )}
              {tile(
                s.billedPerBooking,
                formatUsdAmount(view.kpis.current?.billedPerBooking ?? null, lang),
                formatUsdAmount(view.kpis.previous?.billedPerBooking ?? null, lang),
              )}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard
              title={s.resolutionConversion}
              summary={[
                summary(s.resolution, pct(latestValue(view.rows, "resolution")), pct(view.range.resolution)),
                summary(s.conversion, pct(latestValue(view.rows, "conversion")), pct(view.range.conversion)),
              ].join(" ")}
            >
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis domain={[0, 1]} tickFormatter={pct} />
                <Tooltip formatter={pct} />
                <Legend />
                <Line dataKey="resolution" name={s.resolution} stroke={PALETTE[0]} dot={false} />
                <Line dataKey="conversion" name={s.conversion} stroke={PALETTE[1]} dot={false} />
              </LineChart>
            </ChartCard>

            <section className="glass-card space-y-2 p-4">
              <h2 className={`text-lg font-semibold ${text.heading}`}>{s.handoffReasons}</h2>
              {view.handoffs.length === 0 ? (
                <p className={`text-sm ${text.muted}`}>{s.noHandoffs}</p>
              ) : (
                <p className="sr-only">
                  {fill(s.totals, { list: view.handoffs.map((h) => `${h.reason} ${h.count}`).join(", ") })}
                </p>
              )}
              {view.handoffs.length > 0 && (
                <div dir="ltr" aria-hidden="true" style={{ width: "100%", height: 260 }}>
                  <ResponsiveContainer>
                    <BarChart data={view.handoffs} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis type="number" allowDecimals={false} />
                      <YAxis type="category" dataKey="reason" width={120} />
                      <Tooltip />
                      <Bar dataKey="count" name={s.handoffs} fill={PALETTE[0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            <ChartCard
              title={s.latency}
              summary={(
                [
                  ["llmP50", s.llmP50],
                  ["llmP95", s.llmP95],
                  ["turnP50", s.turnP50],
                  ["turnP95", s.turnP95],
                ] as const
              )
                .map(([key, name]) => summary(name, ms(latestValue(view.rows, key)), ms(seriesMean(view.rows, key))))
                .join(" ")}
            >
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line dataKey="llmP50" name={s.llmP50} stroke={PALETTE[0]} dot={false} />
                <Line dataKey="llmP95" name={s.llmP95} stroke={PALETTE[0]} strokeDasharray="4 3" dot={false} />
                <Line dataKey="turnP50" name={s.turnP50} stroke={PALETTE[1]} dot={false} />
                <Line dataKey="turnP95" name={s.turnP95} stroke={PALETTE[1]} strokeDasharray="4 3" dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard
              title={s.perBooking}
              summary={[
                summary(s.aiCost, usd(latestValue(view.rows, "costPerBooking")), usd(view.range.costPerBooking)),
                summary(s.billed, usd(latestValue(view.rows, "billedPerBooking")), usd(view.range.billedPerBooking)),
              ].join(" ")}
            >
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis tickFormatter={usd} />
                <Tooltip formatter={usd} />
                <Legend />
                <Line dataKey="costPerBooking" name={s.aiCost} stroke={PALETTE[2]} dot={false} />
                <Line dataKey="billedPerBooking" name={s.billed} stroke={PALETTE[1]} dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard
              title={s.spamShare}
              summary={summary(s.spamBlocked, pct(latestValue(view.rows, "spamBlocked")), pct(view.range.spamBlocked))}
            >
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis domain={[0, 1]} tickFormatter={pct} />
                <Tooltip formatter={pct} />
                <Line dataKey="spamBlocked" name={s.spamBlocked} stroke={PALETTE[3]} dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard
              title={s.covers}
              summary={fill(s.totals, { list: view.covers.map((c) => `${c.period} ${c.total}`).join(", ") })}
            >
              <BarChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Legend />
                {view.periods.map((p) => (
                  <Bar key={p} dataKey={`covers:${p}`} name={p} stackId="covers" fill={periodColor(p)} />
                ))}
              </BarChart>
            </ChartCard>
          </div>
        </>
      )}
    </div>
  );
}
