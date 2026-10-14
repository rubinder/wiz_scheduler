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

/** The zone to mention on an availability page, or null to say nothing.
 *  Only an employee with an explicit timezone that differs from one of their
 *  locations' zones gets a note -- nobody, by default. */
export function availabilityZoneNote(
  employeeTz: string | null | undefined,
  locationZones: string[],
): string | null {
  if (!employeeTz) return null;
  return locationZones.some((z) => z !== employeeTz) ? employeeTz : null;
}

/** The zones of an employee's locations, for `availabilityZoneNote`.
 *  Unknown location ids are skipped. */
export function employeeLocationZones(
  locationIds: string[] | null | undefined,
  zoneByLocationId: Map<string, string>,
): string[] {
  return (locationIds ?? [])
    .map((id) => zoneByLocationId.get(id))
    .filter((z): z is string => !!z);
}
