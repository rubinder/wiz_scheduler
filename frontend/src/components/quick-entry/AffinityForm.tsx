import { useState } from "react";
import * as affinitiesApi from "../../api/affinities";
import type { Employee } from "../../types";
import { text } from "../../theme";
import EmployeeSearchBox from "../shared/EmployeeSearchBox";

interface Props {
  employees: Employee[];
  type: "conflict" | "mentoring";
  onSuccess: () => void;
}

export default function AffinityForm({ employees, type, onSuccess }: Props) {
  const [employeeA, setEmployeeA] = useState("");
  const [employeeB, setEmployeeB] = useState("");
  const [expirationDays, setExpirationDays] = useState(14);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const title =
    type === "conflict"
      ? "Record a Conflict Between Employees"
      : "Record a Mentoring Relationship";

  const description =
    type === "conflict"
      ? "Create a negative affinity between two employees"
      : "Create a positive affinity between mentor and trainee";

  const level = type === "conflict" ? -0.7 : 0.7;
  const levelLabel = type === "conflict" ? "Cannot work together" : "Should work together";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!employeeA || !employeeB) {
      setError("Both employees must be selected");
      return;
    }

    if (employeeA === employeeB) {
      setError("Cannot create affinity with the same employee");
      return;
    }

    try {
      setSaving(true);

      const today = new Date();
      const expiration = new Date(today);
      expiration.setDate(expiration.getDate() + expirationDays);
      const expirationDate = expiration.toISOString().split("T")[0];

      await affinitiesApi.createAffinity({
        employee_id: employeeA,
        target_employee_id: employeeB,
        level,
        entry_date: today.toISOString().split("T")[0],
        expiration_date: expirationDate,
      });

      onSuccess();
      setEmployeeA("");
      setEmployeeB("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save affinity");
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <h2 className={`text-xl font-semibold ${text.heading} mb-2`}>{title}</h2>
      <p className={`text-sm ${text.muted} mb-6`}>{description}</p>

      {error && (
        <div className="glass-alert-error mb-6">
          {error}
        </div>
      )}

      <div className="space-y-6">
        {/* Affinity Level Display */}
        <div className="bg-sage/5 border border-sage/20 rounded-lg p-4">
          <div className={`text-sm font-medium ${text.body}`}>Affinity Level</div>
          <div className={`text-lg font-semibold ${text.heading} mt-2`}>{levelLabel}</div>
          <div className={`text-xs ${text.muted} mt-1`}>
            {type === "conflict"
              ? "These employees should not be scheduled together"
              : "These employees work well together and should be scheduled together"}
          </div>
        </div>

        {/* Employee A Selection */}
        <div>
          <label className={`text-sm font-medium ${text.body}`}>
            {type === "conflict" ? "Employee A" : "Mentor"}
          </label>
          <div className="mt-2">
            <EmployeeSearchBox
              employees={employees}
              value={employeeA}
              onChange={setEmployeeA}
              excludeIds={employeeB ? [employeeB] : []}
              placeholder={
                type === "conflict" ? "Select first employee..." : "Select mentor..."
              }
            />
          </div>
        </div>

        {/* Employee B Selection */}
        <div>
          <label className={`text-sm font-medium ${text.body}`}>
            {type === "conflict" ? "Employee B" : "Trainee"}
          </label>
          <div className="mt-2">
            <EmployeeSearchBox
              employees={employees}
              value={employeeB}
              onChange={setEmployeeB}
              excludeIds={employeeA ? [employeeA] : []}
              placeholder={
                type === "conflict" ? "Select second employee..." : "Select trainee..."
              }
            />
          </div>
        </div>

        {/* Duration */}
        <div>
          <label className={`text-sm font-medium ${text.body}`}>Duration (days)</label>
          <div className="mt-2 flex items-center gap-4">
            <input
              type="range"
              min="1"
              max="365"
              value={expirationDays}
              onChange={(e) => setExpirationDays(parseInt(e.target.value))}
              className="flex-1"
            />
            <div className={`text-sm ${text.body} min-w-fit`}>{expirationDays} days</div>
          </div>
          <p className={`text-xs ${text.muted} mt-2`}>
            This affinity will expire{" "}
            {expirationDays === 1 ? "tomorrow" : `in ${expirationDays} days`}
          </p>
        </div>
      </div>

      {/* Submit */}
      <div className="flex gap-3 pt-6 border-t border-sage/20 mt-6">
        <button
          type="submit"
          disabled={saving}
          className="glass-btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? "Saving..." : "Save Affinity"}
        </button>
      </div>
    </form>
  );
}
