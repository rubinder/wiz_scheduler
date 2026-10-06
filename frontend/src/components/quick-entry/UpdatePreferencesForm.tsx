import { useEffect, useState, useMemo } from "react";
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

type Step = "select" | "edit";

export default function UpdatePreferencesForm({
  employees,
  roles,
  locations,
  onSuccess,
}: Props) {
  const [step, setStep] = useState<Step>("select");
  const [selectedLocationId, setSelectedLocationId] = useState<string>("");
  const [filteredEmployees, setFilteredEmployees] = useState<Employee[]>([]);
  const [selectedEmployee, setSelectedEmployee] = useState<Employee | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [isUpdating, setIsUpdating] = useState(false);
  const [error, setError] = useState("");

  // Filter employees by selected location
  useEffect(() => {
    if (selectedLocationId) {
      const filtered = employees.filter((e) =>
        e.location_ids?.includes(selectedLocationId)
      );
      setFilteredEmployees(filtered);
    } else {
      setFilteredEmployees(employees);
    }
    setSearchQuery("");
  }, [selectedLocationId, employees]);

  // Filter search results
  const searchResults = useMemo(() => {
    if (!searchQuery) return filteredEmployees;
    return filteredEmployees.filter((e) =>
      e.full_name.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [filteredEmployees, searchQuery]);

  const handleSelectEmployee = (emp: Employee) => {
    setSelectedEmployee(emp);
    setStep("edit");
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
    if (!selectedEmployee) return;

    try {
      setIsUpdating(true);
      setError("");
      await employeesApi.updateEmployee(selectedEmployee.id, formData);
      onSuccess();
      setStep("select");
      setSelectedLocationId("");
      setSearchQuery("");
      setSelectedEmployee(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update employee");
    } finally {
      setIsUpdating(false);
    }
  };

  if (step === "edit" && selectedEmployee) {
    return (
      <div>
        <div className="mb-6">
          <button
            onClick={() => {
              setStep("select");
              setSelectedEmployee(null);
            }}
            className={`text-sm ${action.link}`}
          >
            ← Back
          </button>
        </div>

        <EmployeeFormFields
          initialData={{
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
          }}
          roles={roles}
          locations={locations}
          onSubmit={handleFormSubmit}
          isLoading={isUpdating}
          isNewEmployee={false}
        />
      </div>
    );
  }

  return (
    <div>
      <h2 className={`text-xl font-semibold ${text.heading} mb-2`}>Update Employee Preferences</h2>
      <p className={`text-sm ${text.muted} mb-6`}>
        Select an employee to update their information and preferences
      </p>

      {error && (
        <div className="glass-alert-error mb-6">
          {error}
        </div>
      )}

      <div className="space-y-6">
        {/* Location Filter */}
        <div>
          <label className={`text-sm font-medium ${text.body}`}>Filter by Location (optional)</label>
          <select
            value={selectedLocationId}
            onChange={(e) => setSelectedLocationId(e.target.value)}
            className="glass-select w-full mt-2"
          >
            <option value="">All Locations</option>
            {locations.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.name}
              </option>
            ))}
          </select>
        </div>

        {/* Employee Search */}
        <div>
          <label className={`text-sm font-medium ${text.body}`}>
            Search Employee
            {selectedLocationId && (
              <span className={`text-xs ${text.muted}`}>
                {" "}
                ({filteredEmployees.length} employees at selected location)
              </span>
            )}
          </label>
          <input
            type="text"
            placeholder="Type employee name..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="glass-input w-full mt-2"
          />
        </div>

        {/* Results */}
        {searchQuery && searchResults.length > 0 && (
          <div>
            <div className={`text-sm font-medium ${text.body}`}>Found employees</div>
            <div className="border border-sage/20 rounded-lg p-4 space-y-2 mt-2">
              {searchResults.map((emp) => (
                <button
                  key={emp.id}
                  type="button"
                  onClick={() => handleSelectEmployee(emp)}
                  className="w-full text-start px-4 py-3 rounded-lg hover:bg-sage/10 transition-colors"
                >
                  <div className={`text-sm ${text.body}`}>{emp.full_name}</div>
                  {emp.email && <div className={`text-xs ${text.muted}`}>{emp.email}</div>}
                </button>
              ))}
            </div>
          </div>
        )}

        {searchQuery && searchResults.length === 0 && (
          <div className="border border-sage/20 rounded-lg p-4 bg-sage/5">
            <p className={`text-sm ${text.muted}`}>No employees found matching "{searchQuery}"</p>
          </div>
        )}

        {!searchQuery && filteredEmployees.length > 0 && (
          <div>
            <div className={`text-sm font-medium ${text.body}`}>All employees</div>
            <div className="border border-sage/20 rounded-lg p-4 space-y-2 mt-2">
              {filteredEmployees.map((emp) => (
                <button
                  key={emp.id}
                  type="button"
                  onClick={() => handleSelectEmployee(emp)}
                  className="w-full text-start px-4 py-3 rounded-lg hover:bg-sage/10 transition-colors"
                >
                  <div className={`text-sm ${text.body}`}>{emp.full_name}</div>
                  {emp.email && <div className={`text-xs ${text.muted}`}>{emp.email}</div>}
                </button>
              ))}
            </div>
          </div>
        )}

        {filteredEmployees.length === 0 && selectedLocationId && (
          <div className="border border-sage/20 rounded-lg p-4 bg-sage/5">
            <p className={`text-sm ${text.muted}`}>
              No employees found at the selected location
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
