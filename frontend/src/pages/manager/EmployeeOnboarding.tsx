import { useCallback, useEffect, useState } from "react";
import {
  listEmployees,
  listInvites,
  sendInvite,
} from "../../api/employees";
import DemoGuard from "../../components/shared/DemoGuard";
import { useLanguage } from "../../i18n/LanguageContext";
import type { Employee, InviteStatusResponse } from "../../types";
import { text, bg, border } from "../../theme";

export default function EmployeeOnboarding() {
  const { t } = useLanguage();
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [invites, setInvites] = useState<InviteStatusResponse[]>([]);
  const [error, setError] = useState("");
  const [copiedLink, setCopiedLink] = useState<string | null>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [emps, invs] = await Promise.all([
        listEmployees(),
        listInvites(),
      ]);
      setEmployees(emps);
      setInvites(invs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const inviteStatusFor = (employeeId: string): InviteStatusResponse | undefined =>
    invites.find(
      (inv) => inv.employee_id === employeeId && inv.status === "pending"
    );

  const handleInvite = async (employeeId: string) => {
    setSendingId(employeeId);
    setError("");
    try {
      const result = await sendInvite(employeeId);
      await navigator.clipboard.writeText(result.invite_url);
      setCopiedLink(result.invite_url);
      setTimeout(() => setCopiedLink(null), 5000);
      await fetchData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to send invite");
    } finally {
      setSendingId(null);
    }
  };

  // Split employees into categories
  const needsInvite = employees.filter(
    (e) => e.user_id === null && !inviteStatusFor(e.id)
  );
  const pendingInvite = employees.filter(
    (e) => e.user_id === null && inviteStatusFor(e.id)
  );
  const onboarded = employees.filter((e) => e.user_id !== null);

  const statusBadge = (status: string) => {
    const styles: Record<string, string> = {
      active: "bg-emerald-500/15 text-emerald-300",
      pending: "bg-yellow-500/15 text-yellow-300",
      "needs invite": `${bg.tableHeader} ${text.muted}`,
    };
    const labelMap: Record<string, string> = {
      active: t.onboarding.statusActive,
      pending: t.onboarding.statusPending,
      "needs invite": t.onboarding.statusNeedsInvite,
    };
    return (
      <span
        className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] ?? styles["needs invite"]}`}
      >
        {labelMap[status] ?? status}
      </span>
    );
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className={`text-2xl font-bold ${text.heading}`}>{t.onboarding.title}</h1>
        <p className={`mt-1 text-sm ${text.muted}`}>
          {t.onboarding.subtitle}
        </p>
      </div>

      {/* How it works */}
      <div className="mb-6 rounded-xl border border-blue-400/20 bg-blue-500/10 p-4">
        <h2 className="text-sm font-semibold text-blue-300 mb-2">
          {t.onboarding.howItWorks}
        </h2>
        <ol className="list-decimal list-inside text-sm text-blue-300/80 space-y-1">
          <li>
            {t.onboarding.step1prefix}{" "}
            <a
              href="/manager/employees"
              className="underline font-medium"
            >
              {t.onboarding.step1link}
            </a>{" "}
            {t.onboarding.step1suffix}
          </li>
          <li>
            {t.onboarding.step2prefix} <strong>{t.onboarding.step2bold}</strong> {t.onboarding.step2suffix}
          </li>
          <li>
            {t.onboarding.step3}
          </li>
        </ol>
        <p className="mt-2 text-xs text-blue-400/60">
          {t.onboarding.expireNote}
        </p>
      </div>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      {copiedLink && (
        <div className="glass-alert-success mb-4">
          {t.onboarding.inviteCopied}
          <span className="block mt-1 text-xs break-all font-mono opacity-80">
            {copiedLink}
          </span>
        </div>
      )}

      {/* Needs Invite */}
      <Section
        title={t.onboarding.needsInvite}
        count={needsInvite.length}
        emptyMessage={t.onboarding.allInvitedOrOnboarded}
      >
        {needsInvite.map((emp) => (
          <EmployeeRow key={emp.id} employee={emp} status="needs invite" statusBadge={statusBadge}>
            {emp.email ? (
              <DemoGuard>
                <button
                  onClick={() => handleInvite(emp.id)}
                  disabled={sendingId === emp.id}
                  className="glass-btn-primary px-3 py-1 text-sm disabled:opacity-50"
                >
                  {sendingId === emp.id ? t.onboarding.sending : t.onboarding.sendInvite}
                </button>
              </DemoGuard>
            ) : (
              <span className={`text-xs ${text.muted} italic`}>
                {t.onboarding.noEmail}
              </span>
            )}
          </EmployeeRow>
        ))}
      </Section>

      {/* Pending */}
      <Section
        title={t.onboarding.inviteSentPending}
        count={pendingInvite.length}
        emptyMessage={t.onboarding.noPending}
      >
        {pendingInvite.map((emp) => {
          const inv = inviteStatusFor(emp.id);
          return (
            <EmployeeRow key={emp.id} employee={emp} status="pending" statusBadge={statusBadge}>
              <div className="flex items-center gap-2">
                <span className={`text-xs ${text.muted}`}>
                  {t.onboarding.expires}{" "}
                  {inv
                    ? new Date(inv.expires_at).toLocaleDateString()
                    : ""}
                </span>
                <DemoGuard>
                  <button
                    onClick={() => handleInvite(emp.id)}
                    disabled={sendingId === emp.id}
                    className="glass-btn-secondary px-3 py-1 text-sm disabled:opacity-50"
                  >
                    {sendingId === emp.id ? t.onboarding.sending : t.onboarding.resend}
                  </button>
                </DemoGuard>
              </div>
            </EmployeeRow>
          );
        })}
      </Section>

      {/* Onboarded */}
      <Section
        title={t.onboarding.onboarded}
        count={onboarded.length}
        emptyMessage={t.onboarding.noOnboarded}
      >
        {onboarded.map((emp) => (
          <EmployeeRow key={emp.id} employee={emp} status="active" statusBadge={statusBadge}>
            <span className="text-xs text-emerald-400">{t.onboarding.canLogIn}</span>
          </EmployeeRow>
        ))}
      </Section>
    </div>
  );
}

function Section({
  title,
  count,
  emptyMessage,
  children,
}: {
  title: string;
  count: number;
  emptyMessage: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <h2 className={`text-lg font-semibold ${text.heading} mb-2`}>
        {title}{" "}
        <span className={`text-sm font-normal ${text.muted}`}>({count})</span>
      </h2>
      <div className={`glass-card divide-y ${border.divider}`}>
        {count === 0 ? (
          <div className={`px-4 py-3 text-sm ${text.muted} italic`}>
            {emptyMessage}
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

function EmployeeRow({
  employee,
  status,
  statusBadge,
  children,
}: {
  employee: Employee;
  status: string;
  statusBadge: (s: string) => React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between px-4 py-3">
      <div className="flex items-center gap-3">
        <div>
          <div className={`text-sm font-medium ${text.body}`}>{employee.full_name}</div>
          <div className={`text-xs ${text.muted}`}>
            {employee.email ?? "No email"}
          </div>
        </div>
        {statusBadge(status)}
      </div>
      <div>{children}</div>
    </div>
  );
}
