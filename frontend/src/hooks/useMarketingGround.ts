import { useEffect } from "react";

/**
 * Scopes the newsprint page ground to the canvas *only* while a marketing
 * or auth surface is mounted.
 *
 * `body` still carries the app's cream ground + sage gradients globally
 * (index.css) so the ~30 logged-in pages are untouched. But the browser
 * paints rubber-band overscroll using the *canvas* background, which is
 * `html`'s background if set, else `body`'s propagated one — so on a
 * marketing page (whose content sits in a `bg-newsprint` wrapper, not on
 * `body` itself) overscroll revealed the app's cream/sage ground underneath.
 *
 * Toggling `bg-newsprint` on `<html>` while mounted gives the canvas an
 * explicit background, so overscroll on marketing/auth pages shows
 * newsprint instead. Removing it on unmount restores the app's cream
 * ground for every other route.
 */
export function useMarketingGround() {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("bg-newsprint");
    return () => {
      root.classList.remove("bg-newsprint");
    };
  }, []);
}
