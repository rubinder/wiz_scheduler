import { useMemo } from "react";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, roleColorsLight } from "../../theme";
import type { Employee, ShiftAssignment, SpecialHoursDay } from "../../types";
import { formatTime } from "../../utils/shiftTime";

const ROLE_COLORS = roleColorsLight;

// Extracted verbatim from Schedule.tsx so the approved-schedule viewer and the
// post-generation review are guaranteed to render identically — two components
// that agree today drift tomorrow. Behaviour is deliberately unchanged; see the
// PR for two pre-existing issues noted but not fixed here. (#92, the
// timezone one, is since fixed — formatTime now comes from utils/shiftTime.)

/** Extract HH:MM from an HH:MM or HH:MM:SS string */
export const fmtHM = (t: string) => (t.length >= 5 ? t.slice(0, 5) : t);

const dayLabelCache: Record<string, string> = {};
export function getDayLabel(dateStr: string): string {
  if (dayLabelCache[dateStr]) return dayLabelCache[dateStr];
  const d = new Date(dateStr + "T00:00:00");
  const label =
    d.toLocaleDateString("en-US", { weekday: "short" }) +
    " " +
    d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  dayLabelCache[dateStr] = label;
  return label;
}

interface ScheduleGridProps {
  shifts: ShiftAssignment[];
  editable: boolean;
  employees: Employee[];
  onEditShift?: (shiftIndex: number) => void;
  specialHoursByDate?: Record<string, SpecialHoursDay>;
}

export default function ScheduleGrid({
  shifts,
  editable,
  onEditShift,
  specialHoursByDate,
}: ScheduleGridProps) {
  const { t } = useLanguage();
  const { dates, roles, grid, roleColorMap, shiftIndexMap } = useMemo(() => {
    const dateSet = new Set<string>();
    const roleSet = new Set<string>();
    shifts.forEach((s) => {
      dateSet.add(s.date);
      roleSet.add(s.role_name);
    });
    const sortedDates = Array.from(dateSet).sort();
    const sortedRoles = Array.from(roleSet).sort();

    const g: Record<string, Record<string, ShiftAssignment[]>> = {};
    // Map each shift in the grid back to its index in the flat array
    const indexMap: Record<string, Record<string, number[]>> = {};
    for (const role of sortedRoles) {
      g[role] = {};
      indexMap[role] = {};
      for (const date of sortedDates) {
        g[role][date] = [];
        indexMap[role][date] = [];
      }
    }
    shifts.forEach((s, i) => {
      g[s.role_name][s.date].push(s);
      indexMap[s.role_name][s.date].push(i);
    });

    const colorMap: Record<string, string> = {};
    sortedRoles.forEach((r, i) => {
      colorMap[r] = ROLE_COLORS[i % ROLE_COLORS.length];
    });

    return {
      dates: sortedDates,
      roles: sortedRoles,
      grid: g,
      roleColorMap: colorMap,
      shiftIndexMap: indexMap,
    };
  }, [shifts]);

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full">
        <thead>
          <tr className={bg.tableHeader}>
            <th className={`px-4 py-3 text-left text-xs font-semibold ${text.muted} uppercase sticky left-0 ${bg.stickyCol} z-10 min-w-[140px]`}>
              {t.common.role}
            </th>
            {dates.map((d) => {
              const sh = specialHoursByDate?.[d];
              return (
                <th
                  key={d}
                  className={`px-3 py-3 text-center text-xs font-semibold ${text.muted} uppercase min-w-[160px]`}
                >
                  <div>{getDayLabel(d)}</div>
                  {sh && (
                    <span
                      className="mt-1 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-900 normal-case"
                      title={`${sh.label ?? t.specialHours.scheduleBadge} · ${fmtHM(sh.open_time)}–${fmtHM(sh.close_time)}`}
                    >
                      ★ {sh.label ?? t.specialHours.scheduleBadge} · {fmtHM(sh.open_time)}–{fmtHM(sh.close_time)}
                    </span>
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role} className={`border-t ${border.subtle}`}>
              <td className={`px-4 py-3 text-sm font-medium ${text.secondary} align-top sticky left-0 ${bg.stickyCol} z-10`}>
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-semibold border ${roleColorMap[role]}`}
                >
                  {role}
                </span>
              </td>
              {dates.map((date) => {
                const cellShifts = grid[role][date];
                const cellIndices = shiftIndexMap[role][date];
                return (
                  <td key={date} className="px-3 py-3 align-top">
                    {cellShifts.length === 0 ? (
                      <span className="text-gray-600 text-xs">&mdash;</span>
                    ) : (
                      <div className="space-y-1.5">
                        {cellShifts.map((s, i) => (
                          <div
                            key={i}
                            onClick={
                              editable && onEditShift
                                ? () => onEditShift(cellIndices[i])
                                : undefined
                            }
                            className={`rounded-lg border px-2.5 py-1.5 ${roleColorMap[role]} ${
                              editable
                                ? "cursor-pointer hover:ring-2 hover:ring-accent/40 transition-shadow"
                                : ""
                            }`}
                          >
                            <div className="text-sm font-medium text-inherit">
                              {s.employee_name}
                            </div>
                            <div className="text-xs opacity-75">
                              {formatTime(s.start_time)} &ndash;{" "}
                              {formatTime(s.end_time)}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main ──
