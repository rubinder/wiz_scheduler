import { useState } from "react";
import { importFrom7Shifts, type ImportResult } from "../../api/import7shifts";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border } from "../../theme";
import DemoGuard from "./DemoGuard";

interface Props {
  onClose: () => void;
}

function SyncRow({
  label,
  stats,
}: {
  label: string;
  stats: { created: number; updated: number; deleted: number };
}) {
  return (
    <tr>
      <td className={`px-3 py-1 text-sm font-medium ${text.secondary}`}>{label}</td>
      <td className="px-3 py-1 text-sm text-emerald-400">{stats.created}</td>
      <td className="px-3 py-1 text-sm text-blue-400">{stats.updated}</td>
      <td className="px-3 py-1 text-sm text-red-400">{stats.deleted}</td>
    </tr>
  );
}

export default function Import7ShiftsModal({ onClose }: Props) {
  const { t } = useLanguage();
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  const handleImport = async () => {
    if (!token.trim()) {
      setError("Please enter your 7shifts access token");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await importFrom7Shifts(token.trim());
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-lg mx-4">
        <div className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}>
          <h2 className={`text-lg font-semibold ${text.body}`}>{t.import7Shifts.title}</h2>
          <button
            onClick={onClose}
            className={`${text.muted} hover:${text.secondary} text-xl leading-none`}
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          {!result && (
            <>
              <p className={`text-sm ${text.muted}`}>
                {t.import7Shifts.description}{" "}
                <span className={`font-medium ${text.secondary}`}>
                  {t.import7Shifts.devTools}
                </span>
                .
              </p>
              <div>
                <label
                  htmlFor="seven-token"
                  className={`block text-sm font-medium ${text.secondary} mb-1`}
                >
                  {t.import7Shifts.accessToken}
                </label>
                <input
                  id="seven-token"
                  type="password"
                  className="w-full glass-input focus:ring-orange-500"
                  placeholder={t.import7Shifts.placeholder}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  disabled={loading}
                />
              </div>
            </>
          )}

          {error && (
            <div className="glass-alert-error">
              {error}
            </div>
          )}

          {loading && (
            <div className="flex items-center gap-3 py-4">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-orange-400 border-t-transparent" />
              <span className={`text-sm ${text.muted}`}>
                {t.import7Shifts.importingData}
              </span>
            </div>
          )}

          {result && (
            <div className="space-y-3">
              <div className="glass-alert-success">
                {t.import7Shifts.importSuccess}
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className={`text-xs ${text.muted} uppercase`}>
                    <th className="px-3 py-1">{t.import7Shifts.entity}</th>
                    <th className="px-3 py-1">{t.import7Shifts.created}</th>
                    <th className="px-3 py-1">{t.import7Shifts.updated}</th>
                    <th className="px-3 py-1">{t.import7Shifts.deleted}</th>
                  </tr>
                </thead>
                <tbody>
                  <SyncRow label={t.common.companies} stats={result.companies} />
                  <SyncRow label={t.common.locations} stats={result.locations} />
                  <SyncRow label={t.import7Shifts.departments} stats={result.departments} />
                  <SyncRow label={t.common.roles} stats={result.roles} />
                  <SyncRow label={t.common.employees} stats={result.employees} />
                  <SyncRow
                    label={t.import7Shifts.userAssignments}
                    stats={result.user_assignments}
                  />
                </tbody>
              </table>

              {result.errors.length > 0 && (
                <div className="glass-alert-warning">
                  <p className="font-medium mb-1">{t.common.warnings}:</p>
                  <ul className="list-disc list-inside space-y-0.5">
                    {result.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className={`flex justify-end gap-3 px-6 py-4 border-t ${border.default} ${bg.sectionSubtle} rounded-b-2xl`}>
          {result ? (
            <button
              onClick={onClose}
              className="glass-btn-primary px-4 py-2 text-sm font-medium"
            >
              {t.common.done}
            </button>
          ) : (
            <>
              <button
                onClick={onClose}
                className="glass-btn-secondary px-4 py-2 text-sm font-medium"
                disabled={loading}
              >
                {t.common.cancel}
              </button>
              <DemoGuard>
                <button
                  onClick={handleImport}
                  className="glass-btn-orange px-4 py-2 text-sm font-medium disabled:opacity-50"
                  disabled={loading || !token.trim()}
                >
                  {loading ? t.common.loading : t.common.import}
                </button>
              </DemoGuard>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
