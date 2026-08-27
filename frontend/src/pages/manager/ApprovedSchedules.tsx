import { useCallback, useEffect, useMemo, useState } from "react";
import * as approvedApi from "../../api/approvedSchedules";
import type { WeekSchedule } from "../../api/approvedSchedules";
import { listApprovedDates } from "../../api/exportSchedules";
import * as locationsApi from "../../api/locations";
import ScheduleGrid from "../../components/shared/ScheduleGrid";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, calendarToday, spinner as spinnerClass } from "../../theme";

// The repo styles panels with the global `glass-card` class (see
// HourRestrictions.tsx:192), not a theme token.
const card = "glass-card";
import type { Location } from "../../types";

function toDateStr(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Monday of the week containing `d`. Schedules are keyed by week_start_date,
 *  which is always a Monday, so any clicked day maps to its Monday. */
function mondayOf(dateStr: string): string {
  const d = new Date(dateStr + "T00:00:00");
  const dow = (d.getDay() + 6) % 7; // Monday = 0
  d.setDate(d.getDate() - dow);
  return toDateStr(d);
}

function buildMonthGrid(year: number, month: number): (string | null)[][] {
  const firstDay = new Date(year, month, 1);
  const startDow = (firstDay.getDay() + 6) % 7; // Monday = 0
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const weeks: (string | null)[][] = [];
  let week: (string | null)[] = Array(startDow).fill(null);

  for (let day = 1; day <= daysInMonth; day++) {
    week.push(toDateStr(new Date(year, month, day)));
    if (week.length === 7) {
      weeks.push(week);
      week = [];
    }
  }
  if (week.length > 0) {
    while (week.length < 7) week.push(null);
    weeks.push(week);
  }
  return weeks;
}

export default function ApprovedSchedules() {
  const { t } = useLanguage();
  const today = useMemo(() => new Date(), []);

  const [viewYear, setViewYear] = useState(today.getFullYear());
  const [viewMonth, setViewMonth] = useState(today.getMonth());
  const [approvedDates, setApprovedDates] = useState<Set<string>>(new Set());
  const [selectedWeek, setSelectedWeek] = useState<string>(() => mondayOf(toDateStr(new Date())));
  const [schedules, setSchedules] = useState<WeekSchedule[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    locationsApi.listLocations().then(setLocations).catch(() => setLocations([]));
  }, []);

  // Dots marking which dates already have an approved schedule.
  useEffect(() => {
    const anchor = toDateStr(new Date(viewYear, viewMonth, 15));
    listApprovedDates(anchor)
      .then((dates) => setApprovedDates(new Set(dates)))
      .catch(() => {
        /* non-critical: the dots simply won't render */
      });
  }, [viewYear, viewMonth]);

  const loadWeek = useCallback(async (weekStart: string) => {
    setLoading(true);
    setError("");
    try {
      setSchedules(await approvedApi.getApprovedWeek(weekStart));
    } catch {
      setError(t.approvedSchedules.loadError);
      setSchedules([]);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    loadWeek(selectedWeek);
  }, [selectedWeek, loadWeek]);

  const locationName = useCallback(
    (id: string) => locations.find((l) => l.id === id)?.name ?? id,
    [locations]
  );

  const shiftMonth = (delta: number) => {
    const d = new Date(viewYear, viewMonth + delta, 1);
    setViewYear(d.getFullYear());
    setViewMonth(d.getMonth());
  };

  const monthLabel = new Date(viewYear, viewMonth, 1).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
  });

  const weekDays = useMemo(() => {
    const start = new Date(selectedWeek + "T00:00:00");
    return new Set(
      Array.from({ length: 7 }, (_, i) => {
        const d = new Date(start);
        d.setDate(d.getDate() + i);
        return toDateStr(d);
      })
    );
  }, [selectedWeek]);

  return (
    <div className="p-6 max-w-[1400px] mx-auto">
      <h1 className={`text-2xl font-bold ${text.primary} mb-1`}>
        {t.approvedSchedules.title}
      </h1>
      <p className={`text-sm ${text.muted} mb-6`}>{t.approvedSchedules.subtitle}</p>

      <div className="flex flex-col lg:flex-row gap-6">
        {/* Month calendar */}
        <div className={`${card} p-4 lg:w-[300px] shrink-0`}>
          <div className="flex items-center justify-between mb-3">
            <button
              type="button"
              onClick={() => shiftMonth(-1)}
              aria-label={t.approvedSchedules.previousMonth}
              className={`px-2 py-1 rounded ${bg.interactiveHover} ${text.secondary}`}
            >
              ‹
            </button>
            <span className={`text-sm font-semibold ${text.primary}`}>{monthLabel}</span>
            <button
              type="button"
              onClick={() => shiftMonth(1)}
              aria-label={t.approvedSchedules.nextMonth}
              className={`px-2 py-1 rounded ${bg.interactiveHover} ${text.secondary}`}
            >
              ›
            </button>
          </div>

          <div className="grid grid-cols-7 mb-1">
            {["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((d) => (
              <div key={d} className={`text-center text-[10px] font-medium ${text.muted} py-1`}>
                {d}
              </div>
            ))}
          </div>

          {buildMonthGrid(viewYear, viewMonth).map((week, wi) => (
            <div key={wi} className="grid grid-cols-7">
              {week.map((day, di) => {
                if (!day) return <div key={`empty-${di}`} className="h-9" />;
                const isToday = day === toDateStr(new Date());
                const inSelectedWeek = weekDays.has(day);
                const hasApproved = approvedDates.has(day);
                return (
                  <button
                    key={day}
                    type="button"
                    onClick={() => setSelectedWeek(mondayOf(day))}
                    className={`relative flex flex-col items-center justify-center h-9 rounded text-sm transition-colors ${
                      inSelectedWeek ? bg.calendarSelected : `${bg.interactiveHover} ${text.secondary}`
                    } ${isToday ? calendarToday : ""}`}
                  >
                    <span>{new Date(day + "T00:00:00").getDate()}</span>
                    {hasApproved && (
                      <span className="absolute bottom-0.5 w-1.5 h-1.5 rounded-full bg-orange-400" />
                    )}
                  </button>
                );
              })}
            </div>
          ))}

          <p className={`mt-3 text-[11px] ${text.muted} flex items-center gap-1.5`}>
            <span className="w-1.5 h-1.5 rounded-full bg-orange-400 inline-block" />
            {t.approvedSchedules.dotLegend}
          </p>
        </div>

        {/* The week */}
        <div className="flex-1 min-w-0">
          <h2 className={`text-sm font-semibold ${text.secondary} mb-3`}>
            {t.approvedSchedules.weekOf} {selectedWeek}
          </h2>

          {loading && <div className={spinnerClass} />}
          {error && <p className="text-sm text-red-600">{error}</p>}

          {!loading && !error && schedules.length === 0 && (
            <div className={`${card} p-8 text-center ${text.muted} text-sm`}>
              {t.approvedSchedules.emptyWeek}
            </div>
          )}

          {!loading &&
            schedules.map((sched) => (
              <div key={sched.id} className={`${card} p-4 mb-4`}>
                <div className={`flex items-baseline justify-between mb-3 pb-2 border-b ${border.subtle}`}>
                  <h3 className={`font-semibold ${text.primary}`}>
                    {locationName(sched.location_id)}
                  </h3>
                  <span className={`text-xs ${text.muted}`}>
                    {sched.shifts.length} {sched.shifts.length === 1 ? t.common.shift : t.common.shifts}
                  </span>
                </div>
                <ScheduleGrid
                  shifts={approvedApi.toAssignments(sched.shifts)}
                  editable={false}
                  employees={[]}
                />
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
