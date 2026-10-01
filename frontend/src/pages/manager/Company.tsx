import React, { useCallback, useEffect, useState } from "react";
import * as companyApi from "../../api/company";
import type { Company as CompanyType } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { usePlan } from "../../hooks/usePlan";
import DemoGuard from "../../components/shared/DemoGuard";
import { text, border, bg } from "../../theme";

export default function Company() {
  const { t } = useLanguage();
  const { plan } = usePlan();
  const [company, setCompany] = useState<CompanyType | null>(null);
  const [name, setName] = useState("");
  const [overtimeThresholdHours, setOvertimeThresholdHours] = useState("");
  const [overtimePremiumMultiplier, setOvertimePremiumMultiplier] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const fetchCompany = useCallback(async () => {
    try {
      const data = await companyApi.getCompany();
      setCompany(data);
      setName(data.name);
      setOvertimeThresholdHours(
        data.overtime_threshold_hours != null ? String(data.overtime_threshold_hours) : ""
      );
      setOvertimePremiumMultiplier(
        data.overtime_premium_multiplier != null ? String(data.overtime_premium_multiplier) : ""
      );
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
      const updated = await companyApi.updateCompany({
        name,
        overtime_threshold_hours:
          overtimeThresholdHours === "" ? null : Number(overtimeThresholdHours),
        overtime_premium_multiplier:
          overtimePremiumMultiplier === "" ? null : Number(overtimePremiumMultiplier),
      });
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

  const overtimeGated = plan?.plan === "free";

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
          <div>
            <label className="glass-label">
              {t.companyPage.overtimeThreshold}
            </label>
            <input
              type="number"
              step="0.5"
              min="0"
              value={overtimeThresholdHours}
              disabled={overtimeGated}
              title={overtimeGated ? t.companyPage.overtimeGatedHint : undefined}
              onChange={(e) => setOvertimeThresholdHours(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          <div>
            <label className="glass-label">
              {t.companyPage.overtimeMultiplier}
            </label>
            <input
              type="number"
              step="0.1"
              min="1"
              value={overtimePremiumMultiplier}
              disabled={overtimeGated}
              title={overtimeGated ? t.companyPage.overtimeGatedHint : undefined}
              onChange={(e) => setOvertimePremiumMultiplier(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          {overtimeGated && (
            <p className={`text-xs ${text.muted}`}>{t.companyPage.overtimeGatedHint}</p>
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
