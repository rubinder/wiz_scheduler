import { useCallback, useEffect, useState } from "react";
import { listManagerInvites, createManagerInvite } from "../../api/managerInvites";
import { listManagers, updateManager } from "../../api/managers";
import * as companyApi from "../../api/company";
import * as locationsApi from "../../api/locations";
import { errorMessage } from "../../api/client";
import type {
  Company,
  Location,
  ManagerInvite,
  ManagerSummary,
  ManagerType,
} from "../../types";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, badge } from "../../theme";
import {
  accessErrorMessage,
  draftError,
  draftFromManager,
  hasArchivedAssignments,
  toggleLocation,
  updateBody,
  type AccessDraft,
} from "./teamAccess";

const thClass = `px-4 py-3 text-start text-xs font-medium ${text.muted} uppercase tracking-wider`;

/** Admin / location-manager choice plus, for a location manager, the
 * checklist of active locations. Shared by the invite and edit forms. */
function AccessFields({
  draft,
  onChange,
  locations,
  name,
}: {
  draft: AccessDraft;
  onChange: (d: AccessDraft) => void;
  locations: Location[];
  name: string;
}) {
  const { t } = useLanguage();
  const option = (type: ManagerType, label: string, desc: string) => (
    <label className="flex items-start gap-2 text-sm cursor-pointer">
      <input
        type="radio"
        name={name}
        className="mt-1"
        checked={draft.managerType === type}
        onChange={() => onChange({ ...draft, managerType: type })}
      />
      <span>
        <span className={`font-medium ${text.body}`}>{label}</span>
        <span className={`block text-xs ${text.muted}`}>{desc}</span>
      </span>
    </label>
  );
  return (
    <div className="space-y-3">
      <fieldset className="space-y-2">
        <legend className={`block text-sm font-medium ${text.body} mb-1`}>
          {t.team.typeLabel}
        </legend>
        {option("admin", t.team.typeAdmin, t.team.typeAdminDesc)}
        {option("regular", t.team.typeRegular, t.team.typeRegularDesc)}
      </fieldset>
      {draft.managerType === "regular" && (
        <fieldset className="space-y-1">
          <legend className={`block text-sm font-medium ${text.body} mb-1`}>
            {t.team.locationsLabel}
          </legend>
          {locations.length === 0 ? (
            <p className={`text-xs ${text.muted}`}>{t.team.noActiveLocations}</p>
          ) : (
            <div className={`max-h-48 overflow-y-auto rounded-lg border ${border.default} p-2 space-y-1`}>
              {locations.map((loc) => (
                <label key={loc.id} className={`flex items-center gap-2 text-sm ${text.body}`}>
                  <input
                    type="checkbox"
                    checked={draft.locationIds.includes(loc.id)}
                    onChange={() => onChange(toggleLocation(draft, loc.id))}
                  />
                  {loc.name}
                </label>
              ))}
            </div>
          )}
        </fieldset>
      )}
    </div>
  );
}

