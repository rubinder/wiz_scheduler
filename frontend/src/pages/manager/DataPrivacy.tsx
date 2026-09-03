import { useEffect, useState } from "react";
import { exportMyData, deleteMyAccount, getConsents, type ConsentRecord } from "../../api/gdpr";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import DemoGuard from "../../components/shared/DemoGuard";
import { text, border } from "../../theme";

export default function DataPrivacy() {
  const { t } = useLanguage();
  const { logout } = useAuth();

  const [consents, setConsents] = useState<ConsentRecord[]>([]);
  const [consentsLoading, setConsentsLoading] = useState(true);

  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");

  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    getConsents()
      .then(setConsents)
      .catch(() => {})
      .finally(() => setConsentsLoading(false));
  }, []);

  const handleExport = async () => {
    setExporting(true);
    setExportError("");
    try {
      const data = await exportMyData();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `my-data-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  };

  const handleDelete = async () => {
    if (deleteConfirm !== "DELETE") return;
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteMyAccount();
      logout();
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-8">
      <h1 className={`text-2xl font-bold ${text.heading}`}>{t.gdpr.dataPrivacy}</h1>

      {/* Consents Section */}
      <div className="glass-card p-6">
        <h2 className={`text-lg font-semibold ${text.heading} mb-4`}>{t.gdpr.yourConsents}</h2>
        {consentsLoading ? (
          <p className={text.muted}>{t.common.loading}</p>
        ) : consents.length === 0 ? (
          <p className={text.muted}>{t.common.none}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-start">
              <thead>
                <tr className={`border-b ${border.default}`}>
                  <th className={`py-2 px-3 ${text.muted} font-medium`}>{t.gdpr.consentType}</th>
                  <th className={`py-2 px-3 ${text.muted} font-medium`}>{t.gdpr.version}</th>
                  <th className={`py-2 px-3 ${text.muted} font-medium`}>{t.gdpr.grantedAt}</th>
                  <th className={`py-2 px-3 ${text.muted} font-medium`}>{t.gdpr.revokedAt}</th>
                </tr>
              </thead>
              <tbody>
                {consents.map((c) => (
                  <tr key={c.id} className={`border-b ${border.subtle}`}>
                    <td className={`py-2 px-3 ${text.secondary}`}>{c.consent_type}</td>
                    <td className={`py-2 px-3 ${text.secondary}`}>{c.version}</td>
                    <td className={`py-2 px-3 ${text.secondary}`}>{new Date(c.granted_at).toLocaleDateString()}</td>
                    <td className={`py-2 px-3 ${text.secondary}`}>
                      {c.revoked_at ? new Date(c.revoked_at).toLocaleDateString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Export Section */}
      <div className="glass-card p-6">
        <h2 className={`text-lg font-semibold ${text.heading} mb-2`}>{t.gdpr.exportData}</h2>
        <p className={`${text.muted} text-sm mb-4`}>{t.gdpr.exportDataDesc}</p>
        {exportError && <div className="glass-alert-error mb-4">{exportError}</div>}
        <DemoGuard>
          <button
            onClick={handleExport}
            disabled={exporting}
            className="glass-btn-primary"
          >
            {exporting ? t.gdpr.exporting : t.gdpr.exportBtn}
          </button>
        </DemoGuard>
      </div>

      {/* Delete Account Section */}
      <div className="glass-card p-6 border border-red-500/20">
        <h2 className={`text-lg font-semibold ${text.heading} mb-2`}>{t.gdpr.deleteAccount}</h2>
        <p className={`${text.muted} text-sm mb-4`}>{t.gdpr.deleteAccountDesc}</p>
        {deleteError && <div className="glass-alert-error mb-4">{deleteError}</div>}
        <div className="space-y-3">
          <div>
            <label className="glass-label">{t.gdpr.deleteConfirmPrompt}</label>
            <input
              type="text"
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              className="glass-input w-full max-w-xs"
              placeholder="DELETE"
            />
          </div>
          <DemoGuard>
            <button
              onClick={handleDelete}
              disabled={deleting || deleteConfirm !== "DELETE"}
              className="glass-btn-danger"
            >
              {deleting ? t.gdpr.deleting : t.gdpr.deleteBtn}
            </button>
          </DemoGuard>
        </div>
      </div>
    </div>
  );
}
