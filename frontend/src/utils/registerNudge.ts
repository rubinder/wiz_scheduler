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
 */
export function signInLinkClass(googleSignedIn: boolean): string {
  return googleSignedIn ? `${m.btn.link} text-[1.75rem] font-semibold` : m.btn.link;
}
