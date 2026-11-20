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
import { getConfig, getInsights } from "../../api/reservations";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { InsightsOut } from "../../types/reservations";
import {
  chartRows,
  coverPeriods,
  defaultRange,
  fillDays,
  formatPercent,
  formatPoints,
  formatUsdAmount,
  handoffTotals,
  pointsDelta,
  sevenDayKpis,
} from "../../utils/insights";
import { errorCode } from "../../utils/reservations";
import { border, text } from "../../theme";

/** Series colours from the theme (sage dark, accent dark, clear, marker), then softer tints. */
const SERIES = ["#7E8C76", "#A6864A", "#1D7357", "#FF5A1F", "#C5CEBF", "#D4B87E"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
    </label>
  );
}

function ChartCard({ title, children }: { title: string; children: ReactElement }) {
  return (
    <section className="glass-card space-y-2 p-4">
      <h2 className={`text-lg font-semibold ${text.heading}`}>{title}</h2>
      <div dir="ltr" style={{ width: "100%", height: 260 }}>
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

  const initial = defaultRange(new Date().toISOString().slice(0, 10));
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationsFailed, setLocationsFailed] = useState(false);
  const [locationId, setLocationId] = useState("");
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [data, setData] = useState<InsightsOut | null>(null);
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
        const locs = await listLocations();
        setLocations(locs);
        if (locs.length > 0) setLocationId(locs[0].id);
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
    getInsights(locationId, from, to)
      .then((out) => {
        if (mine === seq.current) setData(out);
      })
      .catch((e) => {
        if (mine === seq.current) setError(`${s.loadFailed} ${errorText(e)}`);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId, from, to]);

  const view = useMemo(() => {
    if (!data) return null;
    const periods = coverPeriods(data.days);
    return {
      kpis: sevenDayKpis(data.days, data.to),
      rows: chartRows(fillDays(data.days, data.from, data.to), periods),
      handoffs: handoffTotals(data.days),
      periods,
    };
  }, [data]);

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const pct = (v: unknown) => formatPercent(typeof v === "number" ? v : null, lang);
  const usd = (v: unknown) => formatUsdAmount(typeof v === "number" ? v : null, lang);
  const axisDay = (d: string) => d.slice(5);

  const tile = (label: string, value: string, previous: string, change?: string) => (
    <div key={label} className={`rounded border p-3 ${border.default}`}>
      <div className={`text-xs ${text.muted}`}>{label}</div>
      <div className={`text-2xl font-semibold ${text.heading}`}>{value}</div>
      <div className={`text-xs ${text.muted}`}>
        {fill(s.previous, { value: previous })}
        {change !== undefined && <> · {fill(s.points, { value: change })}</>}
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
                  {l.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={s.from}>
            <input type="date" className={input} value={from} max={to} onChange={(e) => setFrom(e.target.value)} />
          </Field>
          <Field label={s.to}>
            <input type="date" className={input} value={to} min={from} onChange={(e) => setTo(e.target.value)} />
          </Field>
        </div>
      )}

      {error && <p className="text-sm text-red-700">{error}</p>}

      {data && view && data.days.length === 0 && <div className="glass-card p-6">{s.empty}</div>}

      {data && view && data.days.length > 0 && (
        <>
          <section className="glass-card space-y-3 p-4">
            <h2 className={`text-lg font-semibold ${text.heading}`}>{s.kpiTitle}</h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              {tile(
                s.conversations,
                String(view.kpis.current.conversations),
                String(view.kpis.previous.conversations),
              )}
              {tile(
                s.resolution,
                formatPercent(view.kpis.current.resolution, lang),
                formatPercent(view.kpis.previous.resolution, lang),
                formatPoints(pointsDelta(view.kpis.current.resolution, view.kpis.previous.resolution), lang),
              )}
              {tile(
                s.conversion,
                formatPercent(view.kpis.current.conversion, lang),
                formatPercent(view.kpis.previous.conversion, lang),
                formatPoints(pointsDelta(view.kpis.current.conversion, view.kpis.previous.conversion), lang),
              )}
              {tile(
                s.spamBlocked,
                formatPercent(view.kpis.current.spamBlocked, lang),
                formatPercent(view.kpis.previous.spamBlocked, lang),
                formatPoints(pointsDelta(view.kpis.current.spamBlocked, view.kpis.previous.spamBlocked), lang),
              )}
              {tile(
                s.costPerBooking,
                formatUsdAmount(view.kpis.current.costPerBooking, lang),
                formatUsdAmount(view.kpis.previous.costPerBooking, lang),
              )}
              {tile(
                s.billedPerBooking,
                formatUsdAmount(view.kpis.current.billedPerBooking, lang),
                formatUsdAmount(view.kpis.previous.billedPerBooking, lang),
              )}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard title={s.resolutionConversion}>
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis domain={[0, 1]} tickFormatter={pct} />
                <Tooltip formatter={pct} />
                <Legend />
                <Line dataKey="resolution" name={s.resolution} stroke={SERIES[0]} dot={false} />
                <Line dataKey="conversion" name={s.conversion} stroke={SERIES[1]} dot={false} />
              </LineChart>
            </ChartCard>

            <section className="glass-card space-y-2 p-4">
              <h2 className={`text-lg font-semibold ${text.heading}`}>{s.handoffReasons}</h2>
              {view.handoffs.length === 0 ? (
                <p className={`text-sm ${text.muted}`}>{s.noHandoffs}</p>
              ) : (
                <div dir="ltr" style={{ width: "100%", height: 260 }}>
                  <ResponsiveContainer>
                    <BarChart data={view.handoffs} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis type="number" allowDecimals={false} />
                      <YAxis type="category" dataKey="reason" width={120} />
                      <Tooltip />
                      <Bar dataKey="count" name={s.handoffs} fill={SERIES[0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            <ChartCard title={s.latency}>
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis />
                <Tooltip />
                <Legend />
                <Line dataKey="llmP50" name={s.llmP50} stroke={SERIES[0]} dot={false} />
                <Line dataKey="llmP95" name={s.llmP95} stroke={SERIES[0]} strokeDasharray="4 3" dot={false} />
                <Line dataKey="turnP50" name={s.turnP50} stroke={SERIES[1]} dot={false} />
                <Line dataKey="turnP95" name={s.turnP95} stroke={SERIES[1]} strokeDasharray="4 3" dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard title={s.perBooking}>
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis tickFormatter={usd} />
                <Tooltip formatter={usd} />
                <Legend />
                <Line dataKey="costPerBooking" name={s.aiCost} stroke={SERIES[2]} dot={false} />
                <Line dataKey="billedPerBooking" name={s.billed} stroke={SERIES[1]} dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard title={s.spamShare}>
              <LineChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis domain={[0, 1]} tickFormatter={pct} />
                <Tooltip formatter={pct} />
                <Line dataKey="spamBlocked" name={s.spamBlocked} stroke={SERIES[3]} dot={false} />
              </LineChart>
            </ChartCard>

            <ChartCard title={s.covers}>
              <BarChart data={view.rows}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="day" tickFormatter={axisDay} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Legend />
                {view.periods.map((p, i) => (
                  <Bar key={p} dataKey={`covers:${p}`} name={p} stackId="covers" fill={SERIES[i % SERIES.length]} />
                ))}
              </BarChart>
            </ChartCard>
          </div>
        </>
      )}
    </div>
  );
}
