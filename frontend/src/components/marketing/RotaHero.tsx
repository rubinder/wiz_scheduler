import { Link } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import { BANDS, CELLS, DAYS, TOTALS } from "./rotaData";

export default function RotaHero() {
  const { t } = useLanguage();

  const cellAt = (day: number, band: number) =>
    CELLS.find((c) => c.day === day && c.band === band);

  return (
    <section className="max-w-[92rem] mx-auto px-6 pt-16 pb-20 grid gap-12 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)] lg:gap-16 lg:items-center">
      {/* ── Copy ── */}
      <div>
        <p className={`${m.text.meta} mb-6`}>{t.landing.badge}</p>
        <h1
          className={`${m.text.display} font-display text-5xl sm:text-6xl lg:text-7xl font-semibold leading-[0.95] mb-6`}
        >
          {t.landing.heroTitle}{" "}
          {/* The highlighter marks by filling, not by tinting. */}
          <span className={m.mark}>{t.landing.heroTitleAccent}</span>
        </h1>
        <p className={`${m.text.muted} text-lg leading-relaxed mb-9 max-w-[46ch]`}>
          {t.landing.heroDesc}
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Link to="/register" className={m.btn.primary}>
            {t.landing.getStarted}
          </Link>
          <a href="#pricing" className={m.btn.link}>
            {t.landing.viewPricing}
          </a>
        </div>
      </div>

      {/* ── The rota ── */}
      <div className={`${m.surface} border ${m.rule.heavy}`}>
        <div
          className="grid"
          style={{ gridTemplateColumns: `4.5rem repeat(${DAYS.length}, minmax(0, 1fr))` }}
        >
          {/* header row */}
          <div className={`border-b ${m.rule.grid}`} />
          {DAYS.map((d, i) => (
            <div
              key={d}
              data-rota-head={i}
              className={`${m.text.meta} border-b border-s ${m.rule.grid} px-2 py-2.5 text-center`}
            >
              {d}
            </div>
          ))}

          {/* body */}
          {BANDS.map((band, b) => (
            <div key={band} className="contents">
              <div
                className={`${m.text.meta} border-b ${m.rule.grid} px-2 py-3 flex items-center`}
              >
                {band}
              </div>
              {DAYS.map((_, d) => {
                const cell = cellAt(d, b);
                return (
                  <div
                    key={`${band}-${d}`}
                    data-cell={cell ? `${d}-${b}` : undefined}
                    data-retried={cell?.retried ? "true" : undefined}
                    className={`border-b border-s ${m.rule.grid} px-2 py-3 min-h-[4.25rem] transition-colors ${
                      cell ? "hover:bg-marker/10" : ""
                    }`}
                  >
                    {cell && (
                      <>
                        <div className={`${m.text.body} text-sm font-medium leading-tight`}>
                          {cell.role}
                        </div>
                        <div className={`${m.text.data} text-xs text-ink/60 mt-1`}>
                          {cell.hours}
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>

        {/* footline */}
        <div className="px-4 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-1">
          <span className={`${m.text.data} text-sm`}>
            <span data-total="shifts">{TOTALS.shifts}</span>{" "}
            <span className="text-ink/60">shifts</span>
          </span>
          <span className={`${m.text.data} text-sm`}>
            <span data-total="people">{TOTALS.people}</span>{" "}
            <span className="text-ink/60">people</span>
          </span>
          <span className={`${m.text.data} text-sm ${m.text.clear}`}>
            <span data-total="violations">{TOTALS.violations}</span> rest violations
          </span>
        </div>
      </div>
    </section>
  );
}
