import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ApiError } from "../../api/client";
import { listLocations } from "../../api/locations";
import { listManagers } from "../../api/managers";
import { getConfig, getRules, saveFlows } from "../../api/reservations";
import FlowConflictNotice from "../../components/reservations/FlowConflictNotice";
import FlowDiagram from "../../components/reservations/FlowDiagram";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Location, ManagerSummary } from "../../types";
import type {
  Flow,
  FlowChannel,
  FlowCollect,
  FlowProblem,
  FlowStep,
  FlowStepKind,
  RulesSchema,
} from "../../types/reservations";
import { flowProblems, flowReducer, isFlowConflict, stepLabel } from "../../utils/conversationFlow";
import type { FlowAction } from "../../utils/conversationFlow";
import { border, text } from "../../theme";

const KINDS: FlowStepKind[] = ["ask", "choice", "book", "change_or_cancel", "say", "handoff", "end"];
const COLLECTS: FlowCollect[] = ["party_size", "date_time", "name", "phone", "occasion", "free_text"];
const CHANNELS: FlowChannel[] = ["web", "voice"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={`glass-label-sm ${text.muted}`}>{label}</span>
      {children}
    </label>
  );
}

/** The server's 422 `invalid_flow` problems, or null for any other error. */
function serverProblems(err: unknown): FlowProblem[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const d = err.data as { code?: string; problems?: FlowProblem[] } | null;
  return d && d.code === "invalid_flow" && Array.isArray(d.problems) ? d.problems : null;
}

