import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import LanguageSelector from "../components/shared/LanguageSelector";
import { text, bg, border, action } from "../theme";

export default function Landing() {
  const { t } = useLanguage();
  useDocumentTitle("AI-Powered Employee Scheduling");

  const features = [
    { title: t.landing.featAITitle, desc: t.landing.featAIDesc },
    { title: t.landing.featStrategiesTitle, desc: t.landing.featStrategiesDesc },
    { title: t.landing.featMultiLocTitle, desc: t.landing.featMultiLocDesc },
    { title: t.landing.featHourCapsTitle, desc: t.landing.featHourCapsDesc },
    { title: t.landing.featDayRulesTitle, desc: t.landing.featDayRulesDesc },
    { title: t.landing.featSelfServiceTitle, desc: t.landing.featSelfServiceDesc },
    { title: t.landing.feat7shiftsTitle, desc: t.landing.feat7shiftsDesc },
    { title: t.landing.featDeputyTitle, desc: t.landing.featDeputyDesc },
    { title: t.landing.featGDPRTitle, desc: t.landing.featGDPRDesc },
    { title: t.landing.featAffinitiesTitle, desc: t.landing.featAffinitiesDesc },
    { title: t.landing.featRoleEquivTitle, desc: t.landing.featRoleEquivDesc },
    { title: t.landing.featLangsTitle, desc: t.landing.featLangsDesc },
  ];

  const strategies = [
    {
      name: t.landing.stratRotation,
      tag: t.landing.stratRotationTag,
      badge: "bg-emerald-100 text-emerald-700 border border-emerald-300",
      desc: t.landing.stratRotationDesc,
    },
    {
      name: t.landing.stratRotationHistory,
      tag: t.landing.stratRotationHistoryTag,
      badge: "bg-emerald-100 text-emerald-700 border border-emerald-300",
      desc: t.landing.stratRotationHistoryDesc,
    },
    {
      name: t.landing.stratMaxHours,
      tag: t.landing.stratMaxHoursTag,
      badge: "bg-emerald-100 text-emerald-700 border border-emerald-300",
      desc: t.landing.stratMaxHoursDesc,
    },
    {
      name: t.landing.stratAI,
      tag: t.landing.stratAITag,
      badge: "bg-accent/10 text-accent-dark border border-accent/20",
      desc: t.landing.stratAIDesc,
    },
  ];

  const compliance = [
    { title: t.landing.compDataExportTitle, desc: t.landing.compDataExportDesc },
    { title: t.landing.compErasureTitle, desc: t.landing.compErasureDesc },
    { title: t.landing.compConsentTitle, desc: t.landing.compConsentDesc },
    { title: t.landing.compNoNamesTitle, desc: t.landing.compNoNamesDesc },
  ];

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Wiz Scheduler",
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    description:
      "AI-powered employee scheduling that automatically respects availability, roles, skill levels, and team preferences.",
    offers: {
      "@type": "Offer",
      price: "0",
      priceCurrency: "USD",
    },
  };

  return (
    <div className="min-h-screen">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Nav */}
      <nav className={`fixed top-0 inset-x-0 z-50 bg-white/50 backdrop-blur-2xl border-b ${border.default}`}>
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="w-8 h-8" />
            <span className={`text-xl font-bold ${text.primary} tracking-wide`}>Wiz Scheduler</span>
          </div>
          <div className="flex items-center gap-4">
            <LanguageSelector />
            <Link to="/login" className={`text-sm ${text.secondary} hover:${text.heading} transition-colors`}>
              {t.login.signIn}
            </Link>
            <Link
              to="/register"
              className="glass-btn-primary text-sm"
            >
              {t.register.registerBtn}
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="pt-32 pb-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-accent/10 border border-accent/20 text-accent-dark text-sm mb-8">
            {t.landing.badge}
          </div>
          <h1 className={`text-5xl sm:text-6xl font-extrabold ${text.heading} leading-tight mb-6`}>
            {t.landing.heroTitle}{" "}
            <span className="bg-gradient-to-r from-accent to-sage-dark bg-clip-text text-transparent">
              {t.landing.heroTitleAccent}
            </span>
          </h1>
          <p className={`text-xl ${text.muted} max-w-2xl mx-auto mb-10 leading-relaxed`}>
            {t.landing.heroDesc}
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link to="/register" className="glass-btn-primary px-8 py-3 text-base">
              {t.landing.getStarted}
            </Link>
            <a href="#pricing" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.viewPricing}
            </a>
            <a href="#demo" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.viewDemo}
            </a>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <h2 className={`text-3xl font-bold ${text.heading} text-center mb-4`}>{t.landing.featuresTitle}</h2>
          <p className={`${text.muted} text-center mb-16 max-w-2xl mx-auto`}>
            {t.landing.featuresDesc}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((f) => (
              <div key={f.title} className={`glass-card p-6 ${bg.cardHover} transition-all`}>
                <h3 className={`text-lg font-semibold ${text.primary} mb-2`}>{f.title}</h3>
                <p className={`text-sm ${text.muted} leading-relaxed`}>{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Demo Video */}
      <section id="demo" className="py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className={`text-3xl font-bold ${text.heading} text-center mb-4`}>{t.landing.demoTitle}</h2>
          <p className={`${text.muted} text-center mb-12 max-w-2xl mx-auto`}>
            {t.landing.demoDesc}
          </p>
          <div className="glass-card p-2 rounded-2xl overflow-hidden">
            <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
              <iframe
                className="absolute inset-0 w-full h-full rounded-xl"
                src="https://www.youtube.com/embed/t1FblchNbb8"
                title={t.landing.demoTitle}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
              />
            </div>
          </div>
          <div className="flex justify-center mt-8">
            <Link to="/features" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.exploreDashboard}
            </Link>
          </div>
        </div>
      </section>

      {/* Strategies */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <h2 className={`text-3xl font-bold ${text.heading} text-center mb-4`}>{t.landing.strategiesTitle}</h2>
          <p className={`${text.muted} text-center mb-16 max-w-2xl mx-auto`}>
            {t.landing.strategiesDesc}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {strategies.map((s) => (
              <div key={s.name} className="glass-card p-6">
                <div className="flex items-center gap-3 mb-3">
                  <span className={`inline-block px-3 py-1 rounded-full text-xs font-semibold ${s.badge}`}>
                    {s.tag}
                  </span>
                  <h3 className={`text-lg font-semibold ${text.primary}`}>{s.name}</h3>
                </div>
                <p className={`text-sm ${text.muted} leading-relaxed`}>{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className={`text-3xl font-bold ${text.heading} text-center mb-4`}>{t.landing.pricingTitle}</h2>
          <p className={`${text.muted} text-center mb-16 max-w-2xl mx-auto`}>
            {t.landing.pricingDesc}
          </p>

          {/* Base plan */}
          <div className="glass-card p-8 mb-10 text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-accent/10 border border-accent/20 text-accent-dark text-sm mb-4">
              {t.landing.allInOnePlan}
            </div>
            <div className={`text-5xl font-extrabold ${text.heading} mb-2`}>$18<span className={`text-xl font-normal ${text.muted}`}> {t.landing.pricePerMonth}</span></div>
            <p className={`${text.muted} mb-8`}>{t.landing.basePlanDesc}</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 max-w-4xl mx-auto">
              <div>
                <div className={`text-2xl font-bold ${text.heading}`}>$2.00</div>
                <div className={`text-sm ${text.muted} mt-1`}>{t.landing.aiCredits}</div>
              </div>
              <div>
                <div className={`text-2xl font-bold ${text.heading}`}>50</div>
                <div className={`text-sm ${text.muted} mt-1`}>{t.landing.compSchedules}</div>
              </div>
              <div>
                <div className={`text-2xl font-bold ${text.heading}`}>0.5 GB</div>
                <div className={`text-sm ${text.muted} mt-1`}>{t.landing.storageIncluded}</div>
              </div>
              <div>
                <div className={`text-2xl font-bold ${text.heading}`}>1K</div>
                <div className={`text-sm ${text.muted} mt-1`}>{t.landing.employeesIncluded}</div>
              </div>
            </div>
            <p className={`${text.placeholder} text-sm mt-6`}>
              {t.landing.normalStrategiesNote}
            </p>
          </div>

          {/* Overage pricing */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-accent/10 border border-accent/20 flex items-center justify-center text-accent-dark text-sm font-bold">
                  AI
                </div>
                <h3 className={`text-base font-semibold ${text.primary}`}>{t.landing.aiScheduling}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className={`flex justify-between ${text.secondary}`}>
                  <span>{t.landing.included}</span>
                  <span className={`${text.primary} font-medium`}>$2.00 / mo</span>
                </div>
                <div className={`border-t ${border.subtle} pt-2 flex justify-between ${text.secondary}`}>
                  <span>{t.landing.overage}</span>
                  <span className={`${text.primary} font-medium`}>Cost x 130%</span>
                </div>
                <p className={`text-xs ${text.muted} pt-1`}>
                  {t.landing.aiOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-amber-100 border border-amber-300 flex items-center justify-center text-amber-700 text-sm font-bold">
                  GEN
                </div>
                <h3 className={`text-base font-semibold ${text.primary}`}>{t.landing.schedules}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className={`flex justify-between ${text.secondary}`}>
                  <span>{t.landing.included}</span>
                  <span className={`${text.primary} font-medium`}>50 / month</span>
                </div>
                <div className={`border-t ${border.subtle} pt-2 flex justify-between ${text.secondary}`}>
                  <span>{t.landing.overage}</span>
                  <span className={`${text.primary} font-medium`}>$0.10 / 50</span>
                </div>
                <p className={`text-xs ${text.muted} pt-1`}>
                  {t.landing.schedulesOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-cyan-100 border border-cyan-300 flex items-center justify-center text-cyan-700 text-sm font-bold">
                  DB
                </div>
                <h3 className={`text-base font-semibold ${text.primary}`}>{t.landing.storage}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className={`flex justify-between ${text.secondary}`}>
                  <span>{t.landing.included}</span>
                  <span className={`${text.primary} font-medium`}>0.5 GB</span>
                </div>
                <div className={`border-t ${border.subtle} pt-2 flex justify-between ${text.secondary}`}>
                  <span>{t.landing.overage}</span>
                  <span className={`${text.primary} font-medium`}>$0.50 / GB</span>
                </div>
                <p className={`text-xs ${text.muted} pt-1`}>
                  {t.landing.storageOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-emerald-100 border border-emerald-300 flex items-center justify-center text-emerald-700 text-sm font-bold">
                  #
                </div>
                <h3 className={`text-base font-semibold ${text.primary}`}>{t.landing.employees}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className={`flex justify-between ${text.secondary}`}>
                  <span>{t.landing.included}</span>
                  <span className={`${text.primary} font-medium`}>1,000</span>
                </div>
                <div className={`border-t ${border.subtle} pt-2 flex justify-between ${text.secondary}`}>
                  <span>{t.landing.overage}</span>
                  <span className={`${text.primary} font-medium`}>$1.00 / 1K</span>
                </div>
                <p className={`text-xs ${text.muted} pt-1`}>
                  {t.landing.employeesOverageNote}
                </p>
              </div>
            </div>
          </div>

          {/* Example */}
          <div className="glass-card p-6 mt-10">
            <h3 className={`text-lg font-semibold ${text.primary} mb-4`}>{t.landing.exampleTitle}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className={`text-left ${text.muted} border-b ${border.subtle}`}>
                    <th className="pb-3 font-medium">{t.landing.exampleItem}</th>
                    <th className="pb-3 font-medium">{t.landing.exampleUsage}</th>
                    <th className="pb-3 font-medium text-right">{t.landing.exampleCost}</th>
                  </tr>
                </thead>
                <tbody className={text.secondary}>
                  <tr className="border-b border-sage/10">
                    <td className="py-3">{t.landing.exampleBase}</td>
                    <td></td>
                    <td className={`text-right ${text.primary} font-medium`}>$18.00</td>
                  </tr>
                  <tr className="border-b border-sage/10">
                    <td className="py-3">{t.landing.exampleSchedules}</td>
                    <td>{t.landing.exampleSchedulesUsage}</td>
                    <td className="text-right text-emerald-600 font-medium">{t.landing.exampleSchedulesCost}</td>
                  </tr>
                  <tr className="border-b border-sage/10">
                    <td className="py-3">{t.landing.exampleAI}</td>
                    <td>{t.landing.exampleAIUsage}</td>
                    <td className="text-right text-emerald-600 font-medium">{t.landing.exampleAICost}</td>
                  </tr>
                  <tr className="border-b border-sage/10">
                    <td className="py-3">{t.landing.exampleStorage}</td>
                    <td>{t.landing.exampleStorageUsage}</td>
                    <td className="text-right text-emerald-600 font-medium">{t.landing.exampleStorageCost}</td>
                  </tr>
                  <tr className="border-b border-sage/10">
                    <td className="py-3">{t.landing.exampleEmployees}</td>
                    <td>{t.landing.exampleEmployeesUsage}</td>
                    <td className="text-right text-emerald-600 font-medium">{t.landing.exampleEmployeesCost}</td>
                  </tr>
                  <tr>
                    <td className={`py-3 font-semibold ${text.heading}`}>{t.landing.exampleTotal}</td>
                    <td></td>
                    <td className={`text-right ${text.heading} font-bold text-lg`}>$20.30</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* GDPR */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className={`text-3xl font-bold ${text.heading} mb-4`}>{t.landing.complianceTitle}</h2>
          <p className={`${text.placeholder} mb-10 max-w-2xl mx-auto`}>
            {t.landing.complianceDesc}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {compliance.map((c) => (
              <div key={c.title} className="glass-card p-5 text-center">
                <h4 className={`text-sm font-semibold ${text.heading} mb-1`}>{c.title}</h4>
                <p className={`text-xs ${text.muted}`}>{c.desc}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex items-center justify-center gap-4 text-sm">
            <Link to="/privacy-policy" className={action.link}>{t.gdpr.privacyPolicy}</Link>
            <span className={text.secondary}>|</span>
            <Link to="/terms" className={action.link}>{t.gdpr.termsOfService}</Link>
            <span className={text.secondary}>|</span>
            <Link to="/dpa" className={action.link}>{t.gdpr.dpa}</Link>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className={`text-4xl font-bold ${text.heading} mb-4`}>{t.landing.ctaTitle}</h2>
          <p className={`${text.placeholder} mb-8 text-lg`}>
            {t.landing.ctaDesc}
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link to="/register" className="glass-btn-primary px-8 py-3 text-base">
              {t.landing.ctaBtn}
            </Link>
            <Link to="/login" className="glass-btn-secondary px-8 py-3 text-base">
              {t.login.signIn}
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={`border-t ${border.default} py-8 px-6`}>
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className={`flex items-center gap-2 ${text.muted} text-sm`}>
            <img src="/favicon.svg" alt="" className="w-5 h-5" />
            <span>Wiz Scheduler</span>
          </div>
          <div className={`flex items-center gap-4 text-xs ${text.muted}`}>
            <Link to="/features" className="hover:text-gray-700">{t.landing.featuresLink}</Link>
            <Link to="/privacy-policy" className="hover:text-gray-700">{t.gdpr.privacyPolicy}</Link>
            <Link to="/terms" className="hover:text-gray-700">{t.gdpr.termsOfService}</Link>
            <Link to="/dpa" className="hover:text-gray-700">{t.gdpr.dpa}</Link>
          </div>
          <div className={`text-xs ${text.muted}`}>Suggestival LLC</div>
        </div>
      </footer>
    </div>
  );
}

