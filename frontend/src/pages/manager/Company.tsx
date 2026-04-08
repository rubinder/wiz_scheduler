import React, { useCallback, useEffect, useState } from "react";
import * as companyApi from "../../api/company";
import type { Company as CompanyType } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import DemoGuard from "../../components/shared/DemoGuard";
import { text, border, bg } from "../../theme";

export default function Company() {
  const { t } = useLanguage();
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
      setSuccess(t.companyPage.updateSuccess);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update company");
    } finally {
      setSaving(false);
    }
  };

  if (!company && !error) {
    return <div className={text.muted}>{t.common.loading}</div>;
  }

  return (
    <div>
      <h1 className={`text-2xl font-bold ${text.heading} mb-6`}>{t.companyPage.title}</h1>
      {error && (
        <div className="glass-alert-error mb-4">
          {error}
        </div>
      )}
      {success && (
        <div className="glass-alert-success mb-4">
          {success}
        </div>
      )}
      <div className="glass-card p-6 max-w-lg">
        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="glass-label">
              {t.companyPage.companyName}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          {company && (
            <div>
              <label className="glass-label">
                {t.companyPage.slug}
              </label>
              <input
                type="text"
                value={company.slug}
                disabled
                className={`w-full rounded px-3 py-2 ${bg.sectionSubtle} ${text.muted} border ${border.subtle}`}
              />
            </div>
          )}
          <DemoGuard>
            <button
              type="submit"
              disabled={saving}
              className="glass-btn-primary"
            >
              {saving ? t.common.saving : t.common.save}
            </button>
          </DemoGuard>
        </form>
      </div>
    </div>
  );
}
