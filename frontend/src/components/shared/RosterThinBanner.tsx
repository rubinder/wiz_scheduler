import { useLanguage } from "../../i18n/LanguageContext";
import type { PreferenceSummary } from "../../types";
import { rosterThinMessage } from "../../utils/preferenceText";

/** "You may need more staff" (#99). Renders nothing unless the summary
 *  says the roster left no clean alternative for at least one shift. */
export default function RosterThinBanner({
  summary,
  atGeneration = false,
}: {
  summary: PreferenceSummary | null | undefined;
  atGeneration?: boolean;
}) {
  const { t } = useLanguage();
  if (!summary || !summary.roster_thin) return null;
  return (
    <div
      role="status"
      className="mx-4 my-3 rounded-lg border border-amber-300 bg-amber-50 text-amber-900 text-sm px-3 py-2"
    >
      {rosterThinMessage(summary, t.schedule.rosterThinBanner)}
      {atGeneration && <span className="ms-1 opacity-75">{t.schedule.rosterThinAtGeneration}</span>}
    </div>
  );
}
