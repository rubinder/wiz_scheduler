import { useCallback, useEffect, useState } from "react";
import { listManagerInvites, createManagerInvite } from "../../api/managerInvites";
import * as companyApi from "../../api/company";
import type { Company, ManagerInvite } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, badge } from "../../theme";

export default function Team() {
  const { t } = useLanguage();
  const [invites, setInvites] = useState<ManagerInvite[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [modalError, setModalError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [invs, comps] = await Promise.all([
        listManagerInvites(),
        companyApi.listGroupCompanies().catch(() => [] as Company[]),
      ]);
      setInvites(invs);
      setCompanies(comps);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load invites");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const companyName = (compId: string | null) => {
    if (!compId) return "—";
    return companies.find((c) => c.id === compId)?.name ?? compId.slice(0, 8);
  };

  const statusBadgeClass = (status: string): string => {
    if (status === "accepted") return badge.status.approved;
    if (status === "pending") return badge.status.draft;
    if (status === "expired") return badge.status.rejected;
    return badge.status.default;
  };

  const statusLabel = (status: string): string => {
    if (status === "accepted") return t.team.acceptedStatus;
    if (status === "expired") return t.team.expiredStatus;
    return t.team.pendingStatus;
  };

  const formatDate = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString() : "—";

  const openModal = () => {
    setInviteEmail("");
    setModalError("");
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setInviteEmail("");
    setModalError("");
  };

  const handleSubmitInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inviteEmail.trim()) return;
    setSubmitting(true);
    setModalError("");
    try {
      await createManagerInvite(inviteEmail.trim());
      await fetchData();
      closeModal();
    } catch (err: unknown) {
      setModalError(err instanceof Error ? err.message : "Failed to send invite");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.team.title}</h1>
        <button onClick={openModal} className="glass-btn-primary">
          {t.team.inviteButton}
        </button>
      </div>

      {error && <div className="glass-alert-error mb-4">{error}</div>}

      {loading ? (
        <div className={`text-sm ${text.muted}`}>{t.common.loading}</div>
      ) : invites.length === 0 ? (
        <div className={`glass-card p-6 text-sm ${text.muted}`}>
          {t.team.noInvites}
        </div>
      ) : (
        <div className="glass-card overflow-x-auto">
          <table className={`min-w-full divide-y ${border.divider}`}>
            <thead className={bg.tableHeader}>
              <tr>
                <th
                  className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.team.columnEmail}
                </th>
                <th
                  className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.team.columnStatus}
                </th>
                <th
                  className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.team.columnInvitedAt}
                </th>
                <th
                  className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.team.columnAcceptedAt}
                </th>
                <th
                  className={`px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`}
                >
                  {t.team.columnCompany}
                </th>
              </tr>
            </thead>
            <tbody className={`divide-y ${border.divider}`}>
              {invites.map((inv) => (
                <tr key={inv.id}>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.body}`}>{inv.email}</span>
                  </td>
                  <td className="px-4 py-2">
                    <span
                      className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${statusBadgeClass(
                        inv.status
                      )}`}
                    >
                      {statusLabel(inv.status)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.secondary}`}>
                      {formatDate(inv.created_at)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.secondary}`}>
                      {formatDate(inv.accepted_at)}
                    </span>
                  </td>
                  <td className="px-4 py-2">
                    <span className={`text-sm ${text.secondary}`}>
                      {companyName(inv.accepted_company_id)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-md mx-4">
            <form onSubmit={handleSubmitInvite}>
              <div
                className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}
              >
                <h2 className={`text-lg font-semibold ${text.body}`}>
                  {t.team.inviteButton}
                </h2>
                <button
                  type="button"
                  onClick={closeModal}
                  className={`${text.muted} hover:text-gray-300 text-xl leading-none`}
                >
                  &times;
                </button>
              </div>

              <div className="px-6 py-4 space-y-3">
                <label className={`block text-sm font-medium ${text.body}`}>
                  {t.team.emailLabel}
                </label>
                <input
                  type="email"
                  required
                  autoFocus
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                  placeholder={t.team.emailPlaceholder}
                  className="glass-input-sm w-full"
                />
                {modalError && (
                  <div className="glass-alert-error">{modalError}</div>
                )}
              </div>

              <div
                className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}
              >
                <button
                  type="button"
                  onClick={closeModal}
                  className="glass-btn-secondary px-4 py-2 text-sm font-medium"
                >
                  {t.team.cancel}
                </button>
                <button
                  type="submit"
                  disabled={submitting || !inviteEmail.trim()}
                  className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
                >
                  {submitting ? t.common.saving : t.team.sendInvite}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
