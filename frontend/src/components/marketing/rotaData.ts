export type Cell = {
  /** Column index into DAYS. */
  day: number;
  /** Row index into BANDS. */
  band: number;
  /** Short role label shown in the cell. */
  role: string;
  /** Shift window, rendered in tabular figures. */
  hours: string;
  /** True for the cells that flash `marker` before resolving —
   *  mirrors the conflict-retry the real pipeline performs. */
  retried: boolean;
};

export const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"] as const;

export const BANDS = ["OPEN", "MID", "CLOSE"] as const;

export const CELLS: readonly Cell[] = [
  { day: 0, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 0, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 0, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 1, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 1, band: 1, role: "Line", hours: "11–19", retried: true },
  { day: 2, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 2, band: 1, role: "Prep", hours: "10–18", retried: false },
  { day: 2, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 3, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 3, band: 2, role: "Close", hours: "16–00", retried: true },
  { day: 4, band: 0, role: "Front", hours: "06–14", retried: false },
  { day: 4, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 4, band: 2, role: "Close", hours: "16–00", retried: false },
  { day: 5, band: 0, role: "Prep", hours: "07–15", retried: false },
  { day: 5, band: 1, role: "Line", hours: "11–19", retried: false },
  { day: 5, band: 2, role: "Close", hours: "16–00", retried: true },
  { day: 6, band: 0, role: "Front", hours: "08–16", retried: false },
  { day: 6, band: 1, role: "Line", hours: "12–20", retried: false },
];

export const TOTALS = { shifts: 38, people: 12, violations: 0 } as const;
