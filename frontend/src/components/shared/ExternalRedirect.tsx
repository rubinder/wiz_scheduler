import { useEffect } from "react";

interface Props {
  /** Absolute URL to send the browser to. Must be a full https:// URL —
   *  this does a real navigation, not a client-side route change. */
  to: string;
}

/**
 * Sends the browser to an address outside this app (the marketing site,
 * now that it owns the apex domain — see docs/superpowers/specs
 * 2026-09-22-marketing-site-design.md). `Navigate` from react-router-dom
 * only handles same-origin client-side routes; a cross-origin move needs
 * a real navigation.
 */
export default function ExternalRedirect({ to }: Props) {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);
  return null;
}