export default function Team() {
  const { t } = useLanguage();
  const { user, refreshUser } = useAuth();
  const [managers, setManagers] = useState<ManagerSummary[]>([]);
  const [invites, setInvites] = useState<ManagerInvite[]>([]);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Invite modal state
  const [showModal, setShowModal] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteDraft, setInviteDraft] = useState<AccessDraft>({
    managerType: "admin",
    locationIds: [],
  });
  const [modalError, setModalError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Edit-access modal state
  const [editing, setEditing] = useState<ManagerSummary | null>(null);
  const [editDraft, setEditDraft] = useState<AccessDraft | null>(null);
  const [editError, setEditError] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [mgrs, invs, comps, locs] = await Promise.all([
        listManagers(),
        listManagerInvites(),
        companyApi.listGroupCompanies().catch(() => [] as Company[]),
        locationsApi.listLocations().catch(() => [] as Location[]),
      ]);
      setManagers(mgrs);
      setInvites(invs);
      setCompanies(comps);
      setLocations(locs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load the team");
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

  const locationName = (id: string) => locations.find((l) => l.id === id)?.name ?? id.slice(0, 8);

  const typeLabel = (type: ManagerType | undefined) =>
    type === "regular" ? t.team.typeRegular : t.team.typeAdmin;

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
    setInviteDraft({ managerType: "admin", locationIds: [] });
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
    if (draftError(inviteDraft)) {
      setModalError(t.team.locationsRequired);
      return;
    }
    setSubmitting(true);
    setModalError("");
    try {
      await createManagerInvite(
        inviteEmail.trim(),
        inviteDraft.managerType,
        inviteDraft.locationIds
      );
      await fetchData();
      closeModal();
    } catch (err: unknown) {
      setModalError(errorMessage(err, "Failed to send invite"));
    } finally {
      setSubmitting(false);
    }
  };

  const openEdit = (m: ManagerSummary) => {
    setEditing(m);
    setEditDraft(draftFromManager(m));
    setEditError("");
  };

  const closeEdit = () => {
    setEditing(null);
    setEditDraft(null);
    setEditError("");
  };

  const handleSaveAccess = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editing || !editDraft) return;
    if (draftError(editDraft)) {
      setEditError(t.team.locationsRequired);
      return;
    }
    setSaving(true);
    setEditError("");
    try {
      await updateManager(editing.id, updateBody(editing, editDraft));
      // Changing your own type changes what this session may see.
      if (editing.id === user?.id) await refreshUser();
      await fetchData();
      closeEdit();
    } catch (err: unknown) {
      setEditError(accessErrorMessage(err, t.team, "Failed to save access"));
    } finally {
      setSaving(false);
    }
  };

  const inviteLocations = (inv: ManagerInvite) =>
    inv.manager_type === "regular" && inv.location_ids?.length
      ? inv.location_ids.map(locationName).join(", ")
      : t.team.allLocations;

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
      ) : (
        <>
          <h2 className={`text-lg font-semibold ${text.heading} mb-3`}>
            {t.team.managersTitle}
          </h2>
          {managers.length === 0 ? (
            <div className={`glass-card p-6 text-sm ${text.muted} mb-8`}>
              {t.team.noManagers}
            </div>
          ) : (
            <div className="glass-card overflow-x-auto mb-8">
              <table className={`min-w-full divide-y ${border.divider}`}>
                <thead className={bg.tableHeader}>
                  <tr>
                    <th className={thClass}>{t.team.columnName}</th>
                    <th className={thClass}>{t.team.columnEmail}</th>
                    <th className={thClass}>{t.team.columnType}</th>
                    <th className={thClass}>{t.team.columnLocations}</th>
                    <th className={thClass} />
                  </tr>
                </thead>
                <tbody className={`divide-y ${border.divider}`}>
                  {managers.map((m) => (
                    <tr key={m.id}>
                      <td className="px-4 py-2">
                        <span className={`text-sm ${text.body}`}>{m.full_name ?? "—"}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`text-sm ${text.secondary}`}>{m.email}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span
                          className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${
                            m.manager_type === "regular" ? badge.location : badge.role
                          }`}
                        >
                          {typeLabel(m.manager_type)}
                        </span>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`text-sm ${text.secondary}`}>
                          {m.manager_type === "admin"
                            ? t.team.allLocations
                            : m.assigned_locations.length === 0
                              ? t.team.noLocationsAssigned
                              : m.assigned_locations
                                  .map((l) =>
                                    l.archived ? `${l.name} (${t.common.archived})` : l.name
                                  )
                                  .join(", ")}
                        </span>
                      </td>
                      <td className="px-4 py-2 text-end">
                        <button
                          type="button"
                          onClick={() => openEdit(m)}
                          className="text-accent hover:text-accent-light text-sm font-medium"
                        >
                          {t.team.editAccess}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h2 className={`text-lg font-semibold ${text.heading} mb-3`}>
            {t.team.invitesTitle}
          </h2>
          {invites.length === 0 ? (
            <div className={`glass-card p-6 text-sm ${text.muted}`}>
              {t.team.noInvites}
            </div>
          ) : (
            <div className="glass-card overflow-x-auto">
              <table className={`min-w-full divide-y ${border.divider}`}>
                <thead className={bg.tableHeader}>
                  <tr>
                    <th className={thClass}>{t.team.columnEmail}</th>
                    <th className={thClass}>{t.team.columnType}</th>
                    <th className={thClass}>{t.team.columnStatus}</th>
                    <th className={thClass}>{t.team.columnInvitedAt}</th>
                    <th className={thClass}>{t.team.columnAcceptedAt}</th>
                    <th className={thClass}>{t.team.columnCompany}</th>
                  </tr>
                </thead>
                <tbody className={`divide-y ${border.divider}`}>
                  {invites.map((inv) => (
                    <tr key={inv.id}>
                      <td className="px-4 py-2">
                        <span className={`text-sm ${text.body}`}>{inv.email}</span>
                      </td>
                      <td className="px-4 py-2">
                        <span className={`text-sm ${text.secondary}`}>
                          {typeLabel(inv.manager_type)}
                          {inv.manager_type === "regular" && (
                            <span className={`block text-xs ${text.muted}`}>
                              {inviteLocations(inv)}
                            </span>
                          )}
                        </span>
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
        </>
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
                <AccessFields
                  draft={inviteDraft}
                  onChange={setInviteDraft}
                  locations={locations}
                  name="invite-manager-type"
                />
                {modalError && (
                  <div className="glass-alert-error" role="alert">{modalError}</div>
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

      {editing && editDraft && (
        <div className="glass-modal-overlay">
          <div className="glass-modal w-full max-w-md mx-4">
            <form onSubmit={handleSaveAccess}>
              <div
                className={`flex items-center justify-between px-6 py-4 border-b ${border.default}`}
              >
                <h2 className={`text-lg font-semibold ${text.body}`}>
                  {t.team.editAccessTitle.replace("{name}", editing.full_name ?? editing.email)}
                </h2>
                <button
                  type="button"
                  onClick={closeEdit}
                  className={`${text.muted} hover:text-gray-300 text-xl leading-none`}
                >
                  &times;
                </button>
              </div>

              <div className="px-6 py-4 space-y-3">
                <AccessFields
                  draft={editDraft}
                  onChange={(d) => {
                    setEditDraft(d);
                    setEditError("");
                  }}
                  locations={locations}
                  name="edit-manager-type"
                />
                {editDraft.managerType === "regular" && hasArchivedAssignments(editing) && (
                  <p className={`text-xs ${text.muted}`}>{t.team.archivedAssignmentNote}</p>
                )}
                {editError && (
                  <div className="glass-alert-error" role="alert">{editError}</div>
                )}
              </div>

              <div
                className={`flex justify-end gap-2 px-6 py-4 border-t ${border.default}`}
              >
                <button
                  type="button"
                  onClick={closeEdit}
                  className="glass-btn-secondary px-4 py-2 text-sm font-medium"
                >
                  {t.team.cancel}
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="glass-btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
                >
                  {saving ? t.common.saving : t.common.save}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
