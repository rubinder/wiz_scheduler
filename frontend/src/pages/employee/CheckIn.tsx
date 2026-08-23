import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { submitCheckIn } from "../../api/checkIns";
import { ApiError } from "../../api/client";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";
import type { CheckInResult } from "../../types";

/** Where the QR deep link lands. The token is in the query string; identity
 *  comes from the bearer token this app already holds, so the code itself
 *  never has to know who is scanning. */
export default function CheckIn() {
  const { t } = useLanguage();
  const [params] = useSearchParams();
  const [result, setResult] = useState<CheckInResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  /** A scan is not idempotent — StrictMode's double-effect would burn two
   *  codes and show the second as a duplicate. */
  const submitted = useRef(false);

  const token = params.get("t") ?? "";
  const locationId = params.get("l") ?? "";
  const hasDeepLink = token !== "" && locationId !== "";

  useEffect(() => {
    if (!hasDeepLink) {
      setBusy(false);
      return;
    }
    if (submitted.current) return;
    submitted.current = true;

    submitCheckIn(token, locationId)
      .then(setResult)
      .catch((e: unknown) => {
        // apiFetch throws ApiError, which carries the server's `detail` on
        // `.data` — NOT on the error itself. The router returns either
        // detail: {code, message} (a CheckInRejected) or a plain string
        // (e.g. the 403 raised when no employee record is linked to the
        // account), so the code is only readable off an object detail.
        const code =
          e instanceof ApiError &&
          typeof e.data === "object" &&
          e.data !== null &&
          "code" in e.data
            ? String((e.data as { code: unknown }).code)
            : "";
        const status = e instanceof ApiError ? e.status : 0;
        setError(
          code === "code_already_used" || code === "invalid_token"
            ? t.checkIn.codeExpired
            : status === 403
              ? t.checkIn.noEmployeeRecord
              : t.checkIn.failed
        );
      })
      .finally(() => setBusy(false));
  }, [hasDeepLink, token, locationId, t]);

  if (!hasDeepLink) {
    return <p className="p-6">{t.checkIn.scanPrompt}</p>;
  }
  if (busy) return <p className="p-6">{t.checkIn.checkingIn}</p>;
  if (error) return <div className="glass-alert-error m-6">{error}</div>;
  if (!result) return null;

  const minutes = result.minutes_from_start ?? 0;
  let message: string;
  if (result.status === "duplicate") message = t.checkIn.successDuplicate;
  else if (result.status === "no_shift") message = t.checkIn.successNoShift;
  else if (result.status === "wrong_location")
    message = t.checkIn.successWrongLocation;
  else if (minutes === 0) message = t.checkIn.successOnTime;
  else
    message = t.checkIn.successMatched
      .replace("{minutes}", String(Math.abs(minutes)))
      .replace(
        "{direction}",
        minutes < 0 ? t.checkIn.directionEarly : t.checkIn.directionLate
      );

  return <p className={`p-6 text-lg ${text.body}`}>{message}</p>;
}
