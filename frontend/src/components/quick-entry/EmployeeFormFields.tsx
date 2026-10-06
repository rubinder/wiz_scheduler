import { useEffect, useState } from "react";
import type {
  Location,
  Role,
  Employee,
  EmployeeAffinity,
  EmployeeDayPreference,
  EmployeeHourRangePreference,
  EmployeeHourRangeCap,
} from "../../types";
import { text } from "../../theme";
import * as schedulingPreferencesApi from "../../api/schedulingPreferences";
import * as affinitiesApi from "../../api/affinities";

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
  employees?: Employee[];
  employeeId?: string;
  onSubmit: (data: FormData) => Promise<void>;
  isLoading?: boolean;
  isNewEmployee?: boolean;
}

const dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function EmployeeFormFieldsEnhanced({
  initialData,
  roles,
  locations,
  employees = [],
  employeeId,
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

  // Preference state
  const [affinities, setAffinities] = useState<EmployeeAffinity[]>([]);
  const [dayPreferences, setDayPreferences] = useState<EmployeeDayPreference[]>([]);
  const [hourRangePreferences, setHourRangePreferences] = useState<EmployeeHourRangePreference[]>([]);
  const [hourRangeCaps, setHourRangeCaps] = useState<EmployeeHourRangeCap[]>([]);
  const [preferencesLoading, setPreferencesLoading] = useState(false);

  // Track original preferences for change detection
  const [originalAffinities, setOriginalAffinities] = useState<EmployeeAffinity[]>([]);
  const [originalDayPreferences, setOriginalDayPreferences] = useState<EmployeeDayPreference[]>([]);
  const [originalHourRangePreferences, setOriginalHourRangePreferences] = useState<EmployeeHourRangePreference[]>([]);
  const [originalHourRangeCaps, setOriginalHourRangeCaps] = useState<EmployeeHourRangeCap[]>([]);

  // New preference forms
  const [newAffinity, setNewAffinity] = useState<{ targetEmployeeId: string; level: number }>({
    targetEmployeeId: "",
    level: 1,
  });
  const [newHourRangePreference, setNewHourRangePreference] = useState({
    start_time: "09:00",
    end_time: "17:00",
    weight: 1,
  });
  const [newHourRangeCap, setNewHourRangeCap] = useState({
    start_time: "09:00",
    end_time: "17:00",
    max_per_week: 5,
    weight: 1,
  });

  // Load existing preferences
  useEffect(() => {
    if (!employeeId) return;

    const loadPreferences = async () => {
      try {
        setPreferencesLoading(true);
        const [affs, dayPrefs, hourPrefs, caps] = await Promise.all([
          affinitiesApi.listAffinities().catch(() => []),
          schedulingPreferencesApi.listDayPreferences().catch(() => []),
          schedulingPreferencesApi.listHourRangePreferences().catch(() => []),
          schedulingPreferencesApi.listHourRangeCaps().catch(() => []),
        ]);

        const filteredAffinities = affs.filter((a) => a.employee_id === employeeId);
        const filteredDayPrefs = dayPrefs.filter((p) => p.employee_id === employeeId);
        const filteredHourPrefs = hourPrefs.filter((p) => p.employee_id === employeeId);
        const filteredCaps = caps.filter((c) => c.employee_id === employeeId);

        setAffinities(filteredAffinities);
        setDayPreferences(filteredDayPrefs);
        setHourRangePreferences(filteredHourPrefs);
        setHourRangeCaps(filteredCaps);

        // Store original state for change detection
        setOriginalAffinities(filteredAffinities);
        setOriginalDayPreferences(filteredDayPrefs);
        setOriginalHourRangePreferences(filteredHourPrefs);
        setOriginalHourRangeCaps(filteredCaps);
      } catch (err) {
        console.error("Failed to load preferences:", err);
      } finally {
        setPreferencesLoading(false);
      }
    };

    loadPreferences();
  }, [employeeId]);

  const persistPreferences = async (empId: string) => {
    // Persist affinities
    for (const aff of affinities) {
      const isNew = aff.id.startsWith("temp-");
      try {
        if (isNew) {
          await affinitiesApi.createAffinity({
            employee_id: empId,
            target_employee_id: aff.target_employee_id,
            level: aff.level,
            entry_date: aff.entry_date,
            expiration_date: aff.expiration_date,
          });
        } else {
          const originalAff = originalAffinities.find((a) => a.id === aff.id);
          if (originalAff && (originalAff.level !== aff.level || originalAff.expiration_date !== aff.expiration_date)) {
            await affinitiesApi.updateAffinity(aff.id, {
              level: aff.level,
              expiration_date: aff.expiration_date,
            });
          }
        }
      } catch (err) {
        console.error("Failed to persist affinity:", err);
        throw new Error(`Failed to save affinity: ${err instanceof Error ? err.message : "Unknown error"}`);
      }
    }

    // Delete removed affinities
    for (const original of originalAffinities) {
      if (!affinities.find((a) => a.id === original.id)) {
        try {
          await affinitiesApi.deleteAffinity(original.id);
        } catch (err) {
          console.error("Failed to delete affinity:", err);
        }
      }
    }

    // Persist day preferences
    for (const dayPref of dayPreferences) {
      const isNew = dayPref.id.startsWith("temp-");
      try {
        if (isNew) {
          await schedulingPreferencesApi.createDayPreference({
            employee_id: empId,
            day_of_week: dayPref.day_of_week,
            weight: dayPref.weight,
          });
        } else {
          const originalPref = originalDayPreferences.find((p) => p.id === dayPref.id);
          if (originalPref && originalPref.weight !== dayPref.weight) {
            await schedulingPreferencesApi.updateDayPreference(dayPref.id, {
              weight: dayPref.weight,
            });
          }
        }
      } catch (err) {
        console.error("Failed to persist day preference:", err);
        throw new Error(`Failed to save day preference: ${err instanceof Error ? err.message : "Unknown error"}`);
      }
    }

    // Delete removed day preferences
    for (const original of originalDayPreferences) {
      if (!dayPreferences.find((p) => p.id === original.id)) {
        try {
          await schedulingPreferencesApi.deleteDayPreference(original.id);
        } catch (err) {
          console.error("Failed to delete day preference:", err);
        }
      }
    }

    // Persist hour range preferences
    for (const hourPref of hourRangePreferences) {
      const isNew = hourPref.id.startsWith("temp-");
      try {
        if (isNew) {
          await schedulingPreferencesApi.createHourRangePreference({
            employee_id: empId,
            start_time: hourPref.start_time,
            end_time: hourPref.end_time,
            weight: hourPref.weight,
          });
        } else {
          const originalPref = originalHourRangePreferences.find((p) => p.id === hourPref.id);
          if (originalPref && (originalPref.weight !== hourPref.weight || originalPref.start_time !== hourPref.start_time || originalPref.end_time !== hourPref.end_time)) {
            await schedulingPreferencesApi.updateHourRangePreference(hourPref.id, {
              start_time: hourPref.start_time,
              end_time: hourPref.end_time,
              weight: hourPref.weight,
            });
          }
        }
      } catch (err) {
        console.error("Failed to persist hour range preference:", err);
        throw new Error(`Failed to save hour range preference: ${err instanceof Error ? err.message : "Unknown error"}`);
      }
    }

    // Delete removed hour range preferences
    for (const original of originalHourRangePreferences) {
      if (!hourRangePreferences.find((p) => p.id === original.id)) {
        try {
          await schedulingPreferencesApi.deleteHourRangePreference(original.id);
        } catch (err) {
          console.error("Failed to delete hour range preference:", err);
        }
      }
    }

    // Persist hour range caps
    for (const cap of hourRangeCaps) {
      const isNew = cap.id.startsWith("temp-");
      try {
        if (isNew) {
          await schedulingPreferencesApi.createHourRangeCap({
            employee_id: empId,
            start_time: cap.start_time,
            end_time: cap.end_time,
            max_per_week: cap.max_per_week,
            weight: cap.weight,
          });
        } else {
          const originalCap = originalHourRangeCaps.find((c) => c.id === cap.id);
          if (originalCap && (originalCap.weight !== cap.weight || originalCap.start_time !== cap.start_time || originalCap.end_time !== cap.end_time || originalCap.max_per_week !== cap.max_per_week)) {
            await schedulingPreferencesApi.updateHourRangeCap(cap.id, {
              start_time: cap.start_time,
              end_time: cap.end_time,
              max_per_week: cap.max_per_week,
              weight: cap.weight,
            });
          }
        }
      } catch (err) {
        console.error("Failed to persist hour range cap:", err);
        throw new Error(`Failed to save hour range cap: ${err instanceof Error ? err.message : "Unknown error"}`);
      }
    }

    // Delete removed hour range caps
    for (const original of originalHourRangeCaps) {
      if (!hourRangeCaps.find((c) => c.id === original.id)) {
        try {
          await schedulingPreferencesApi.deleteHourRangeCap(original.id);
        } catch (err) {
          console.error("Failed to delete hour range cap:", err);
        }
      }
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!formData.full_name.trim()) {
      setError("Employee name is required");
      return;
    }

    try {
      // Save employee info first
      await onSubmit(formData);

      // Then persist preferences if employeeId exists
      if (employeeId) {
        await persistPreferences(employeeId);
      }
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

  const addAffinity = () => {
    if (!newAffinity.targetEmployeeId || !employeeId) return;
    const existing = affinities.find((a) => a.target_employee_id === newAffinity.targetEmployeeId);
    if (existing) {
      setError("This affinity already exists for this employee");
      return;
    }

    setAffinities([
      ...affinities,
      {
        id: `temp-${Date.now()}`,
        employee_id: employeeId,
        target_employee_id: newAffinity.targetEmployeeId,
        level: newAffinity.level,
        entry_date: new Date().toISOString().split("T")[0],
        expiration_date: null,
      },
    ]);
    setError("");
    setNewAffinity({ targetEmployeeId: "", level: 0 });
  };

  const removeAffinity = (targetEmployeeId: string) => {
    setAffinities(affinities.filter((a) => a.target_employee_id !== targetEmployeeId));
  };

  const updateAffinityExpiration = (targetEmployeeId: string, expirationDate: string | null) => {
    setAffinities(
      affinities.map((a) =>
        a.target_employee_id === targetEmployeeId
          ? { ...a, expiration_date: expirationDate }
          : a
      )
    );
  };

  const toggleDayPreference = (dayOfWeek: number, weight: number = 1) => {
    setDayPreferences((prev) => {
      const existing = prev.find((p) => p.day_of_week === dayOfWeek);
      if (existing) {
        return prev.filter((p) => p.day_of_week !== dayOfWeek);
      } else {
        return [
          ...prev,
          {
            id: `temp-${dayOfWeek}`,
            company_id: "",
            employee_id: employeeId || "",
            day_of_week: dayOfWeek,
            weight,
          },
        ];
      }
    });
  };

  const updateDayWeight = (dayOfWeek: number, weight: number) => {
    setDayPreferences(
      dayPreferences.map((p) =>
        p.day_of_week === dayOfWeek ? { ...p, weight } : p
      )
    );
  };

  const addHourRangePreference = () => {
    if (!employeeId) return;
    setHourRangePreferences([
      ...hourRangePreferences,
      {
        id: `temp-${Date.now()}`,
        company_id: "",
        employee_id: employeeId,
        start_time: newHourRangePreference.start_time,
        end_time: newHourRangePreference.end_time,
        weight: newHourRangePreference.weight,
      },
    ]);
    setNewHourRangePreference({ start_time: "09:00", end_time: "17:00", weight: 1 });
  };

  const removeHourRangePreference = (id: string) => {
    setHourRangePreferences(hourRangePreferences.filter((p) => p.id !== id));
  };

  const addHourRangeCap = () => {
    if (!employeeId) return;
    setHourRangeCaps([
      ...hourRangeCaps,
      {
        id: `temp-${Date.now()}`,
        company_id: "",
        employee_id: employeeId,
        start_time: newHourRangeCap.start_time,
        end_time: newHourRangeCap.end_time,
        max_per_week: newHourRangeCap.max_per_week,
        weight: newHourRangeCap.weight,
      },
    ]);
    setNewHourRangeCap({ start_time: "09:00", end_time: "17:00", max_per_week: 5, weight: 1 });
  };

  const removeHourRangeCap = (id: string) => {
    setHourRangeCaps(hourRangeCaps.filter((c) => c.id !== id));
  };

  return (
    <form onSubmit={handleSubmit}>
      {error && <div className="glass-alert-error mb-6">{error}</div>}

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
              onChange={(e) =>
                updateField("seniority_rank", e.target.value ? parseInt(e.target.value) : null)
              }
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
              onChange={(e) =>
                updateField("pay_rate", e.target.value ? parseFloat(e.target.value) : null)
              }
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
            onChange={(e) =>
              updateField("max_hours_per_week", e.target.value ? parseFloat(e.target.value) : null)
            }
            className="glass-input w-full mt-2"
          />
          <p className={`text-xs ${text.muted} mt-1`}>Leave blank for no limit</p>
        </div>
      </div>

      {/* Preferences Sections (only for existing employees) */}
      {employeeId && (
        <>
          {/* Employee Association Section */}
          <div className="mb-8">
            <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Employee Association</h3>
            <p className={`text-sm ${text.muted} mb-4`}>Set relationships with other team members</p>

            {/* Existing Affinities */}
            {affinities.length > 0 && (
              <div className="space-y-3 mb-6">
                {affinities.map((aff) => {
                  const targetEmployee = employees.find((e) => e.id === aff.target_employee_id);
                  return (
                    <div key={aff.target_employee_id} className="border border-sage/20 rounded-lg p-4">
                      <div className="flex items-center justify-between mb-3">
                        <span className={`text-sm font-medium ${text.body}`}>
                          {targetEmployee?.full_name}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeAffinity(aff.target_employee_id)}
                          className="text-xs text-red-600 hover:text-red-700"
                        >
                          Remove
                        </button>
                      </div>
                      <div className="space-y-3">
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>
                            Level: {aff.level}
                          </label>
                          <input
                            type="range"
                            min="-1"
                            max="1"
                            step="0.5"
                            value={aff.level}
                            onChange={(e) => {
                              const newAffs = affinities.map((a) =>
                                a.target_employee_id === aff.target_employee_id
                                  ? { ...a, level: parseFloat(e.target.value) }
                                  : a
                              );
                              setAffinities(newAffs);
                            }}
                            className="w-full"
                          />
                          <div className={`text-xs ${text.muted} text-center mt-1`}>
                            -1 (conflict) to +1 (mentor)
                          </div>
                        </div>
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>
                            Expiration Date
                          </label>
                          <input
                            type="date"
                            value={aff.expiration_date ?? ""}
                            onChange={(e) =>
                              updateAffinityExpiration(aff.target_employee_id, e.target.value || null)
                            }
                            className="glass-input w-full"
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Add New Affinity */}
            <div className="border border-sage/20 rounded-lg p-4 bg-sage/5">
              <h4 className={`text-sm font-medium ${text.body} mb-3`}>Add Affinity</h4>
              <div className="space-y-3">
                <div>
                  <label className={`text-xs font-medium ${text.body} block mb-1`}>Employee</label>
                  <select
                    value={newAffinity.targetEmployeeId}
                    onChange={(e) =>
                      setNewAffinity({ ...newAffinity, targetEmployeeId: e.target.value })
                    }
                    className="glass-input w-full"
                  >
                    <option value="">Select an employee</option>
                    {employees.map((emp) => {
                      if (emp.id === employeeId) return null;
                      if (affinities.some((a) => a.target_employee_id === emp.id)) return null;
                      return (
                        <option key={emp.id} value={emp.id}>
                          {emp.full_name}
                        </option>
                      );
                    })}
                  </select>
                </div>
                <div>
                  <label className={`text-xs font-medium ${text.body} block mb-1`}>
                    Level: {newAffinity.level}
                  </label>
                  <input
                    type="range"
                    min="-1"
                    max="1"
                    step="0.5"
                    value={newAffinity.level}
                    onChange={(e) =>
                      setNewAffinity({ ...newAffinity, level: parseFloat(e.target.value) })
                    }
                    className="w-full"
                  />
                  <div className={`text-xs ${text.muted} text-center mt-1`}>
                    -1 (conflict) to +1 (mentor)
                  </div>
                </div>
                <button
                  type="button"
                  onClick={addAffinity}
                  disabled={!newAffinity.targetEmployeeId}
                  className="glass-btn-secondary w-full disabled:opacity-50"
                >
                  Add Affinity
                </button>
              </div>
            </div>
          </div>

          {/* Day Preferences Section */}
          <div className="mb-8">
            <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Day Preferences</h3>
            <p className={`text-sm ${text.muted} mb-4`}>Set preferred days and weights</p>
            <div className="space-y-3">
              {dayNames.map((day, idx) => {
                const pref = dayPreferences.find((p) => p.day_of_week === idx);
                return (
                  <div key={idx} className="border border-sage/20 rounded-lg p-4">
                    <div className="flex items-center justify-between">
                      <label className="flex items-center space-x-3 flex-1">
                        <input
                          type="checkbox"
                          checked={!!pref}
                          onChange={() => toggleDayPreference(idx)}
                          className="rounded"
                        />
                        <span className={`text-sm font-medium ${text.body}`}>{day}</span>
                      </label>
                    </div>
                    {pref && (
                      <div className="mt-3 ms-7">
                        <label className={`text-xs font-medium ${text.body} block mb-1`}>
                          Weight: {pref.weight}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="2"
                          step="0.5"
                          value={pref.weight}
                          onChange={(e) => updateDayWeight(idx, parseFloat(e.target.value))}
                          className="w-full"
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Hour Range Preferences Section */}
          <div className="mb-8">
            <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Hour Range Preferences</h3>
            <p className={`text-sm ${text.muted} mb-4`}>Preferred time windows for shifts</p>

            {/* Existing Preferences */}
            {hourRangePreferences.length > 0 && (
              <div className="space-y-3 mb-6">
                {hourRangePreferences.map((pref) => (
                  <div key={pref.id} className="border border-sage/20 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                      <span className={`text-sm font-medium ${text.body}`}>
                        {pref.start_time} - {pref.end_time}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeHourRangePreference(pref.id)}
                        className="text-xs text-red-600 hover:text-red-700"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="space-y-3">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>Start Time</label>
                          <input
                            type="time"
                            value={pref.start_time}
                            onChange={(e) => {
                              const updated = hourRangePreferences.map((p) =>
                                p.id === pref.id ? { ...p, start_time: e.target.value } : p
                              );
                              setHourRangePreferences(updated);
                            }}
                            className="glass-input w-full"
                          />
                        </div>
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>End Time</label>
                          <input
                            type="time"
                            value={pref.end_time}
                            onChange={(e) => {
                              const updated = hourRangePreferences.map((p) =>
                                p.id === pref.id ? { ...p, end_time: e.target.value } : p
                              );
                              setHourRangePreferences(updated);
                            }}
                            className="glass-input w-full"
                          />
                        </div>
                      </div>
                      <div>
                        <label className={`text-xs font-medium ${text.body} block mb-1`}>
                          Weight: {pref.weight}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="2"
                          step="0.5"
                          value={pref.weight}
                          onChange={(e) => {
                            const updated = hourRangePreferences.map((p) =>
                              p.id === pref.id ? { ...p, weight: parseFloat(e.target.value) } : p
                            );
                            setHourRangePreferences(updated);
                          }}
                          className="w-full"
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Add New Preference */}
            <div className="border border-sage/20 rounded-lg p-4 bg-sage/5">
              <h4 className={`text-sm font-medium ${text.body} mb-3`}>Add Preference</h4>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={`text-xs font-medium ${text.body} block mb-1`}>Start Time</label>
                    <input
                      type="time"
                      value={newHourRangePreference.start_time}
                      onChange={(e) =>
                        setNewHourRangePreference({
                          ...newHourRangePreference,
                          start_time: e.target.value,
                        })
                      }
                      className="glass-input w-full"
                    />
                  </div>
                  <div>
                    <label className={`text-xs font-medium ${text.body} block mb-1`}>End Time</label>
                    <input
                      type="time"
                      value={newHourRangePreference.end_time}
                      onChange={(e) =>
                        setNewHourRangePreference({
                          ...newHourRangePreference,
                          end_time: e.target.value,
                        })
                      }
                      className="glass-input w-full"
                    />
                  </div>
                </div>
                <div>
                  <label className={`text-xs font-medium ${text.body} block mb-1`}>
                    Weight: {newHourRangePreference.weight}
                  </label>
                  <input
                    type="range"
                    min="0"
                    max="2"
                    step="0.5"
                    value={newHourRangePreference.weight}
                    onChange={(e) =>
                      setNewHourRangePreference({
                        ...newHourRangePreference,
                        weight: parseFloat(e.target.value),
                      })
                    }
                    className="w-full"
                  />
                </div>
                <button
                  type="button"
                  onClick={addHourRangePreference}
                  className="glass-btn-secondary w-full"
                >
                  Add Preference
                </button>
              </div>
            </div>
          </div>

          {/* Frequency Caps Section */}
          <div className="mb-8">
            <h3 className={`text-lg font-semibold ${text.heading} mb-4`}>Frequency Caps</h3>
            <p className={`text-sm ${text.muted} mb-4`}>Maximum shifts per time window</p>

            {/* Existing Caps */}
            {hourRangeCaps.length > 0 && (
              <div className="space-y-3 mb-6">
                {hourRangeCaps.map((cap) => (
                  <div key={cap.id} className="border border-sage/20 rounded-lg p-4">
                    <div className="flex items-center justify-between mb-3">
                      <span className={`text-sm font-medium ${text.body}`}>
                        {cap.start_time} - {cap.end_time}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeHourRangeCap(cap.id)}
                        className="text-xs text-red-600 hover:text-red-700"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="space-y-3">
                      <div className="grid grid-cols-2 gap-2">
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>Start Time</label>
                          <input
                            type="time"
                            value={cap.start_time}
                            onChange={(e) => {
                              const updated = hourRangeCaps.map((c) =>
                                c.id === cap.id ? { ...c, start_time: e.target.value } : c
                              );
                              setHourRangeCaps(updated);
                            }}
                            className="glass-input w-full"
                          />
                        </div>
                        <div>
                          <label className={`text-xs font-medium ${text.body} block mb-1`}>End Time</label>
                          <input
                            type="time"
                            value={cap.end_time}
                            onChange={(e) => {
                              const updated = hourRangeCaps.map((c) =>
                                c.id === cap.id ? { ...c, end_time: e.target.value } : c
                              );
                              setHourRangeCaps(updated);
                            }}
                            className="glass-input w-full"
                          />
                        </div>
                      </div>
                      <div>
                        <label className={`text-xs font-medium ${text.body} block mb-1`}>Max Per Week</label>
                        <input
                          type="number"
                          min="1"
                          value={cap.max_per_week}
                          onChange={(e) => {
                            const updated = hourRangeCaps.map((c) =>
                              c.id === cap.id ? { ...c, max_per_week: parseInt(e.target.value) || 5 } : c
                            );
                            setHourRangeCaps(updated);
                          }}
                          className="glass-input w-full"
                        />
                      </div>
                      <div>
                        <label className={`text-xs font-medium ${text.body} block mb-1`}>
                          Weight: {cap.weight}
                        </label>
                        <input
                          type="range"
                          min="0"
                          max="2"
                          step="0.5"
                          value={cap.weight}
                          onChange={(e) => {
                            const updated = hourRangeCaps.map((c) =>
                              c.id === cap.id ? { ...c, weight: parseFloat(e.target.value) } : c
                            );
                            setHourRangeCaps(updated);
                          }}
                          className="w-full"
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Add New Cap */}
            <div className="border border-sage/20 rounded-lg p-4 bg-sage/5">
              <h4 className={`text-sm font-medium ${text.body} mb-3`}>Add Cap</h4>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className={`text-xs font-medium ${text.body} block mb-1`}>Start Time</label>
                    <input
                      type="time"
                      value={newHourRangeCap.start_time}
                      onChange={(e) =>
                        setNewHourRangeCap({ ...newHourRangeCap, start_time: e.target.value })
                      }
                      className="glass-input w-full"
                    />
                  </div>
                  <div>
                    <label className={`text-xs font-medium ${text.body} block mb-1`}>End Time</label>
                    <input
                      type="time"
                      value={newHourRangeCap.end_time}
                      onChange={(e) =>
                        setNewHourRangeCap({ ...newHourRangeCap, end_time: e.target.value })
                      }
                      className="glass-input w-full"
                    />
                  </div>
                </div>
                <div>
                  <label className={`text-xs font-medium ${text.body} block mb-1`}>
                    Max Per Week
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={newHourRangeCap.max_per_week}
                    onChange={(e) =>
                      setNewHourRangeCap({
                        ...newHourRangeCap,
                        max_per_week: parseInt(e.target.value) || 5,
                      })
                    }
                    className="glass-input w-full"
                  />
                </div>
                <button
                  type="button"
                  onClick={addHourRangeCap}
                  className="glass-btn-secondary w-full"
                >
                  Add Cap
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Submit Buttons */}
      <div className="flex gap-3 pt-6 border-t border-sage/20">
        <button
          type="submit"
          disabled={isLoading || preferencesLoading}
          className="glass-btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {isLoading ? "Saving..." : isNewEmployee ? "Create Employee" : "Update Employee"}
        </button>
      </div>
    </form>
  );
}
