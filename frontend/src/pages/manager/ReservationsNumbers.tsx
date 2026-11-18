import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import {
  createNumberRule,
  deleteNumberRule,
  getConfig,
  getScreeningStats,
  listNumberRules,
  updateNumberRule,
} from "../../api/reservations";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location } from "../../types";
import type { NumberRuleOut, ScreeningStatsOut } from "../../types/reservations";
import {
  canEditRule,
  emptyRuleForm,
  formatExpiry,
  formToCreate,
  formToPatch,
  NOTE_MAX,
  numberErrorKey,
  ruleStatus,
  ruleToForm,
  showE164Hint,
} from "../../utils/numberRules";
import type { RuleForm } from "../../utils/numberRules";
import { errorCode } from "../../utils/reservations";
import { border, text } from "../../theme";

function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
      {hint}
    </label>
  );
}

const fill = (template: string, values: Record<string, string | number>): string =>
  template.replace(/\{(\w+)\}/g, (m, k: string) => (k in values ? String(values[k]) : m));

/**
 * Number lists (#259 step 7): numbers the AI host always or never answers,
 * per location or company-wide, and the last week's screening counts.
 * Whether an entry is active and whether a call is blocked are the server's
 * answers; this page only shows them.
 */
export default function ReservationsNumbers() {
  const { t, lang } = useLanguage();
  const { isAdmin } = useAuth();
  const r = t.reservations;
  const n = r.numbers;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  /** "" = every location in scope (plus company-wide entries). */
  const [filter, setFilter] = useState("");
  const [rules, setRules] = useState<NumberRuleOut[] | null>(null);
  const [stats, setStats] = useState<ScreeningStatsOut | null>(null);
  const [loadError, setLoadError] = useState("");
  /** null = adding a new entry. */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<RuleForm>(emptyRuleForm(""));
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [notice, setNotice] = useState("");
  const seq = useRef(0);

  const errorText = (e: unknown): string => {
    const code = errorCode(e instanceof ApiError ? e.data : null);
    const own = numberErrorKey(code);
    if (own) return n[own];
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
        setForm(emptyRuleForm(locs[0]?.id ?? ""));
      } catch (e) {
        setEnabled(true);
        setLoadError(errorText(e));
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const load = async () => {
    const mine = ++seq.current;
    setRules(null);
    setLoadError("");
    try {
      const [list, counts] = await Promise.all([
        listNumberRules(filter || undefined),
        getScreeningStats(filter || undefined),
      ]);
      if (mine !== seq.current) return;
      setRules(list);
      setStats(counts);
    } catch (e) {
      if (mine === seq.current) setLoadError(n.loadFailed + " " + errorText(e));
    }
  };

  useEffect(() => {
    if (enabled) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, filter]);

  const locationName = (id: string | null): string =>
    id === null ? n.allLocations : (locations.find((l) => l.id === id)?.name ?? id);

  const startAdd = () => {
    setEditingId(null);
    setForm(emptyRuleForm(filter || locations[0]?.id || ""));
    setFormError("");
  };

  const startEdit = (rule: NumberRuleOut) => {
    setEditingId(rule.id);
    setForm(ruleToForm(rule));
    setFormError("");
    setNotice("");
  };

  const save = async () => {
    if (busy) return;
    const patch = editingId ? formToPatch(form) : null;
    const create = editingId ? null : formToCreate(form);
    if (!patch && !create) return;
    setBusy(true);
    setFormError("");
    setNotice("");
    try {
      if (editingId && patch) await updateNumberRule(editingId, patch);
      else if (create) await createNumberRule(create);
      setNotice(r.common.saved);
      startAdd();
      await load();
    } catch (e) {
      setFormError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (rule: NumberRuleOut) => {
    if (busy || !window.confirm(fill(n.confirmDelete, { number: rule.e164 }))) return;
    setBusy(true);
    setNotice("");
    setLoadError("");
    try {
      await deleteNumberRule(rule.id);
      if (editingId === rule.id) startAdd();
      await load();
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (enabled === null) return <p className={text.muted}>{r.common.loading}</p>;
  if (!enabled) return <div className="glass-card p-6">{r.common.notEnabled}</div>;

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;
  const hintBad = showE164Hint(form.number);
  const valid = editingId ? formToPatch(form) !== null : formToCreate(form) !== null;
  const scopeChoices = locations.map((l) => l.id);
  if (isAdmin) scopeChoices.push("");
  // An entry being edited keeps its scope even when it is not offered for new entries (archived, company-wide).
  if (editingId && !scopeChoices.includes(form.scope)) scopeChoices.push(form.scope);

  return (
    <div className="space-y-4">
      <div>
        <h1 className={`text-2xl font-bold ${text.heading}`}>{n.title}</h1>
        <p className={`text-sm ${text.muted}`}>{n.description}</p>
      </div>

      {stats && (
        <section className="glass-card p-4" aria-labelledby="screening-stats-title">
          <h2 id="screening-stats-title" className={`text-sm font-semibold ${text.heading}`}>
            {fill(n.statsTitle, { days: stats.days })}
          </h2>
          <dl className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
            {(
              [
                [n.blockedList, stats.blocked_list],
                [n.blockedFtc, stats.blocked_ftc],
                [n.answered, stats.answered],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className={`rounded border p-3 ${border.default}`}>
                <dt className={`text-xs ${text.muted}`}>{label}</dt>
                <dd className={`text-2xl font-semibold tabular-nums ${text.primary}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <div className="glass-card flex flex-wrap items-end gap-3 p-4">
        <Field label={n.location}>
          <select className={input} value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">{n.allMyLocations}</option>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <section className="glass-card space-y-2 overflow-x-auto p-4">
        {loadError && (
          <p role="alert" className="text-sm text-red-700">
            {loadError}
          </p>
        )}
        {!loadError && rules === null && <p className={text.muted}>{r.common.loading}</p>}
        {rules !== null && rules.length === 0 && <p className={`text-sm ${text.muted}`}>{n.none}</p>}
        {rules !== null && rules.length > 0 && (
          <table className="w-full text-start text-sm">
            <thead>
              <tr className={`text-xs ${text.muted}`}>
                <th className="p-2 text-start font-medium">{n.number}</th>
                <th className="p-2 text-start font-medium">{n.action}</th>
                <th className="p-2 text-start font-medium">{n.scope}</th>
                <th className="p-2 text-start font-medium">{n.expires}</th>
                <th className="p-2 text-start font-medium">{n.note}</th>
                <th className="p-2 text-start font-medium">{n.createdBy}</th>
                <th className="p-2 text-start font-medium">{n.status}</th>
                <th className="p-2" />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => {
                const status = ruleStatus(rule);
                return (
                  <tr key={rule.id} className={`border-t ${border.default} ${text.primary}`}>
                    <td className="p-2 font-mono" dir="ltr">
                      {rule.e164}
                    </td>
                    <td className="p-2">{rule.action === "allow" ? n.allow : n.block}</td>
                    <td className="p-2">{locationName(rule.location_id)}</td>
                    <td className="p-2">{formatExpiry(rule.expires_on, lang, n.never)}</td>
                    <td className="p-2 break-words">{rule.note ?? ""}</td>
                    <td className="p-2">{rule.created_by_name ?? ""}</td>
                    <td className={`p-2 ${status === "expired" ? text.muted : ""}`}>
                      {status === "active" ? n.active : n.expired}
                    </td>
                    <td className="p-2 whitespace-nowrap text-end">
                      {canEditRule(isAdmin, rule) && (
                        <>
                          <button
                            type="button"
                            className="glass-btn-secondary text-xs"
                            disabled={busy}
                            onClick={() => startEdit(rule)}
                          >
                            {n.edit}
                          </button>{" "}
                          <button
                            type="button"
                            className="glass-btn-danger text-xs"
                            disabled={busy}
                            onClick={() => void remove(rule)}
                          >
                            {n.delete}
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {!isAdmin && <p className={`text-xs ${text.placeholder}`}>{n.adminOnly}</p>}
      </section>

      <section className="glass-card space-y-3 p-4" aria-labelledby="number-form-title">
        <h2 id="number-form-title" className={`text-lg font-semibold ${text.heading}`}>
          {editingId ? n.editTitle : n.addTitle}
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label={n.number}
            hint={
              <span
                id="number-hint"
                className={`block text-xs ${hintBad ? "text-red-700" : text.placeholder}`}
              >
                {n.numberHint}
              </span>
            }
          >
            <input
              type="tel"
              dir="ltr"
              className={input}
              value={form.number}
              placeholder="+12125550100"
              disabled={editingId !== null}
              aria-invalid={hintBad}
              aria-describedby="number-hint"
              autoComplete="off"
              onChange={(e) => setForm({ ...form, number: e.target.value })}
            />
          </Field>
          <Field label={n.action}>
            <select
              className={input}
              value={form.action}
              onChange={(e) => setForm({ ...form, action: e.target.value as RuleForm["action"] })}
            >
              <option value="block">{n.block}</option>
              <option value="allow">{n.allow}</option>
            </select>
          </Field>
          <Field label={n.scope}>
            <select
              className={input}
              value={form.scope}
              disabled={editingId !== null}
              onChange={(e) => setForm({ ...form, scope: e.target.value })}
            >
              {scopeChoices.map((id) => (
                <option key={id || "all"} value={id}>
                  {locationName(id || null)}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label={n.expiresOn}
            hint={<span className={`block text-xs ${text.placeholder}`}>{n.expiresHint}</span>}
          >
            <input
              type="date"
              className={input}
              value={form.expiresOn}
              onChange={(e) => setForm({ ...form, expiresOn: e.target.value })}
            />
          </Field>
          <div className="sm:col-span-2">
            <Field
              label={n.note}
              hint={<span className={`block text-xs ${text.placeholder}`}>{n.noteHint}</span>}
            >
              <input
                type="text"
                className={`${input} w-full`}
                value={form.note}
                maxLength={NOTE_MAX}
                onChange={(e) => setForm({ ...form, note: e.target.value })}
              />
            </Field>
          </div>
        </div>
        {editingId && <p className={`text-xs ${text.placeholder}`}>{n.scopeLocked}</p>}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="glass-btn-primary text-sm"
            disabled={busy || !valid}
            onClick={() => void save()}
          >
            {busy ? r.common.saving : editingId ? n.save : n.add}
          </button>
          {editingId && (
            <button type="button" className="glass-btn-secondary text-sm" disabled={busy} onClick={startAdd}>
              {n.cancel}
            </button>
          )}
        </div>
        {formError && (
          <p role="alert" className="text-sm text-red-700">
            {formError}
          </p>
        )}
        {notice && (
          <p role="status" className={`text-sm ${text.primary}`}>
            {notice}
          </p>
        )}
      </section>
    </div>
  );
}