export default function ReservationsFlow() {
  const { t } = useLanguage();
  const { isAdmin, user } = useAuth();
  const r = t.reservations;
  const f = r.flow;

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [locations, setLocations] = useState<Location[]>([]);
  const [locationId, setLocationId] = useState("");
  const [rules, setRules] = useState<RulesSchema | null>(null);
  const [flows, dispatch] = useReducer(flowReducer, { web: null, voice: null });
  const [editors, setEditors] = useState<string[]>([]);
  const [managers, setManagers] = useState<ManagerSummary[]>([]);
  const [channel, setChannel] = useState<FlowChannel>("web");
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [remote, setRemote] = useState<FlowProblem[]>([]);
  // The flows_version loaded: sent with every save. A stale one is refused
  // (409 flow_conflict); the editor keeps the unsaved edits until Reload.
  const [flowsVersion, setFlowsVersion] = useState(0);
  const [conflict, setConflict] = useState(false);
  const [reloads, setReloads] = useState(0);
  const currentLocation = useRef("");

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setEnabled(cfg.enabled);
        if (!cfg.enabled) return;
        const locs = await listLocations();
        setLocations(locs);
        if (locs.length > 0) setLocationId(locs[0].id);
      } catch {
        setEnabled(true);
        setLoadError(r.errors.generic);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    listManagers()
      .then(setManagers)
      .catch(() => setManagers([]));
  }, [isAdmin]);

  useEffect(() => {
    currentLocation.current = locationId;
    if (!locationId) return;
    let cancelled = false;
    setRules(null);
    setSaved(false);
    setSaveError("");
    setRemote([]);
    setLoadError("");
    setConflict(false);
    getRules(locationId)
      .then((out) => {
        if (cancelled) return;
        setRules(out.rules);
        setFlowsVersion(out.flows_version);
        dispatch({ type: "load", state: out.rules.flows ?? { web: null, voice: null } });
        setEditors(out.rules.flow_editor_ids ?? []);
      })
      .catch(() => {
        if (!cancelled) setLoadError(r.errors.generic);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locationId, reloads]);

  const canEdit = isAdmin || (!!user && !!rules && (rules.flow_editor_ids ?? []).includes(user.id));
  const flow = flows[channel];
  const local = useMemo(
    () => CHANNELS.flatMap((c) => (flows[c] ? flowProblems(flows[c] as Flow).map((p) => ({ ...p, channel: c })) : [])),
    [flows],
  );
  const problems = remote.length > 0 ? remote : local;
  const shown = problems.filter((p) => (p.channel ?? channel) === channel);
  const locationManagers = managers.filter(
    (m) => m.manager_type === "regular" && m.assigned_locations.some((l) => l.id === locationId),
  );
  // Listed editors who are no longer regular managers here: shown so an
  // admin can remove them (the server also drops them on the next save).
  const staleEditors = editors.filter((id) => !locationManagers.some((m) => m.id === id));
  const managerName = (id: string) => {
    const m = managers.find((x) => x.id === id);
    return m ? m.full_name || m.email : id;
  };

  const act = (action: FlowAction) => {
    setSaved(false);
    setRemote([]);
    dispatch(action);
  };
  const stepName = (id: string) => (flow ? stepLabel(flow, id, f.untitled) : f.untitled);
  const problemText = (p: FlowProblem) => (f.problems as Record<string, string>)[p.code] ?? r.errors.generic;

  const onSave = async () => {
    if (!rules || local.length > 0) return;
    const forLocation = locationId;
    setSaving(true);
    setSaveError("");
    setSaved(false);
    // Only the flows (and the editors, for an admin): the server keeps every
    // other rules field as stored, so a colleague's rules edit is never undone.
    const body = {
      flows: { web: flows.web, voice: flows.voice },
      flows_version: flowsVersion,
      ...(isAdmin ? { flow_editor_ids: editors } : {}),
    };
    try {
      const out = await saveFlows(forLocation, body);
      if (currentLocation.current !== forLocation) return;
      setRules(out.rules);
      setFlowsVersion(out.flows_version);
      setEditors(out.rules.flow_editor_ids ?? []);
      setSaved(true);
    } catch (e) {
      if (currentLocation.current !== forLocation) return;
      const found = serverProblems(e);
      if (found) setRemote(found);
      else if (e instanceof ApiError && isFlowConflict(e.status, e.data)) setConflict(true);
      else if (e instanceof ApiError && e.status === 403) setSaveError(f.readOnly);
      else setSaveError(r.errors.generic);
    } finally {
      setSaving(false);
    }
  };

  const input = `glass-input-sm text-sm ${border.default} ${text.primary}`;

  const targetSelect = (value: string | null, onChange: (v: string) => void, allowNone: boolean, label: string) => (
    <Field label={label}>
      <select className={`${input} block`} disabled={!canEdit} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {allowNone ? <option value="">{f.nextNone}</option> : <option value="">{f.choose}</option>}
        {flow?.steps.map((s) => (
          <option key={s.id} value={s.id}>
            {stepName(s.id)}
          </option>
        ))}
      </select>
    </Field>
  );

  const textInput = (value: string, onChange: (v: string) => void, label: string, max: number, long = false) => (
    <Field label={label}>
      {long ? (
        <textarea
          className={`${input} block w-full`}
          rows={2}
          maxLength={max}
          disabled={!canEdit}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          className={`${input} block w-full`}
          maxLength={max}
          disabled={!canEdit}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );

  const stepEditor = (s: FlowStep, index: number) => {
    const update = (patch: Record<string, string | null>) => act({ type: "updateStep", channel, id: s.id, patch });
    const mine = shown.filter((p) => p.step === s.id);
    return (
      <li key={s.id} className="glass-card space-y-2 p-3">
        <div className="flex flex-wrap items-end gap-3">
          <span className={`text-sm font-semibold ${text.muted}`}>{index + 1}.</span>
          <div className="min-w-[10rem] flex-1">{textInput(s.title, (v) => update({ title: v }), f.stepTitle, 80)}</div>
          <Field label={f.kind}>
            <select
              className={`${input} block`}
              disabled={!canEdit}
              value={s.kind}
              onChange={(e) => act({ type: "changeKind", channel, id: s.id, kind: e.target.value as FlowStepKind })}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {f.kinds[k]}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex gap-1">
            <button
              type="button"
              className="glass-btn-secondary text-xs"
              disabled={!canEdit || index === 0}
              aria-label={`${f.moveUp}: ${stepName(s.id)}`}
              onClick={() => act({ type: "moveStep", channel, id: s.id, delta: -1 })}
            >
              ↑
            </button>
            <button
              type="button"
              className="glass-btn-secondary text-xs"
              disabled={!canEdit || index === (flow?.steps.length ?? 0) - 1}
              aria-label={`${f.moveDown}: ${stepName(s.id)}`}
              onClick={() => act({ type: "moveStep", channel, id: s.id, delta: 1 })}
            >
              ↓
            </button>
            <button
              type="button"
              className="glass-btn-secondary text-xs"
              disabled={!canEdit}
              onClick={() => act({ type: "removeStep", channel, id: s.id })}
            >
              {f.removeStep}
            </button>
          </div>
        </div>

        {(s.kind === "ask" || s.kind === "choice") && textInput(s.prompt, (v) => update({ prompt: v }), f.prompt, 500, true)}
        {s.kind === "ask" && (
          <div className="flex flex-wrap gap-3">
            <Field label={f.collect}>
              <select
                className={`${input} block`}
                disabled={!canEdit}
                value={s.collect}
                onChange={(e) => update({ collect: e.target.value })}
              >
                {COLLECTS.map((c) => (
                  <option key={c} value={c}>
                    {f.collects[c]}
                  </option>
                ))}
              </select>
            </Field>
            {targetSelect(s.next, (v) => update({ next: v }), false, f.next)}
          </div>
        )}
        {s.kind === "choice" && (
          <div className="space-y-2">
            {s.options.map((o, i) => (
              <div key={i} className="flex flex-wrap items-end gap-3">
                <div className="min-w-[10rem] flex-1">
                  {textInput(
                    o.label,
                    (v) => act({ type: "updateOption", channel, id: s.id, index: i, patch: { label: v } }),
                    f.optionLabel.replace("{n}", String(i + 1)),
                    80,
                  )}
                </div>
                {targetSelect(
                  o.next,
                  (v) => act({ type: "updateOption", channel, id: s.id, index: i, patch: { next: v } }),
                  false,
                  f.next,
                )}
                <button
                  type="button"
                  className="glass-btn-secondary text-xs"
                  disabled={!canEdit}
                  onClick={() => act({ type: "removeOption", channel, id: s.id, index: i })}
                >
                  {r.common.remove}
                </button>
              </div>
            ))}
            <button
              type="button"
              className="glass-btn-secondary text-xs"
              disabled={!canEdit || s.options.length >= 6}
              onClick={() => act({ type: "addOption", channel, id: s.id })}
            >
              {f.addOption}
            </button>
          </div>
        )}
        {s.kind === "book" && targetSelect(s.next, (v) => update({ next: v === "" ? null : v }), true, f.next)}
        {(s.kind === "say" || s.kind === "end") && textInput(s.message, (v) => update({ message: v }), f.message, 500, true)}
        {s.kind === "say" && targetSelect(s.next, (v) => update({ next: v }), false, f.next)}
        {s.kind === "handoff" && textInput(s.reason, (v) => update({ reason: v }), f.reason, 80)}

        {mine.length > 0 && (
          <ul className="text-sm text-red-700" role="alert">
            {mine.map((p, i) => (
              <li key={i}>{problemText(p)}</li>
            ))}
          </ul>
        )}
      </li>
    );
  };

  if (enabled === false) return <div className="glass-card p-6 text-sm">{r.common.notEnabled}</div>;

  const general = shown.filter((p) => !p.step || !flow?.steps.some((s) => s.id === p.step));

  return (
    <div className="space-y-4">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{f.title}</h1>
      <p className={`text-sm ${text.muted}`}>{f.description}</p>

      {locations.length === 0 ? (
        enabled && !loadError && <div className="glass-card p-6 text-sm">{f.noLocations}</div>
      ) : (
        <Field label={f.location}>
          <select
            className={`${input} block`}
            disabled={saving}
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
          >
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {loadError && (
        <div className="glass-alert-error" role="alert">
          {loadError}
        </div>
      )}
      {locationId && !rules && !loadError && <p className={text.muted}>{r.common.loading}</p>}

      {rules && (
        <>
          {!canEdit && <div className="glass-card p-4 text-sm">{f.readOnly}</div>}

          <div role="tablist" className="flex gap-2">
            {CHANNELS.map((c) => (
              <button
                key={c}
                type="button"
                role="tab"
                aria-selected={channel === c}
                className={channel === c ? "glass-btn-primary" : "glass-btn-secondary"}
                onClick={() => setChannel(c)}
              >
                {c === "web" ? f.tabWeb : f.tabVoice}
              </button>
            ))}
          </div>

          <section className="glass-card space-y-3 p-4" role="tabpanel">
            <fieldset className="flex flex-wrap items-center gap-4">
              <legend className="sr-only">{f.title}</legend>
              <label className="flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name="flow-mode"
                  disabled={!canEdit}
                  checked={flow === null}
                  onChange={() => act({ type: "useDefault", channel })}
                />
                {f.useDefault}
              </label>
              <label className="flex items-center gap-1 text-sm">
                <input
                  type="radio"
                  name="flow-mode"
                  disabled={!canEdit}
                  checked={flow !== null}
                  onChange={() => act({ type: "useCustom", channel })}
                />
                {f.useCustom}
              </label>
            </fieldset>

            {flow === null && <p className={`text-sm ${text.secondary}`}>{f.defaultExplained}</p>}

            {flow !== null && (
              <>
                <div className="flex flex-wrap items-end gap-3">
                  <button
                    type="button"
                    className="glass-btn-secondary"
                    disabled={!canEdit}
                    onClick={() => act({ type: "loadDefault", channel })}
                  >
                    {f.startFromDefault}
                  </button>
                  {channel === "voice" && (
                    <button
                      type="button"
                      className="glass-btn-secondary"
                      disabled={!canEdit || flows.web === null}
                      onClick={() => act({ type: "copyWebToVoice" })}
                    >
                      {f.copyWebToVoice}
                    </button>
                  )}
                  {targetSelect(flow.start, (v) => act({ type: "setStart", channel, id: v }), false, f.startStep)}
                </div>

                {general.length > 0 && (
                  <div className="glass-alert-error" role="alert">
                    <p>{f.problemsTitle}</p>
                    <ul className="list-disc ps-5">
                      {general.map((p, i) => (
                        <li key={i}>
                          {p.step ? f.problemAt.replace("{step}", p.step).replace("{problem}", problemText(p)) : problemText(p)}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <ol className="space-y-3">{flow.steps.map(stepEditor)}</ol>

                <div className="flex flex-wrap gap-2">
                  {KINDS.map((k) => (
                    <button
                      key={k}
                      type="button"
                      className="glass-btn-secondary text-xs"
                      disabled={!canEdit}
                      onClick={() => act({ type: "addStep", channel, kind: k })}
                    >
                      {f.addStep}: {f.kinds[k]}
                    </button>
                  ))}
                </div>

                <h2 className={`text-lg font-semibold ${text.heading}`}>{f.diagram}</h2>
                <FlowDiagram flow={flow} label={stepName} title={f.diagram} />
              </>
            )}
          </section>

          {isAdmin && (
            <section className="glass-card space-y-2 p-4">
              <h2 className={`text-lg font-semibold ${text.heading}`}>{f.editors}</h2>
              <p className={`text-sm ${text.muted}`}>{f.editorsHint}</p>
              {staleEditors.length > 0 && (
                <ul className="space-y-1">
                  {staleEditors.map((id) => (
                    <li key={id}>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked
                          onChange={() => {
                            setSaved(false);
                            setEditors(editors.filter((x) => x !== id));
                          }}
                        />
                        {f.staleEditor.replace("{name}", managerName(id))}
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {locationManagers.length === 0 ? (
                <p className={`text-sm ${text.secondary}`}>{f.noEditors}</p>
              ) : (
                <ul className="space-y-1">
                  {locationManagers.map((m) => (
                    <li key={m.id}>
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={editors.includes(m.id)}
                          onChange={(e) => {
                            setSaved(false);
                            setEditors(e.target.checked ? [...editors, m.id] : editors.filter((id) => id !== m.id));
                          }}
                        />
                        {m.full_name || m.email}
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <div className="flex items-center gap-3">
            <button
              type="button"
              className="glass-btn-primary"
              disabled={saving || !canEdit || local.length > 0 || conflict}
              onClick={onSave}
            >
              {saving ? r.common.saving : r.common.save}
            </button>
            {saved && <span className={`text-sm ${text.muted}`}>{r.common.saved}</span>}
            {saveError && (
              <span className="text-sm text-red-700" role="alert">
                {saveError}
              </span>
            )}
          </div>
          {conflict && (
            <FlowConflictNotice message={f.conflict} reloadLabel={f.reload} onReload={() => setReloads((n) => n + 1)} />
          )}
        </>
      )}
    </div>
  );
}
