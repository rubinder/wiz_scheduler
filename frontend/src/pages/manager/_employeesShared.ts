// Shared pure helpers used across the manager employee pages
// (Availability + Association). Extracted from EmployeeAssociation.tsx
// to avoid duplication when that page is split.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Translations = any;

export function getLevelOptions(t: Translations) {
  return [
    { value: "1", label: t.association.levelMustTogether },
    { value: "0.5", label: t.association.levelPreferTogether },
    { value: "0", label: t.association.levelNeutral },
    { value: "-0.5", label: t.association.levelPreferApart },
    { value: "-1", label: t.association.levelMustNotTogether },
  ];
}

export function getLevelLabel(level: number, t: Translations): string {
  if (level >= 1) return t.association.labelMustTogether;
  if (level > 0) return t.association.labelPreferTogether;
  if (level === 0) return t.association.labelNeutral;
  if (level > -1) return t.association.labelPreferApart;
  return t.association.labelMustNotTogether;
}

export const getLevelColor = (level: number): string => {
  if (level >= 1) return "bg-emerald-500/15 text-emerald-300";
  if (level > 0) return "bg-emerald-500/10 text-emerald-400";
  if (level === 0) return "bg-sage/[0.07] text-gray-500";
  if (level > -1) return "bg-orange-500/15 text-orange-300";
  return "bg-red-500/15 text-red-300";
};

export function formatDate(y: number, m: number, d: number) {
  return `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

export function formatTime(iso: string) {
  // Parse hours/minutes directly from the ISO string to avoid browser timezone conversion.
  // Availability times are stored as-is from the source (location-local intent).
  const match = iso.match(/T(\d{2}):(\d{2})/);
  if (!match) return iso;
  let hours = parseInt(match[1], 10);
  const minutes = match[2];
  const ampm = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  return `${hours}:${minutes} ${ampm}`;
}

export function isAllDay(start: string, end: string): boolean {
  const sMatch = start.match(/T(\d{2}):(\d{2})/);
  const eMatch = end.match(/T(\d{2}):(\d{2})/);
  if (!sMatch || !eMatch) return false;
  return sMatch[1] === "00" && sMatch[2] === "00" && eMatch[1] === "23" && eMatch[2] === "59";
}
