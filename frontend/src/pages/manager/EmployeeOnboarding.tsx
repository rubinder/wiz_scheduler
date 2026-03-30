import { useCallback, useEffect, useState } from "react";
import {
  listEmployees,
  listInvites,
  sendInvite,
} from "../../api/employees";
import type { Employee, InviteStatusResponse } from "../../types";

export default function EmployeeOnboarding() {
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
      active: "bg-green-100 text-green-700",
      pending: "bg-yellow-100 text-yellow-700",
      "needs invite": "bg-gray-100 text-gray-600",
    };
    return (
      <span
        className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] ?? styles["needs invite"]}`}
      >
        {status}
      </span>
    );
  };

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Employee Onboarding</h1>
        <p className="mt-1 text-sm text-gray-500">
          Send invite links to imported employees so they can create a login and
          manage their own availability.
        </p>
      </div>

      {/* How it works */}
      <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4">
        <h2 className="text-sm font-semibold text-blue-800 mb-2">
          How onboarding works
        </h2>
        <ol className="list-decimal list-inside text-sm text-blue-700 space-y-1">
          <li>
            Import or manually create employees on the{" "}
            <a
              href="/manager/employees"
              className="underline font-medium"
            >
              Employees
            </a>{" "}
            page (make sure each has an email).
          </li>
          <li>
            Click <strong>Send Invite</strong> below to generate a unique login
            link. The link is copied to your clipboard automatically.
          </li>
          <li>
            Share the link with the employee. They set a password, and can then
            log in to manage their availability.
          </li>
        </ol>
        <p className="mt-2 text-xs text-blue-600">
          Invite links expire after 7 days. You can re-send at any time to
          generate a fresh link.
        </p>
      </div>

      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}

      {copiedLink && (
        <div className="mb-4 p-3 bg-green-50 text-green-700 rounded text-sm">
          Invite link copied to clipboard!
          <span className="block mt-1 text-xs text-green-600 break-all font-mono">
            {copiedLink}
          </span>
        </div>
      )}

      {/* Needs Invite */}
      <Section
        title="Needs Invite"
        count={needsInvite.length}
        emptyMessage="All employees have been invited or onboarded."
      >
        {needsInvite.map((emp) => (
          <EmployeeRow key={emp.id} employee={emp} status="needs invite" statusBadge={statusBadge}>
            {emp.email ? (
              <button
                onClick={() => handleInvite(emp.id)}
                disabled={sendingId === emp.id}
                className="px-3 py-1 bg-indigo-600 text-white rounded text-sm hover:bg-indigo-700 disabled:opacity-50"
              >
                {sendingId === emp.id ? "Sending..." : "Send Invite"}
              </button>
            ) : (
              <span className="text-xs text-gray-400 italic">
                No email — add one on the Employees page
              </span>
            )}
          </EmployeeRow>
        ))}
      </Section>

      {/* Pending */}
      <Section
        title="Invite Sent (Pending)"
        count={pendingInvite.length}
        emptyMessage="No pending invites."
      >
        {pendingInvite.map((emp) => {
          const inv = inviteStatusFor(emp.id);
          return (
            <EmployeeRow key={emp.id} employee={emp} status="pending" statusBadge={statusBadge}>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500">
                  Expires{" "}
                  {inv
                    ? new Date(inv.expires_at).toLocaleDateString()
                    : ""}
                </span>
                <button
                  onClick={() => handleInvite(emp.id)}
                  disabled={sendingId === emp.id}
                  className="px-3 py-1 bg-gray-200 text-gray-700 rounded text-sm hover:bg-gray-300 disabled:opacity-50"
                >
                  {sendingId === emp.id ? "Sending..." : "Re-send"}
                </button>
              </div>
            </EmployeeRow>
          );
        })}
      </Section>

      {/* Onboarded */}
      <Section
        title="Onboarded"
        count={onboarded.length}
        emptyMessage="No employees have completed onboarding yet."
      >
        {onboarded.map((emp) => (
          <EmployeeRow key={emp.id} employee={emp} status="active" statusBadge={statusBadge}>
            <span className="text-xs text-green-600">Can log in</span>
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
      <h2 className="text-lg font-semibold mb-2">
        {title}{" "}
        <span className="text-sm font-normal text-gray-500">({count})</span>
      </h2>
      <div className="bg-white rounded-lg shadow divide-y divide-gray-200">
        {count === 0 ? (
          <div className="px-4 py-3 text-sm text-gray-400 italic">
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
          <div className="text-sm font-medium">{employee.full_name}</div>
          <div className="text-xs text-gray-500">
            {employee.email ?? "No email"}
          </div>
        </div>
        {statusBadge(status)}
      </div>
      <div>{children}</div>
    </div>
  );
}
