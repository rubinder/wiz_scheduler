import { useState } from "react";
import type { Location, Role } from "../../types";
import { text } from "../../theme";

interface RoleAssignment {
  role_id: string;
  skill_level: number;
}

interface FormData {
  full_name: string;
  email: string | null;
  location_ids: string[];
  roles: RoleAssignment[];
  max_hours_per_week: number | null;
  pay_rate: number | null;
  hire_date: string | null;
  seniority_rank: number | null;
}

interface Props {
  initialData?: FormData;
  roles: Role[];
  locations: Location[];
  onSubmit: (data: FormData) => Promise<void>;
  isLoading?: boolean;
  isNewEmployee?: boolean;
}

export default function EmployeeFormFields({
  initialData,
  roles,
  locations,
  onSubmit,
  isLoading = false,
  isNewEmployee = true,
}: Props) {
  const [formData, setFormData] = useState<FormData>(
    initialData || {
      full_name: "",
      email: null,
      location_ids: [],
      roles: [],
      max_hours_per_week: null,
      pay_rate: null,
      hire_date: null,
      seniority_rank: null,
    }
  );
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!formData.full_name.trim()) {
      setError("Employee name is required");
      return;
    }

    try {
      await onSubmit(formData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save");
    }
  };

  const updateField = <K extends keyof FormData>(key: K, value: FormData[K]) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const toggleLocation = (locId: string) => {
    setFormData((prev) => ({
      ...prev,
      location_ids: prev.location_ids.includes(locId)
        ? prev.location_ids.filter((id) => id !== locId)
        : [...prev.location_ids, locId],
    }));
  };

  const toggleRole = (roleId: string) => {
    setFormData((prev) => {
      const existing = prev.roles.find((r) => r.role_id === roleId);
      if (existing) {
        return {
          ...prev,
          roles: prev.roles.filter((r) => r.role_id !== roleId),
        };
      } else {
        return {
          ...prev,
          roles: [...prev.roles, { role_id: roleId, skill_level: 1 }],
        };
      }
    });
  };

  const updateRoleSkillLevel = (roleId: string, skill_level: number) => {
    setFormData((prev) => ({
      ...prev,
      roles: prev.roles.map((r) =>
        r.role_id === roleId ? { ...r, skill_level } : r
      ),
    }));
  };

  return (
    <form onSubmit={handleSubmit}>
      {error && (
        <div className="glass-alert-error mb-6">
          {error}
        </div>
      )}

      {/* Employee Information Section */}
      <div className="mb-8">
        <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Employee Information</h3>

        <div className="space-y-4">
          <div>
            <label className={`text-sm font-medium ${text.body}`}>Full Name *</label>
            <input
              type="text"
              value={formData.full_name}
              onChange={(e) => updateField("full_name", e.target.value)}
              className="glass-input w-full mt-2"
            />
          </div>

          <div>
            <label className={`text-sm font-medium ${text.body}`}>Email</label>
            <input
              type="email"
              value={formData.email ?? ""}
              onChange={(e) => updateField("email", e.target.value || null)}
              className="glass-input w-full mt-2"
            />
          </div>

          <div>
            <label className={`text-sm font-medium ${text.body}`}>Hire Date</label>
            <input
              type="date"
              value={formData.hire_date ?? ""}
              onChange={(e) => updateField("hire_date", e.target.value || null)}
              className="glass-input w-full mt-2"
            />
          </div>

          <div>
            <label className={`text-sm font-medium ${text.body}`}>Seniority Rank</label>
            <input
              type="number"
              min="0"
              value={formData.seniority_rank ?? ""}
              onChange={(e) => updateField("seniority_rank", e.target.value ? parseInt(e.target.value) : null)}
              className="glass-input w-full mt-2"
            />
          </div>

          <div>
            <label className={`text-sm font-medium ${text.body}`}>Pay Rate</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={formData.pay_rate ?? ""}
              onChange={(e) => updateField("pay_rate", e.target.value ? parseFloat(e.target.value) : null)}
              className="glass-input w-full mt-2"
            />
          </div>
        </div>
      </div>

      {/* Roles Section */}
      <div className="mb-8">
        <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Roles</h3>
        <div className="space-y-3">
          {roles.map((role) => {
            const assignment = formData.roles.find((r) => r.role_id === role.id);
            return (
              <div key={role.id} className="border border-sage/20 rounded-lg p-4">
                <label className="flex items-center space-x-3">
                  <input
                    type="checkbox"
                    checked={!!assignment}
                    onChange={() => toggleRole(role.id)}
                    className="rounded"
                  />
                  <span className={`text-sm ${text.body}`}>{role.name}</span>
                </label>
                {assignment && (
                  <div className="mt-3 ms-7">
                    <label className={`text-sm font-medium ${text.body} block mb-2`}>Skill Level</label>
                    <input
                      type="range"
                      min="1"
                      max="5"
                      value={assignment.skill_level}
                      onChange={(e) => updateRoleSkillLevel(role.id, parseInt(e.target.value))}
                      className="w-full"
                    />
                    <div className={`text-xs ${text.muted} text-center mt-1`}>
                      Level {assignment.skill_level}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Locations Section */}
      <div className="mb-8">
        <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Work Locations</h3>
        <div className="space-y-3">
          {locations.map((loc) => (
            <label key={loc.id} className="flex items-center space-x-3">
              <input
                type="checkbox"
                checked={formData.location_ids.includes(loc.id)}
                onChange={() => toggleLocation(loc.id)}
                className="rounded"
              />
              <span className={`text-sm ${text.body}`}>{loc.name}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Hour Restrictions Section */}
      <div className="mb-8">
        <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Hour Restrictions</h3>
        <div>
          <label className={`text-sm font-medium ${text.body}`}>Max Hours Per Week</label>
          <input
            type="number"
            min="0"
            max="168"
            value={formData.max_hours_per_week ?? ""}
            onChange={(e) => updateField("max_hours_per_week", e.target.value ? parseFloat(e.target.value) : null)}
            className="glass-input w-full mt-2"
          />
          <p className={`text-xs ${text.muted} mt-1`}>Leave blank for no limit</p>
        </div>
      </div>

      {/* Submit Buttons */}
      <div className="flex gap-3 pt-6 border-t border-sage/20">
        <button
          type="submit"
          disabled={isLoading}
          className="glass-btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "Saving..." : isNewEmployee ? "Create Employee" : "Update Employee"}
        </button>
      </div>
    </form>
  );
}
