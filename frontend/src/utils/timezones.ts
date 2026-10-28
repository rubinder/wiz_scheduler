/** IANA timezone helpers. The zone list comes from the browser, so there is
 *  no bundled database and no new dependency. */

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function timeZoneOptions(current?: string | null): { value: string; label: string }[] {
  let zones: string[] = [];
  try {
    const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
    zones = intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    zones = [];
  }
  const browser = browserTimeZone();
  const all = new Set<string>([...zones, "UTC", browser]);
  if (current) all.add(current);
  const ordered = [browser, ...[...all].filter((z) => z !== browser).sort()];
  return ordered.map((z) => ({ value: z, label: z.replace(/_/g, " ") }));
}

/** The zone(s) to name on an availability page: availability is always the
 *  location's local time (#171), so this is the employee's location zones,
 *  deduplicated, or null when none is known. */
export function locationZoneNote(locationZones: string[]): string | null {
  const unique = [...new Set(locationZones.filter(Boolean))];
  return unique.length ? unique.map((z) => z.replace(/_/g, " ")).join(", ") : null;
}

/** The zones of an employee's locations, for `locationZoneNote`.
 *  Unknown location ids are skipped. */
export function employeeLocationZones(
  locationIds: string[] | null | undefined,
  zoneByLocationId: Map<string, string>,
): string[] {
  return (locationIds ?? [])
    .map((id) => zoneByLocationId.get(id))
    .filter((z): z is string => !!z);
}
