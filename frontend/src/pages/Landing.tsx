import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { MarketingShell } from "../components/marketing/MarketingNav";
import SectionRule from "../components/marketing/SectionRule";
import RotaHero from "../components/marketing/RotaHero";
import { marketing as m } from "../theme";

export default function Landing() {
  const { t } = useLanguage();
  useDocumentTitle("AI-Powered Employee Scheduling");

  const principalFeatures = [
    { title: t.landing.featAITitle, desc: t.landing.featAIDesc },
    { title: t.landing.featStrategiesTitle, desc: t.landing.featStrategiesDesc },
    { title: t.landing.featMultiLocTitle, desc: t.landing.featMultiLocDesc },
  ];

  const supportingFeatures = [
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
      desc: t.landing.stratRotationDesc,
    },
    {
      name: t.landing.stratRotationHistory,
      tag: t.landing.stratRotationHistoryTag,
      desc: t.landing.stratRotationHistoryDesc,
    },
    {
      name: t.landing.stratMaxHours,
      tag: t.landing.stratMaxHoursTag,
      desc: t.landing.stratMaxHoursDesc,
    },
    {
      name: t.landing.stratAI,
      tag: t.landing.stratAITag,
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
    <MarketingShell>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <RotaHero />

      <main className="max-w-[92rem] mx-auto px-6">
        <div className="flex justify-end mb-8">
          <a href="#demo" className={`${m.btn.link} text-sm`}>{t.landing.viewDemo}</a>
        </div>

        {/* Features */}
        <SectionRule eyebrow="01 — Capability" title={t.landing.featuresTitle} />
        <p className={`${m.text.muted} max-w-[60ch] mb-10`}>{t.landing.featuresDesc}</p>
        <div className="grid gap-10 md:grid-cols-3 mb-16">
          {principalFeatures.map((f) => (
            <div key={f.title}>
              <h3 className={`${m.text.display} font-display text-2xl font-semibold mb-3`}>
                {f.title}
              </h3>
              <p className={`${m.text.muted} leading-relaxed`}>{f.desc}</p>
            </div>
          ))}
        </div>
        <dl className={`border-t ${m.rule.line} mb-24`}>
          {supportingFeatures.map((f) => (
            <div
              key={f.title}
              className={`border-b ${m.rule.line} py-4 grid gap-1 md:grid-cols-[18rem_1fr] md:gap-8`}
            >
              <dt className={`${m.text.body} font-medium`}>{f.title}</dt>
              <dd className={`${m.text.muted} text-sm leading-relaxed`}>{f.desc}</dd>
            </div>
          ))}
        </dl>

        {/* Demo — the one dark band */}
        <div id="demo" className={`${m.inverse} -mx-6 px-6 py-20 mb-24 scroll-mt-20`}>
          <div className="max-w-5xl mx-auto">
            <p className={`${m.text.meta} !text-newsprint/60 mb-4`}>02 — Proof</p>
            <h2 className="font-display text-3xl md:text-5xl font-semibold leading-[1.05] mb-4">
              {t.landing.demoTitle}
            </h2>
            <p className="text-newsprint/70 font-body max-w-[60ch] mb-10">{t.landing.demoDesc}</p>
            <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
              <iframe
                className="absolute inset-0 w-full h-full"
                src="https://www.youtube.com/embed/t1FblchNbb8"
                title={t.landing.demoTitle}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
              />
            </div>
            <div className="mt-10">
              <Link
                to="/features"
                className={`${m.btn.secondary} !border-newsprint/40 !text-newsprint hover:!border-newsprint`}
              >
                {t.landing.exploreDashboard}
              </Link>
            </div>
          </div>
        </div>

        {/* Strategies — AI is the product, rules are the floor */}
        <SectionRule eyebrow="03 — Method" title={t.landing.strategiesTitle} />
        <p className={`${m.text.muted} max-w-[60ch] mb-10`}>{t.landing.strategiesDesc}</p>
        <div className={`${m.surface} border ${m.rule.heavy} p-6 md:p-10 mb-10`}>
          <p className={`${m.text.meta} mb-3`}>
            <span className={m.mark}>{strategies[3].tag}</span>
          </p>
          <h3 className={`${m.text.display} font-display text-3xl font-semibold mb-4`}>
            {strategies[3].name}
          </h3>
          <p className={`${m.text.muted} leading-relaxed max-w-[62ch]`}>
            {strategies[3].desc}
          </p>
        </div>
        <div className={`grid gap-px md:grid-cols-3 bg-rule border ${m.rule.line} mb-24`}>
          {strategies.slice(0, 3).map((s) => (
            <div key={s.name} className="bg-newsprint p-6">
              <p className={`${m.text.meta} mb-2`}>{s.tag}</p>
              <h3 className={`${m.text.body} font-medium mb-2`}>{s.name}</h3>
              <p className={`${m.text.muted} text-sm leading-relaxed`}>{s.desc}</p>
            </div>
          ))}
        </div>

        {/* Pricing — receipt first */}
        <SectionRule id="pricing" eyebrow="04 — Cost" title={t.landing.pricingTitle} />
        <p className={`${m.text.muted} max-w-[60ch] mb-10`}>{t.landing.pricingDesc}</p>
        <div className={`${m.surface} border ${m.rule.heavy} p-6 md:p-10 max-w-3xl mb-10`}>
          <p className={`${m.text.meta} mb-6`}>{t.landing.exampleTitle}</p>
          <div className="overflow-x-auto">
            <table className="w-full font-data text-sm tabular-nums">
              <thead>
                <tr className={`border-b ${m.rule.line}`}>
                  <th className={`${m.text.meta} text-start pb-3`}>{t.landing.exampleItem}</th>
                  <th className={`${m.text.meta} text-start pb-3`}>{t.landing.exampleUsage}</th>
                  <th className={`${m.text.meta} text-end pb-3`}>{t.landing.exampleCost}</th>
                </tr>
              </thead>
              <tbody>
                <tr className={`border-b ${m.rule.grid}`}>
                  <td className="py-2.5 text-start">{t.landing.exampleBase}</td>
                  <td className="py-2.5 text-start"></td>
                  <td className="py-2.5 text-end">$18.00</td>
                </tr>
                <tr className={`border-b ${m.rule.grid}`}>
                  <td className="py-2.5 text-start">{t.landing.exampleSchedules}</td>
                  <td className="py-2.5 text-start">{t.landing.exampleSchedulesUsage}</td>
                  <td className={`py-2.5 text-end ${m.text.clear}`}>{t.landing.exampleSchedulesCost}</td>
                </tr>
                <tr className={`border-b ${m.rule.grid}`}>
                  <td className="py-2.5 text-start">{t.landing.exampleAI}</td>
                  <td className="py-2.5 text-start">{t.landing.exampleAIUsage}</td>
                  <td className={`py-2.5 text-end ${m.text.clear}`}>{t.landing.exampleAICost}</td>
                </tr>
                <tr className={`border-b ${m.rule.grid}`}>
                  <td className="py-2.5 text-start">{t.landing.exampleStorage}</td>
                  <td className="py-2.5 text-start">{t.landing.exampleStorageUsage}</td>
                  <td className={`py-2.5 text-end ${m.text.clear}`}>{t.landing.exampleStorageCost}</td>
                </tr>
                <tr className={`border-b ${m.rule.grid}`}>
                  <td className="py-2.5 text-start">{t.landing.exampleEmployees}</td>
                  <td className="py-2.5 text-start">{t.landing.exampleEmployeesUsage}</td>
                  <td className={`py-2.5 text-end ${m.text.clear}`}>{t.landing.exampleEmployeesCost}</td>
                </tr>
                <tr className={`border-t-2 border-ink pt-4 font-semibold`}>
                  <td className="py-4">{t.landing.exampleTotal}</td>
                  <td></td>
                  <td className="py-4 text-end text-lg">$20.30</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className={`border ${m.rule.line} p-6 md:p-8 mb-10`}>
          <p className={`${m.text.meta} mb-3`}>{t.landing.allInOnePlan}</p>
          <div className={`${m.text.display} font-display text-5xl font-semibold mb-2`}>
            $18<span className={`${m.text.muted} text-xl font-normal`}> {t.landing.pricePerMonth}</span>
          </div>
          <p className={`${m.text.muted} mb-8 max-w-[60ch]`}>{t.landing.basePlanDesc}</p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 max-w-4xl">
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>$2.00</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.aiCredits}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>50</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.compSchedules}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>0.5 GB</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.storageIncluded}</div>
            </div>
            <div>
              <div className={`${m.text.data} text-2xl font-semibold`}>1K</div>
              <div className={`${m.text.meta} mt-1`}>{t.landing.employeesIncluded}</div>
            </div>
          </div>
          <p className={`${m.text.muted} text-sm mt-6`}>{t.landing.normalStrategiesNote}</p>
        </div>

        <div className={`grid gap-px md:grid-cols-4 bg-rule border ${m.rule.line} mb-24`}>
          <div className="bg-newsprint p-5">
            <p className={`${m.text.meta} mb-2`}>AI</p>
            <h3 className={`${m.text.body} font-medium mb-3`}>{t.landing.aiScheduling}</h3>
            <div className="space-y-2 text-sm">
              <div className={`flex justify-between ${m.text.muted}`}>
                <span>{t.landing.included}</span>
                <span className={m.text.data}>$2.00 / mo</span>
              </div>
              <div className={`border-t ${m.rule.grid} pt-2 flex justify-between ${m.text.muted}`}>
                <span>{t.landing.overage}</span>
                <span className={m.text.data}>Cost x 130%</span>
              </div>
              <p className={`${m.text.muted} text-xs pt-1`}>{t.landing.aiOverageNote}</p>
            </div>
          </div>

          <div className="bg-newsprint p-5">
            <p className={`${m.text.meta} mb-2`}>RUNS</p>
            <h3 className={`${m.text.body} font-medium mb-3`}>{t.landing.schedules}</h3>
            <div className="space-y-2 text-sm">
              <div className={`flex justify-between ${m.text.muted}`}>
                <span>{t.landing.included}</span>
                <span className={m.text.data}>50 / month</span>
              </div>
              <div className={`border-t ${m.rule.grid} pt-2 flex justify-between ${m.text.muted}`}>
                <span>{t.landing.overage}</span>
                <span className={m.text.data}>$0.10 / 50</span>
              </div>
              <p className={`${m.text.muted} text-xs pt-1`}>{t.landing.schedulesOverageNote}</p>
            </div>
          </div>

          <div className="bg-newsprint p-5">
            <p className={`${m.text.meta} mb-2`}>DATA</p>
            <h3 className={`${m.text.body} font-medium mb-3`}>{t.landing.storage}</h3>
            <div className="space-y-2 text-sm">
              <div className={`flex justify-between ${m.text.muted}`}>
                <span>{t.landing.included}</span>
                <span className={m.text.data}>0.5 GB</span>
              </div>
              <div className={`border-t ${m.rule.grid} pt-2 flex justify-between ${m.text.muted}`}>
                <span>{t.landing.overage}</span>
                <span className={m.text.data}>$0.50 / GB</span>
              </div>
              <p className={`${m.text.muted} text-xs pt-1`}>{t.landing.storageOverageNote}</p>
            </div>
          </div>

          <div className="bg-newsprint p-5">
            <p className={`${m.text.meta} mb-2`}>TEAM</p>
            <h3 className={`${m.text.body} font-medium mb-3`}>{t.landing.employees}</h3>
            <div className="space-y-2 text-sm">
              <div className={`flex justify-between ${m.text.muted}`}>
                <span>{t.landing.included}</span>
                <span className={m.text.data}>1,000</span>
              </div>
              <div className={`border-t ${m.rule.grid} pt-2 flex justify-between ${m.text.muted}`}>
                <span>{t.landing.overage}</span>
                <span className={m.text.data}>$1.00 / 1K</span>
              </div>
              <p className={`${m.text.muted} text-xs pt-1`}>{t.landing.employeesOverageNote}</p>
            </div>
          </div>
        </div>

        {/* GDPR — quiet, reassurance not pitch */}
        <SectionRule eyebrow="05 — Assurance" title={t.landing.complianceTitle} />
        <p className={`${m.text.muted} max-w-[60ch] mb-10`}>{t.landing.complianceDesc}</p>
        <dl className={`grid gap-px sm:grid-cols-2 lg:grid-cols-4 bg-rule border ${m.rule.line} mb-10`}>
          {compliance.map((c) => (
            <div key={c.title} className="bg-newsprint p-5">
              <dt className={`${m.text.body} text-sm font-medium mb-1`}>{c.title}</dt>
              <dd className={`${m.text.muted} text-xs leading-relaxed`}>{c.desc}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm mb-24">
          <Link to="/privacy-policy" className={m.btn.link}>{t.gdpr.privacyPolicy}</Link>
          <Link to="/terms" className={m.btn.link}>{t.gdpr.termsOfService}</Link>
          <Link to="/dpa" className={m.btn.link}>{t.gdpr.dpa}</Link>
        </div>

        {/* CTA — one action */}
        <div className="max-w-2xl mb-24">
          <h2 className={`${m.text.display} font-display text-4xl font-semibold mb-4`}>
            {t.landing.ctaTitle}
          </h2>
          <p className={`${m.text.muted} text-lg mb-8 max-w-[55ch]`}>{t.landing.ctaDesc}</p>
          <div className="flex flex-wrap items-center gap-6">
            <Link to="/register" className={m.btn.primary}>
              {t.landing.ctaBtn}
            </Link>
            <Link to="/login" className={m.btn.link}>
              {t.login.signIn}
            </Link>
          </div>
        </div>
      </main>
    </MarketingShell>
  );
}
