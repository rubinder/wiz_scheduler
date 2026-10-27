/** Fill a schedule-lock message (#180).
 *
 *  A 409 `schedule_locked` names the busy location when the server knows it;
 *  then `withLocation` is used with `{location}` filled in, else `base`.
 *  Every `{key}` in `vars` is replaced wherever it appears. */
export function scheduleLockedMessage(
  base: string,
  withLocation: string,
  vars: Record<string, string>,
  locationName?: string | null,
): string {
  const template = locationName ? withLocation : base;
  const values = locationName ? { ...vars, location: locationName } : vars;
  return Object.entries(values).reduce(
    (text, [key, value]) => text.split(`{${key}}`).join(value),
    template,
  );
}
