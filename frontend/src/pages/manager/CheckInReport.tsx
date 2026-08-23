import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { getCheckInReport } from "../../api/checkIns";
import { listEmployees } from "../../api/employees";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { CheckInReportRow, Employee } from "../../types";

export default function CheckInReport() {
  const { t } = useLanguage();
  const [rows, setRows] = useState<CheckInReportRow[]>([]);
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [retentionDays, setRetentionDays] = useState(180);

  useEffect(() => {
    listEmployees().then(setEmployees).catch(() => setEmployees([]));
  }, []);

  useEffect(() => {
    getCheckInReport(employeeId || undefined)
      .then((r) => {
        setRows(r.rows);
        setRetentionDays(r.retention_days);
      })
      .catch(() => setRows([]));
  }, [employeeId]);

  /** Only matched rows carry a punctuality number; the rest have no shift to
   *  be early or late against. `duplicate` is excluded by construction, which
   *  is what stops an afternoon re-scan reading as a late arrival. */
  const points = useMemo(
    () =>
      rows
        .filter((r) => r.status === "matched" && r.minutes_from_start !== null)
        .map((r) => ({
          x: new Date(r.local_date).getTime(),
          y: r.minutes_from_start as number,
          name: r.employee_name,
        })),
    [rows]
  );

  return (
    <div className="p-6">
      <h1 className={`text-2xl font-semibold mb-1 ${text.body}`}>
        {t.checkIn.reportTitle}
      </h1>
      <p className={`mb-6 max-w-2xl ${text.muted}`}>
        {t.checkIn.reportDesc.replace("{days}", String(retentionDays))}
      </p>

      <label className={`block mb-2 text-sm ${text.muted}`}>
        {t.checkIn.filterEmployee}
      </label>
      <select
        value={employeeId}
        onChange={(e) => setEmployeeId(e.target.value)}
        className="glass-input mb-6"
      >
        <option value="">{t.checkIn.allEmployees}</option>
        {employees.map((e) => (
          <option key={e.id} value={e.id}>
            {e.full_name}
          </option>
        ))}
      </select>

      {points.length === 0 ? (
        <p className={text.muted}>{t.checkIn.noData}</p>
      ) : (
        <div style={{ width: "100%", height: 360 }}>
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="x"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v) => new Date(v).toLocaleDateString()}
                name={t.checkIn.date}
              />
              <YAxis
                dataKey="y"
                type="number"
                name={t.checkIn.minutesLate}
              />
              {/* Zero is on time: below the line arrived early, above late. */}
              <ReferenceLine y={0} stroke="currentColor" />
              <Tooltip
                labelFormatter={(v) => new Date(Number(v)).toLocaleDateString()}
              />
              <Scatter data={points} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}

      <table className="w-full mt-8 text-sm">
        <thead>
          <tr>
            <th className="text-start py-2">{t.checkIn.date}</th>
            <th className="text-start py-2">{t.checkIn.filterEmployee}</th>
            <th className="text-end py-2">{t.checkIn.minutesLate}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="py-1">{r.local_date}</td>
              <td className="py-1">{r.employee_name}</td>
              <td className="py-1 text-end">
                {r.minutes_from_start ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
