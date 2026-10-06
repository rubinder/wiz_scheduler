import { useCallback, useEffect, useState } from "react";
import * as employeesApi from "../../api/employees";
import * as rolesApi from "../../api/roles";
import * as locationsApi from "../../api/locations";
import { usePlan } from "../../hooks/usePlan";
import type {
  Employee,
  Location,
  Role,
} from "../../types";
import { text } from "../../theme";
import EmployeeSearchForm from "../../components/quick-entry/EmployeeSearchForm";
import AffinityForm from "../../components/quick-entry/AffinityForm";
import UpdatePreferencesForm from "../../components/quick-entry/UpdatePreferencesForm";

type Workflow = "new" | "conflict" | "mentoring" | "update";

interface WorkflowButton {
  id: Workflow;
  label: string;
  description: string;
  color: string;
}

const workflows: WorkflowButton[] = [
  {
    id: "new",
    label: "New Employee",
    description: "Add a new employee to the system",
    color: "bg-blue-50 border-blue-200 hover:bg-blue-100",
  },
  {
    id: "conflict",
    label: "Conflict",
    description: "Record a conflict between two employees",
    color: "bg-orange-50 border-orange-200 hover:bg-orange-100",
  },
  {
    id: "mentoring",
    label: "Mentoring",
    description: "Record a mentoring/shadowing relationship",
    color: "bg-green-50 border-green-200 hover:bg-green-100",
  },
  {
    id: "update",
    label: "Update Preferences",
    description: "Update an employee's preferences and settings",
    color: "bg-gray-50 border-gray-200 hover:bg-gray-100",
  },
];

export default function QuickEntry() {
  const { plan } = usePlan();
  const [workflow, setWorkflow] = useState<Workflow>("new");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [locations, setLocations] = useState<Location[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const [emps, rls, locs] = await Promise.all([
        employeesApi.listEmployees(),
        rolesApi.listRoles(),
        locationsApi.listLocations(),
      ]);
      setEmployees(emps);
      setRoles(rls);
      setLocations(locs);
      setError("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Check if user is on paid plan
  const isPaidPlan = plan?.plan !== "free";

  if (!isPaidPlan) {
    return (
      <div>
        <div className="mb-8">
          <h1 className={`text-2xl font-bold ${text.heading}`}>Quick Entry</h1>
        </div>
        <div className="glass-card p-6">
          <p className={`text-sm ${text.body}`}>
            Quick Entry is available on paid plans. Upgrade to unlock faster entry workflows.
          </p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div>
        <p className={`text-sm ${text.body}`}>Loading...</p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className={`text-2xl font-bold ${text.heading}`}>Quick Entry</h1>
        <p className={`text-sm ${text.muted}`}>Consolidate common manager workflows into a single form</p>
      </div>

      {error && (
        <div className="glass-alert-error mb-6">
          {error}
        </div>
      )}

      {/* Workflow selector */}
      <div className="grid grid-cols-2 gap-4 mb-8">
        {workflows.map((wf) => (
          <button
            key={wf.id}
            onClick={() => setWorkflow(wf.id)}
            className={`
              ${wf.color}
              border rounded-lg p-4 text-left transition-all
              ${workflow === wf.id ? `ring-2 ring-blue-500` : ""}
            `}
          >
            <div className="font-semibold text-sm">{wf.label}</div>
            <div className="text-xs text-gray-600">{wf.description}</div>
          </button>
        ))}
      </div>

      {/* Form section */}
      <div className="glass-card p-8">
          {workflow === "new" && (
            <EmployeeSearchForm
              employees={employees}
              roles={roles}
              locations={locations}
              onSuccess={() => {
                fetchData();
                setWorkflow("new");
              }}
            />
          )}

          {workflow === "conflict" && (
            <AffinityForm
              employees={employees}
              type="conflict"
              onSuccess={() => fetchData()}
            />
          )}

          {workflow === "mentoring" && (
            <AffinityForm
              employees={employees}
              type="mentoring"
              onSuccess={() => fetchData()}
            />
          )}

          {workflow === "update" && (
            <UpdatePreferencesForm
              employees={employees}
              roles={roles}
              locations={locations}
              onSuccess={() => fetchData()}
            />
          )}
        </div>
    </div>
  );
}
