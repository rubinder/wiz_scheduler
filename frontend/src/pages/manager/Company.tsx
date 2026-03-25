import React, { useCallback, useEffect, useState } from "react";
import * as companyApi from "../../api/company";
import type { Company as CompanyType } from "../../types";

export default function Company() {
  const [company, setCompany] = useState<CompanyType | null>(null);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const fetchCompany = useCallback(async () => {
    try {
      const data = await companyApi.getCompany();
      setCompany(data);
      setName(data.name);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load company");
    }
  }, []);

  useEffect(() => {
    fetchCompany();
  }, [fetchCompany]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      const updated = await companyApi.updateCompany({ name });
      setCompany(updated);
      setSuccess("Company name updated successfully.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update company");
    } finally {
      setSaving(false);
    }
  };

  if (!company && !error) {
    return <div className="text-gray-500">Loading...</div>;
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Company Settings</h1>
      {error && (
        <div className="mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 p-3 bg-green-50 text-green-700 rounded text-sm">
          {success}
        </div>
      )}
      <div className="bg-white rounded-lg shadow p-6 max-w-lg">
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Company Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2"
            />
          </div>
          {company && (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Slug
              </label>
              <input
                type="text"
                value={company.slug}
                disabled
                className="w-full border border-gray-200 rounded px-3 py-2 bg-gray-50 text-gray-500"
              />
            </div>
          )}
          <button
            type="submit"
            disabled={saving}
            className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 font-medium text-sm"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </form>
      </div>
    </div>
  );
}
