import { Link } from "react-router-dom";
import LanguageSelector from "../shared/LanguageSelector";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import { useMarketingGround } from "../../hooks/useMarketingGround";
import { BANDS, CELLS, DAYS } from "./rotaData";

interface Props {
  /** Page title. Must come from i18n. */
  title: string;
  children: React.ReactNode;
  /** Sign-in / register cross-links. */
  footer?: React.ReactNode;
}

/**
 * Split shell for all six auth pages: form on `paper`, a quiet static
 * rota field beside it. Replaces the centred max-w-md glass card.
 * The panel is decorative and hidden from assistive tech.
 */
export default function AuthLayout({ title, children, footer }: Props) {
  const { t } = useLanguage();
  useMarketingGround();

  // Rendered once for real in the header below, and once invisibly in the
  // rota side so both columns share the same header height — that keeps the
  // rota panel's centered content vertically aligned with the form's.
  const brandMark = (
    <span className="flex items-center gap-2.5">
      <img src="/favicon.svg" alt="" className="w-6 h-6" />
      <span
        className={`${m.text.display} font-display text-base font-semibold uppercase tracking-[0.06em]`}
      >
        {t.common.appName}
      </span>
    </span>
  );

  return (
    <div className={`min-h-screen ${m.page} font-body lg:grid lg:grid-cols-2`}>
      {/* form side */}
      <div className="flex flex-col min-h-screen px-6 py-8 lg:px-14">
        <div className="flex items-center justify-between mb-14">
          <Link to="/" className="flex items-center gap-2.5">
            {brandMark}
          </Link>
          <LanguageSelector variant="rota" />
        </div>

        <div className="flex-1 flex flex-col justify-center max-w-[26rem] w-full">
          <h1
            className={`${m.text.display} font-display text-4xl font-semibold leading-tight mb-8`}
          >
            {title}
          </h1>
          {children}
          {footer && <div className="mt-8">{footer}</div>}
        </div>
      </div>

      {/* rota side — decorative */}
      <div
        aria-hidden="true"
        className={`hidden lg:flex lg:flex-col min-h-screen border-s ${m.rule.heavy} ${m.surface} px-14 py-8`}
      >
        <div className="invisible mb-14">{brandMark}</div>
        <div className="flex-1 flex items-center justify-center">
          <div
            className={`w-full max-w-lg border ${m.rule.heavy} grid`}
            style={{ gridTemplateColumns: `3.5rem repeat(${DAYS.length}, minmax(0, 1fr))` }}
          >
            <div className={`border-b ${m.rule.grid}`} />
            {DAYS.map((d) => (
              <div
                key={d}
                className={`${m.text.meta} !text-[0.6rem] border-b border-s ${m.rule.grid} px-1 py-1.5 text-center`}
              >
                {d}
              </div>
            ))}
            {BANDS.map((band, b) => (
              <div key={band} className="contents">
                <div
                  className={`${m.text.meta} !text-[0.6rem] border-b ${m.rule.grid} px-1 py-2 flex items-center`}
                >
                  {band}
                </div>
                {DAYS.map((_, d) => {
                  const filled = CELLS.some((c) => c.day === d && c.band === b);
                  return (
                    <div
                      key={`${band}-${d}`}
                      className={`border-b border-s ${m.rule.grid} h-11 ${
                        filled ? "bg-ink/[0.16]" : ""
                      }`}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
