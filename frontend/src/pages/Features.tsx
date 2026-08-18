import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { MarketingShell } from "../components/marketing/MarketingNav";
import { marketing as m } from "../theme";

const PAGE_SLUGS = [
  "dashboard",
  "company",
  "regions",
  "locations",
  "roles",
  "role-equivalents",
  "employees",
  "hour-restrictions",
  "day-blackouts",
  "employee-onboarding",
  "employee-association",
  "shift-templates",
  "schedule",
  "export-schedules",
  "data-privacy",
] as const;

type Slug = (typeof PAGE_SLUGS)[number];

function ordinal(idx: number): string {
  return String(idx + 1).padStart(2, "0");
}

export default function Features() {
  const { t } = useLanguage();
  useDocumentTitle("Manager Tour");

  return (
    <MarketingShell>
      <main className="max-w-[92rem] mx-auto px-6">
        {/* Hero */}
        <section className="pt-12 lg:pt-20 pb-12">
          <div className="max-w-[62ch] text-start">
            <h1
              className={`${m.text.display} font-display text-5xl lg:text-6xl font-semibold leading-[0.95] mb-6`}
            >
              {t.features.pageTitle}
            </h1>
            <p className={`${m.text.muted} text-lg leading-relaxed max-w-[62ch]`}>
              {t.features.pageIntro}
            </p>
          </div>
        </section>

        {/* Body: sticky TOC (desktop) + screen rows */}
        <section className="pb-20">
          <div className="lg:grid lg:grid-cols-[220px_1fr] lg:gap-10">
            {/* TOC */}
            <aside className="hidden lg:block">
              <div className="sticky top-20">
                <h2 className={`${m.text.meta} mb-3`}>{t.features.tocTitle}</h2>
                <nav className="flex flex-col">
                  {PAGE_SLUGS.map((slug, idx) => (
                    <a
                      key={slug}
                      href={`#${slug}`}
                      className={`flex items-baseline gap-2 border-b ${m.rule.line} border-s-2 border-transparent py-2 ${m.text.meta} transition-colors hover:!text-ink hover:border-marker focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink`}
                    >
                      <span className="shrink-0">{ordinal(idx)}</span>
                      <span className="normal-case tracking-normal">
                        {t.features.pages[slug].title}
                      </span>
                    </a>
                  ))}
                </nav>
              </div>
            </aside>

            {/* Rows */}
            <div className="flex flex-col gap-10">
              {PAGE_SLUGS.map((slug, idx) => {
                const reverse = idx % 2 === 1;
                return (
                  <article
                    id={slug}
                    key={slug}
                    className={`${m.surface} border ${m.rule.heavy} p-6 lg:p-10 scroll-mt-24 grid grid-cols-1 lg:grid-cols-2 gap-6 lg:gap-10 items-center ${
                      reverse ? "lg:[&>div:first-child]:order-2" : ""
                    }`}
                  >
                    <div
                      className={`${m.surface} border ${m.rule.line} overflow-hidden aspect-[16/10]`}
                    >
                      <ScreenshotImage slug={slug} title={t.features.pages[slug].title} />
                    </div>
                    <div>
                      <p className={`${m.text.meta} mb-2`}>{ordinal(idx)}</p>
                      <h3
                        className={`${m.text.display} font-display text-2xl font-semibold mb-3`}
                      >
                        {t.features.pages[slug].title}
                      </h3>
                      <p className={`${m.text.muted} max-w-[62ch] leading-relaxed`}>
                        {t.features.pages[slug].desc}
                      </p>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </section>

        {/* CTA — one action */}
        <section className="pb-24">
          <div className="max-w-2xl">
            <h2 className={`${m.text.display} font-display text-4xl font-semibold mb-4`}>
              {t.features.ctaTitle}
            </h2>
            <p className={`${m.text.muted} text-lg mb-8 max-w-[55ch]`}>{t.features.ctaDesc}</p>
            <div className="flex flex-wrap items-center gap-6">
              <Link to="/register" className={m.btn.primary}>
                {t.features.ctaBtn}
              </Link>
              <Link to="/" className={m.btn.link}>
                {t.features.backToHome}
              </Link>
            </div>
          </div>
        </section>
      </main>
    </MarketingShell>
  );
}

function ScreenshotImage({ slug, title }: { slug: Slug; title: string }) {
  return (
    <img
      src={`/screenshots/${slug}.png`}
      alt={`Screenshot of the ${title} page`}
      loading="lazy"
      className="w-full h-full object-cover object-top"
      onError={(e) => {
        const img = e.currentTarget;
        const parent = img.parentElement;
        if (!parent) return;
        img.style.display = "none";
        parent.classList.add("flex", "items-center", "justify-center");
        const placeholder = document.createElement("div");
        placeholder.className = "text-sm text-ink/70 px-4 text-center";
        placeholder.textContent = `Screenshot pending: ${title}`;
        parent.appendChild(placeholder);
      }}
    />
  );
}
