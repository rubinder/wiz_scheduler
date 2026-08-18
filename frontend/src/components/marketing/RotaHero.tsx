import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Link } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import { BANDS, CELLS, DAYS, TOTALS } from "./rotaData";

export default function RotaHero() {
  const { t } = useLanguage();
  const reduce = useReducedMotion();

  const cellAt = (day: number, band: number) =>
    CELLS.find((c) => c.day === day && c.band === band);

  // Diagonal wave: cells further from the top-start corner land later.
  const cellDelay = (day: number, band: number) => 0.4 + (day + band) * 0.024;

  const cellAnim = (c: { day: number; band: number; retried: boolean }) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, scale: 0.94 },
          animate: {
            opacity: 1,
            scale: 1,
            // Retried cells flash `marker` mid-sequence, then resolve —
            // mirroring the conflict-retry the real pipeline performs.
            backgroundColor: c.retried
              ? ["rgba(255,90,31,0)", "rgba(255,90,31,0.28)", "rgba(255,90,31,0)"]
              : undefined,
          },
          transition: {
            duration: 0.28,
            delay: cellDelay(c.day, c.band),
            backgroundColor: { duration: 0.5, delay: 0.9, times: [0, 0.4, 1] },
          },
        };

  const Count = ({ to }: { to: number }) => {
    const [n, setN] = useState(reduce ? to : 0);
    useEffect(() => {
      if (reduce || to === 0) { setN(to); return; }
      let raf = 0;
      const start = performance.now();
      const tick = (now: number) => {
        const p = Math.min(1, (now - start - 1050) / 350);
        if (p >= 0) setN(Math.round(to * p));
        if (p < 1) raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(raf);
    }, [to]);
    return <>{n}</>;
  };

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
          <a href="#demo" className={m.btn.link}>
            {t.landing.viewDemo}
          </a>
        </div>
      </div>

      {/* ── The rota ── */}
      <div className={`${m.surface} border ${m.rule.heavy}`}>
        <div
          className="grid [--label-w:2.25rem] sm:[--label-w:4.5rem]"
          style={{ gridTemplateColumns: `var(--label-w) repeat(${DAYS.length}, minmax(0, 1fr))` }}
        >
          {/* header row */}
          <div className={`border-b ${m.rule.grid}`} />
          {DAYS.map((d, i) => (
            <motion.div
              key={d}
              data-rota-head={i}
              initial={reduce ? undefined : { opacity: 0 }}
              animate={reduce ? undefined : { opacity: 1 }}
              transition={reduce ? undefined : { delay: 0.2 + i * 0.03 }}
              className={`${m.text.meta} border-b border-s ${m.rule.grid} px-1 py-2.5 sm:px-2 text-center`}
            >
              <span className="sm:hidden">{d.charAt(0)}</span>
              <span className="hidden sm:inline">{d}</span>
            </motion.div>
          ))}

          {/* body */}
          {BANDS.map((band, b) => (
            <div key={band} className="contents">
              <div
                className={`${m.text.meta} border-b ${m.rule.grid} px-1 py-3 sm:px-2 flex items-center justify-center sm:justify-start`}
              >
                <span className="sm:hidden">{band.charAt(0)}</span>
                <span className="hidden sm:inline">{band}</span>
              </div>
              {DAYS.map((_, d) => {
                const cell = cellAt(d, b);
                return (
                  <motion.div
                    key={`${band}-${d}`}
                    data-cell={cell ? `${d}-${b}` : undefined}
                    data-retried={cell?.retried ? "true" : undefined}
                    {...(cell ? cellAnim(cell) : {})}
                    className={`border-b border-s ${m.rule.grid} px-1.5 py-2 sm:px-2 sm:py-3 min-h-[2.75rem] sm:min-h-[4.25rem] transition-colors ${
                      cell ? "bg-ink/[0.06] hover:bg-marker/10 sm:bg-transparent" : ""
                    }`}
                  >
                    {cell && (
                      <>
                        <div
                          className={`${m.text.body} text-xs sm:text-sm font-medium leading-tight truncate`}
                        >
                          {cell.role}
                        </div>
                        <div className={`${m.text.data} hidden sm:block text-xs text-ink/60 mt-1`}>
                          {cell.hours}
                        </div>
                      </>
                    )}
                  </motion.div>
                );
              })}
            </div>
          ))}
        </div>

        {/* footline */}
        <div className="px-4 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-1">
          <span className={`${m.text.data} text-sm`}>
            <span data-total="shifts">
              <Count to={TOTALS.shifts} />
            </span>{" "}
            <span className="text-ink/60">shifts</span>
          </span>
          <span className={`${m.text.data} text-sm`}>
            <span data-total="people">
              <Count to={TOTALS.people} />
            </span>{" "}
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
