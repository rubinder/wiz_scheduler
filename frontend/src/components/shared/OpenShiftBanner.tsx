import { useLanguage } from "../../i18n/LanguageContext";

interface Props {
  count: number;
  onShow: () => void;
}

/** "N open shifts this week" (#174). Clicking highlights them in the grid. */
export default function OpenShiftBanner({ count, onShow }: Props) {
  const { t } = useLanguage();
  if (count <= 0) return null;
  const label =
    count === 1
      ? t.schedule.openShiftsBannerOne
      : t.schedule.openShiftsBanner.replace("{n}", String(count));
  return (
    <button
      type="button"
      onClick={onShow}
      className="glass-alert-warning w-full text-start mb-3 text-sm font-medium"
    >
      {label}
    </button>
  );
}
