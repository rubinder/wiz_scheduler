import { useEffect, useMemo, useRef, useState } from "react";
import type { Employee } from "../../types";
import { fetchCandidates, type CandidateSlot } from "../../api/openShifts";
import { groupCandidates, missingFlags, type Candidate, type MissingFlag, type Tier } from "../../utils/openShifts";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

interface Props {
  employees: Employee[];
  /** The slot to rank for; null shows the plain list. */
  slot: CandidateSlot | null;
  /** null = "Leave open". */
  value: string | null;
  onChange: (id: string | null) => void;
}

/** The employee picker for a shift (#174): "Leave open" first, then people
 *  grouped by how well they fit, as ranked by the server. Falls back to the
 *  plain list if ranking fails. */
export default function CandidatePicker({ employees, slot, value, onChange }: Props) {
  const { t } = useLanguage();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [failed, setFailed] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const slotKey = slot ? JSON.stringify(slot) : "";
  useEffect(() => {
    if (!slot) {
      setCandidates(null);
      return;
    }
    let cancelled = false;
    setFailed(false);
    fetchCandidates(slot)
      .then((c) => !cancelled && setCandidates(c))
      .catch(() => {
        if (!cancelled) {
          setCandidates(null);
          setFailed(true);
        }
      });
    return () => {
      cancelled = true;
    };
    // slotKey captures every field of slot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotKey]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const selectedLabel =
    value === null
      ? t.schedule.leaveOpen
      : employees.find((e) => e.id === value)?.full_name ??
        candidates?.find((c) => c.employee_id === value)?.full_name ??
        "";

  const tierLabel: Record<Tier, string> = {
    1: t.schedule.tierBestFit,
    2: t.schedule.tierRoleNotAvailable,
    3: t.schedule.tierAvailableNoRole,
  };
  const flagLabel: Record<MissingFlag, string> = {
    availability: t.schedule.missingAvailability,
    role: t.schedule.missingRole,
  };

  const groups = useMemo(() => {
    if (candidates) return groupCandidates(candidates, query);
    // Fallback when ranking failed: everyone, one list with no heading. There
    // is nothing to rank by, so nobody is hidden.
    const q = query.trim().toLowerCase();
    const all: Candidate[] = employees
      .filter((e) => !q || e.full_name.toLowerCase().includes(q))
      .map((e) => ({
        employee_id: e.id, full_name: e.full_name, tier: 3,
        available: true, has_role: true, at_location: true,
      }));
    return all.length ? [{ tier: 3 as Tier, candidates: all }] : [];
  }, [candidates, employees, query]);

  const pick = (id: string | null) => {
    onChange(id);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative">
      <input
        type="text"
        value={open ? query : selectedLabel}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        placeholder={t.schedule.searchEmployee}
        className="w-full glass-input-sm"
        autoComplete="off"
      />
      {failed && <p className={`mt-1 text-xs ${text.muted}`}>{t.schedule.candidatesError}</p>}
      {open && (
        <ul className="absolute z-20 mt-1 w-full max-h-64 overflow-auto bg-cream/95 backdrop-blur-xl border border-sage/25 rounded-lg shadow-2xl text-sm">
          <li
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => pick(null)}
            className={`px-3 py-2 cursor-pointer border-b border-sage/20 hover:bg-white/[0.08] ${
              value === null ? "bg-accent/10 text-accent-dark font-medium" : "text-amber-900"
            }`}
          >
            {t.schedule.leaveOpen}
          </li>
          {groups.map((g) => (
            <li key={g.tier}>
              {candidates && (
                <div className={`px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wide ${text.muted}`}>
                  {tierLabel[g.tier]}
                </div>
              )}
              <ul>
                {g.candidates.map((c) => (
                  <li
                    key={c.employee_id}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => pick(c.employee_id)}
                    className={`px-3 py-1.5 cursor-pointer hover:bg-white/[0.08] ${
                      c.employee_id === value ? "bg-accent/10 text-accent-dark font-medium" : text.body
                    }`}
                  >
                    <span>{c.full_name}</span>
                    {candidates &&
                      missingFlags(c).map((f) => (
                        <span
                          key={f}
                          className="ms-1.5 inline-block px-1.5 py-0.5 rounded text-[10px] bg-sage/15 text-sage-dark"
                        >
                          {flagLabel[f]}
                        </span>
                      ))}
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {groups.length === 0 && (
            <li className={`px-3 py-2 ${text.muted}`}>{t.search.noMatches}</li>
          )}
        </ul>
      )}
    </div>
  );
}
