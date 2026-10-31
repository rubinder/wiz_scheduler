import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { redeemPayrollArchiveDownload } from "../api/billing";
import { ApiError } from "../api/client";
import AuthLayout from "../components/marketing/AuthLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

/**
 * Where the mailed payroll-archive link lands (#218). Opening the page
 * redeems nothing: corporate mail scanners open every link in an email, and
 * a single-use token redeemed on GET would be used up before its recipient
 * clicks. Only the button POSTs the token, then navigates to the short-lived
 * download URL the server returns.
 */
export default function PayrollArchiveDownload() {
  useDocumentTitle("Download Payroll Archive");
  const { t } = useLanguage();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token") ?? "";
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  const download = async () => {
    setError("");
    setWorking(true);
    try {
      const { url } = await redeemPayrollArchiveDownload(token);
      window.location.href = url;
    } catch (err: unknown) {
      setWorking(false);
      setError(
        err instanceof ApiError && err.status === 410
          ? t.payrollArchive.downloadInvalid
          : t.payrollArchive.downloadUnavailable
      );
    }
  };

  return (
    <AuthLayout title={t.payrollArchive.downloadTitle}>
      <p className={`text-sm ${m.text.muted} mb-6`}>{t.payrollArchive.downloadDescription}</p>
      {error && <div className={`${m.alert.error} mb-4`}>{error}</div>}
      <button
        type="button"
        disabled={working || !token}
        onClick={() => void download()}
        className={`${m.btn.primary} w-full`}
      >
        {working ? t.payrollArchive.downloading : t.payrollArchive.downloadButton}
      </button>
      <p className={`text-sm ${m.text.muted} mt-6 text-center`}>
        <Link to="/payroll-archive" className={m.btn.link}>
          {t.payrollArchive.requestNewLink}
        </Link>
      </p>
    </AuthLayout>
  );
}
