import { useState } from "react";
import { importFromDeputy, type DeputyImportResult } from "../../api/importDeputy";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border } from "../../theme";
import DemoGuard from "./DemoGuard";

interface Props {
  onClose: () => void;
  /**
   * Invoked when the user dismisses the modal AFTER a successful import.
   * Used by Dashboard to reload the page so newly-imported Companies show
   * up in the sidebar / company-list — without this, React state stays
   * stale and the customer thinks the import didn't work.
   */
  onSuccess?: () => void;
}

function SyncRow({
  label,
  stats,
}: {
  label: string;
  stats: { created: number; updated: number; deleted: number } | { created: number; skipped: number; deleted: number };
}) {
  const col2 = "updated" in stats ? stats.updated : (stats as any).skipped;
  return (
    <tr>
      <td className={`px-3 py-1 text-sm font-medium ${text.secondary}`}>{label}</td>
      <td className="px-3 py-1 text-sm text-emerald-400">{stats.created}</td>
      <td className="px-3 py-1 text-sm text-blue-400">{col2}</td>
      <td className="px-3 py-1 text-sm text-red-400">{stats.deleted}</td>
    </tr>
  );
}

export default function ImportDeputyModal({ onClose, onSuccess }: Props) {
  const { t } = useLanguage();
  const [token, setToken] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<DeputyImportResult | null>(null);

  const handleImport = async () => {
    if (!token.trim()) {
      setError(t.importDeputy.errorNoToken);
      return;
    }
    if (!baseUrl.trim()) {
      setError(t.importDeputy.errorNoUrl);
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await importFromDeputy(token.trim(), baseUrl.trim());
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
          <h2 className={`text-lg font-semibold ${text.body}`}>{t.importDeputy.title}</h2>
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
                {t.importDeputy.description}
              </p>
              <div>
                <label
                  htmlFor="deputy-url"
                  className={`block text-sm font-medium ${text.secondary} mb-1`}
                >
                  {t.importDeputy.baseUrlLabel}
                </label>
                <input
                  id="deputy-url"
                  type="text"
                  className="w-full glass-input"
                  placeholder={t.importDeputy.baseUrlPlaceholder}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  disabled={loading}
                />
              </div>
              <div>
                <label
                  htmlFor="deputy-token"
                  className={`block text-sm font-medium ${text.secondary} mb-1`}
                >
                  {t.importDeputy.accessToken}
                </label>
                <input
                  id="deputy-token"
                  type="password"
                  className="w-full glass-input"
                  placeholder={t.importDeputy.tokenPlaceholder}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  disabled={loading}
                />
                <p className={`text-xs ${text.muted} mt-1`}>
                  {t.importDeputy.tokenHint}{" "}
                  <a
                    href="https://developer.deputy.com/docs/the-deputy-api"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-accent-dark underline hover:text-accent"
                  >
                    Deputy API Docs
                  </a>
                  .
                </p>
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
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-blue-400 border-t-transparent" />
              <span className={`text-sm ${text.muted}`}>
                {t.importDeputy.importing}
              </span>
            </div>
          )}

          {result && (
            <div className="space-y-3">
              <div className="glass-alert-success">
                {t.importDeputy.success}
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className={`text-xs ${text.muted} uppercase`}>
                    <th className="px-3 py-1">{t.importDeputy.entity}</th>
                    <th className="px-3 py-1">{t.importDeputy.created}</th>
                    <th className="px-3 py-1">{t.importDeputy.updatedOrSkipped}</th>
                    <th className="px-3 py-1">{t.importDeputy.deleted}</th>
                  </tr>
                </thead>
                <tbody>
                  <SyncRow label={t.common.locations} stats={result.locations} />
                  <SyncRow label={t.common.roles} stats={result.roles} />
                  <SyncRow label={t.common.employees} stats={result.employees} />
                  <SyncRow label={t.importDeputy.unavailability} stats={result.unavailability} />
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
              onClick={() => {
                onSuccess?.();
                onClose();
              }}
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
                  className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
                  disabled={loading || !token.trim() || !baseUrl.trim()}
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
