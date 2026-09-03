/**
 * A stable, opaque per-browser id sent with registration.
 *
 * Anti-abuse signal ONLY: the backend records it next to the ownership
 * group and never denies anything on it (see backend/services/
 * signup_signals.py). It identifies a browser, not a person — cleared
 * storage, a private window, or a different browser all produce a new one,
 * and that is an accepted gap rather than something to work around with
 * fingerprinting.
 *
 * Deliberately random and meaningless: it carries no account, email, or
 * device information, so it tells us nothing about a user beyond "these two
 * signups came from the same browser".
 */
const STORAGE_KEY = "wz_device_id";

function randomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Older Safari. Not cryptographically important — this is a grouping key.
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getDeviceId(): string | undefined {
  try {
    const existing = localStorage.getItem(STORAGE_KEY);
    if (existing) return existing;
    const fresh = randomId();
    localStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    // Storage disabled or full. Registration must not care: the field is
    // optional and its absence is an expected hole in the signal.
    return undefined;
  }
}
