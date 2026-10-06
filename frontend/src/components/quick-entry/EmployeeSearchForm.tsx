import { useState } from "react";
import * as employeesApi from "../../api/employees";
import type { Employee, Location, Role } from "../../types";
import { text, action } from "../../theme";
import EmployeeFormFields from "./EmployeeFormFields";

interface Props {
  employees: Employee[];
  roles: Role[];
  locations: Location[];
  onSuccess: () => void;
}

type Step = "search" | "form";

export default function EmployeeSearchForm({
  employees,
  roles,
  locations,
  onSuccess,
}: Props) {
  const [step, setStep] = useState<Step>("search");
  const [query, setQuery] = useState("");
  const [selectedEmployee, setSelectedEmployee] = useState<Employee | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState("");

  const filtered = employees.filter((e) =>
    e.full_name.toLowerCase().includes(query.toLowerCase())
  );

  const handleSelectExisting = (emp: Employee) => {
    setSelectedEmployee(emp);
    setStep("form");
  };

  const handleNewEmployee = () => {
    setSelectedEmployee(null);
    setStep("form");
  };

  const handleFormSubmit = async (formData: {
    full_name: string;
    email: string | null;
    location_ids: string[];
    roles: { role_id: string; skill_level: number }[];
    max_hours_per_week: number | null;
    pay_rate: number | null;
    hire_date: string | null;
    seniority_rank: number | null;
  }) => {
    try {
      setIsCreating(true);
      setError("");

      if (selectedEmployee) {
        // Update existing
        await employeesApi.updateEmployee(selectedEmployee.id, formData);
      } else {
        // Create new
        await employeesApi.createEmployee(formData);
      }

      onSuccess();
      setStep("search");
      setQuery("");
      setSelectedEmployee(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save employee");
    } finally {
      setIsCreating(false);
    }
  };

  if (step === "form") {
    return (
      <div>
        <div className="mb-6">
          <button
            onClick={() => {
              setStep("search");
              setSelectedEmployee(null);
            }}
            className={`text-sm ${action.link}`}
          >
            ← Back
          </button>
        </div>

        <EmployeeFormFields
          initialData={
            selectedEmployee
              ? {
                  full_name: selectedEmployee.full_name,
                  email: selectedEmployee.email ?? null,
                  location_ids: selectedEmployee.location_ids ?? [],
                  roles: selectedEmployee.roles.map((r) => ({
                    role_id: r.id,
                    skill_level: r.skill_level,
                  })),
                  max_hours_per_week: selectedEmployee.max_hours_per_week ?? null,
                  pay_rate: selectedEmployee.pay_rate ?? null,
                  hire_date: selectedEmployee.hire_date ?? null,
                  seniority_rank: selectedEmployee.seniority_rank ?? null,
                }
              : undefined
          }
          roles={roles}
          locations={locations}
          onSubmit={handleFormSubmit}
          isLoading={isCreating}
          isNewEmployee={!selectedEmployee}
        />
      </div>
    );
  }

  return (
    <div>
      <h2 className={`text-xl font-semibold ${text.heading} mb-4`}>
        {selectedEmployee ? "Edit Existing Employee" : "New Employee"}
      </h2>

      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}

      <div className="mb-6">
        <label className={`text-sm font-medium ${text.body}`}>Search for employee</label>
        <input
          type="text"
          placeholder="Type employee name..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="glass-input w-full mt-2"
        />
      </div>

      {query && filtered.length > 0 && (
        <div className="mb-6">
          <div className={`text-sm font-medium ${text.body}`}>Found employees</div>
          <div className="border border-sage/20 rounded-lg p-4 space-y-2 mt-2">
            {filtered.map((emp) => (
              <button
                key={emp.id}
                onClick={() => handleSelectExisting(emp)}
                className="w-full text-left px-4 py-3 rounded-lg hover:bg-sage/10 transition-colors"
              >
                <div className={`text-sm ${text.body}`}>{emp.full_name}</div>
                {emp.email && <div className={`text-xs ${text.muted}`}>{emp.email}</div>}
              </button>
            ))}
          </div>
        </div>
      )}

      {query && filtered.length === 0 && (
        <div className="border border-sage/20 rounded-lg p-4 mb-6 bg-sage/5">
          <p className={`text-sm ${text.muted}`}>No employees found matching "{query}"</p>
        </div>
      )}

      <button
        onClick={handleNewEmployee}
        className="glass-btn-primary"
      >
        Create New Employee
      </button>
    </div>
  );
}
