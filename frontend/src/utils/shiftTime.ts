/** Wall-clock reading for shift timestamps.
 *
 *  Shift timestamps carry the *location's* UTC offset
 *  (`2026-08-31T09:00:00-04:00` means 9am at that location). Rendering them
 *  through `Date` re-projects them into whatever timezone the viewer's
 *  browser happens to be in, so a London manager sees a New York 9am shift
 *  as 2pm — silently, with no error and no indication anything is off
 *  (issue #92).
 *
 *  Every function here reads the face off the string and never constructs a
 *  `Date`. This mirrors the backend, which slices "HH:MM" out of timestamps
 *  for exactly the same reason (`scheduling/nodes.py`, `_wall_clock` in #85).
 *
 *  These live in one module because four separate copies of this conversion
 *  had drifted into the codebase and two of them were wrong. One shared
 *  implementation cannot disagree with itself.
 */

/** Extract "HH:MM" from a wall-clock string, wherever the date/offset sit.
 *  Accepts a full ISO datetime or a bare "HH:MM" / "HH:MM:SS". */
export function extractTime(value: string): string {
  const afterDate = value.includes("T") ? value.slice(value.indexOf("T") + 1) : value;
  return afterDate.slice(0, 5);
}

/** Extract the trailing offset ("Z" or "+HH:MM"/"-HH:MM") from a wall-clock
 *  datetime string, or "" if it carries none (e.g. a naive VACANT shift, or
 *  a naive test-DB value).
 *
 *  Callers rebuilding a timestamp after an edit must put this back: dropping
 *  it silently re-anchors the shift to a different instant. */
export function extractOffset(value: string): string {
  const m = value.match(/(Z|[+-]\d{2}:\d{2})$/);
  return m ? m[0] : "";
}

/** Render a shift timestamp as 12-hour local-to-the-location time.
 *
 *  Returns the input unchanged if no hour can be read from it, rather than
 *  rendering "NaN:00 AM". */
export function formatTime(value: string): string {
  const [hStr, mStr] = extractTime(value).split(":");
  const h = parseInt(hStr, 10);
  if (Number.isNaN(h)) return value;
  const m = mStr ?? "00";
  const ampm = h >= 12 ? "PM" : "AM";
  const h12 = h % 12 || 12;
  return `${h12}:${m} ${ampm}`;
}
