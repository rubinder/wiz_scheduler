import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { MarketingShell } from "../components/marketing/MarketingNav";
import { marketing as m } from "../theme";

export default function Contact() {
  useDocumentTitle("Contact Us");

  return (
    <MarketingShell>
      <main className="max-w-[92rem] mx-auto px-6">
        {/* Hero */}
        <section className="pt-12 lg:pt-20 pb-16">
          <div className="max-w-[62ch] text-start">
            <h1
              className={`${m.text.display} font-display text-5xl lg:text-6xl font-semibold leading-[0.95] mb-6`}
            >
              Get in Touch
            </h1>
            <p className={`${m.text.muted} text-lg leading-relaxed max-w-[62ch]`}>
              Questions about Wiz Scheduler? We'd love to hear from you.
            </p>
          </div>
        </section>

        {/* Contact Info & Form */}
        <section className="max-w-3xl pb-20">
          <div className="grid md:grid-cols-2 gap-12">
            {/* Contact Methods */}
            <div>
              <h2 className={`${m.text.display} text-2xl font-semibold mb-6`}>Contact Methods</h2>
              <div className="space-y-6">
                <div>
                  <h3 className="font-semibold mb-1">Email</h3>
                  <a
                    href="mailto:hello@wizscheduler.com"
                    className="text-blue-600 hover:underline"
                  >
                    hello@wizscheduler.com
                  </a>
                </div>
                <div>
                  <h3 className="font-semibold mb-1">Legal & Privacy</h3>
                  <a
                    href="mailto:legal@wizscheduler.com"
                    className="text-blue-600 hover:underline"
                  >
                    legal@wizscheduler.com
                  </a>
                </div>
                <div>
                  <h3 className="font-semibold mb-1">Support</h3>
                  <p className={`${m.text.muted} text-sm`}>
                    Questions about your account? Log in and use the help chat in-app.
                  </p>
                </div>
              </div>

              <div className="mt-12 pt-12 border-t border-gray-200">
                <h3 className="font-semibold mb-4">Company</h3>
                <p className={`${m.text.muted} text-sm mb-2`}>
                  <strong>Suggestival LLC</strong>
                </p>
                <p className={`${m.text.muted} text-sm`}>
                  Building AI-powered scheduling for restaurants and retail teams.
                </p>
              </div>
            </div>

            {/* Quick Links */}
            <div>
              <h2 className={`${m.text.display} text-2xl font-semibold mb-6`}>Resources</h2>
              <ul className="space-y-3">
                <li>
                  <a href="/privacy-policy" className="text-blue-600 hover:underline">
                    Privacy Policy
                  </a>
                </li>
                <li>
                  <a href="/terms" className="text-blue-600 hover:underline">
                    Terms of Service
                  </a>
                </li>
                <li>
                  <a href="/dpa" className="text-blue-600 hover:underline">
                    Data Processing Agreement
                  </a>
                </li>
                <li>
                  <a href="/about" className="text-blue-600 hover:underline">
                    About Us
                  </a>
                </li>
                <li>
                  <a href="/features" className="text-blue-600 hover:underline">
                    Features
                  </a>
                </li>
                <li>
                  <a href="/pricing" className="text-blue-600 hover:underline">
                    Pricing
                  </a>
                </li>
              </ul>
            </div>
          </div>
        </section>
      </main>
    </MarketingShell>
  );
}
