import { marketing as m } from "../theme";

/**
 * Classes for the "Already have an account? Sign in" cross-link on the
 * Register page (#110).
 *
 * Once a visitor has authenticated with Google (`googleSignedIn`), most of
 * them already have an account — the Google button is how they'd sign up
 * *and* how they'd sign in, so people who already registered land here by
 * mistake. Doubling the link's size (text-sm's 0.875rem -> 1.75rem) is a
 * subtle nudge toward /login without adding any copy.
 *
 * The link is always set in the site's highlighter orange (`text-marker`) so
 * it reads as the one action on the page besides the form itself.
 */
export function signInLinkClass(googleSignedIn: boolean): string {
  const base = `${m.btn.link} text-marker`;
  return googleSignedIn ? `${base} text-[1.75rem] font-semibold` : base;
}
