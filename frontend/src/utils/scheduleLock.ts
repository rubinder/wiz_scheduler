/** Fill a schedule-lock message (#180).
 *
 *  A 409 `schedule_locked` names the busy location when the server knows it;
 *  then `withLocation` is used with `{location}` filled in, else `base`.
 *  When the server says the busy location is only locked because it shares
 *  employees with this one (`sharedStaff`, #222), `sharedWithLocation` is
 *  used instead, if given. Every `{key}` in `vars` is replaced wherever it
 *  appears. */
export function scheduleLockedMessage(
  base: string,
  withLocation: string,
  vars: Record<string, string>,
  locationName?: string | null,
  sharedWithLocation?: string,
  sharedStaff = false,
): string {
  const template = !locationName
    ? base
    : sharedStaff && sharedWithLocation
      ? sharedWithLocation
      : withLocation;
  const values = locationName ? { ...vars, location: locationName } : vars;
  return Object.entries(values).reduce(
    (text, [key, value]) => text.split(`{${key}}`).join(value),
    template,
  );
}
