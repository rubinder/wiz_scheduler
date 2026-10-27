import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

/**
 * Stands in for edit controls on a row a regular manager may see but not
 * change: the employee also works at a location they don't manage (#206).
 */
export default function SharedReadOnlyNote() {
  const { t } = useLanguage();
  return (
    <span className={`text-xs italic ${text.muted}`} title={t.access.sharedReadOnlyHint}>
      {t.access.sharedReadOnly}
    </span>
  );
}
