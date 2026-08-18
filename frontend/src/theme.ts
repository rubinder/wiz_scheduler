/**
 * Centralized color theme for the application.
 *
 * All semantic color classes are defined here so that changing the palette
 * requires editing only this file (and index.css / tailwind.config.ts for
 * the underlying color values).
 *
 * Usage:  import { theme } from "@/theme";
 *         <h1 className={theme.text.heading}>…</h1>
 */

// ── Text ──

export const text = {
  /** Page titles, headings, strong emphasis */
  heading: "text-gray-900",
  /** Primary body text */
  body: "text-gray-700",
  /** Secondary body text (descriptions, form values) */
  secondary: "text-gray-600",
  /** Muted / helper text (labels, captions, timestamps) */
  muted: "text-gray-500",
  /** Placeholder / disabled text */
  placeholder: "text-gray-400",
  /** Primary content text (slightly stronger than body) */
  primary: "text-gray-800",
};

// ── Backgrounds ──

export const bg = {
  /** Sidebar panel */
  sidebar: "bg-white/40 backdrop-blur-2xl",
  /** Top navigation bar */
  topbar: "bg-white/40 backdrop-blur-xl",
  /** Landing / public nav bar */
  navPublic: "bg-white/50 backdrop-blur-2xl",
  /** Table header row */
  tableHeader: "bg-sage/10",
  /** Row currently being edited */
  editingRow: "bg-accent/10",
  /** Row being added / created */
  addRow: "bg-emerald-50",
  /** Subtle section / panel background */
  section: "bg-sage/[0.07]",
  /** Even more subtle section background */
  sectionSubtle: "bg-sage/[0.05]",
  /** Card hover effect */
  cardHover: "hover:bg-white/70",
  /** Row hover effect */
  rowHover: "hover:bg-sage/[0.07]",
  /** Interactive element hover (buttons, list items) */
  interactiveHover: "hover:bg-sage/[0.12]",
  /** Subtle interactive hover (nav links, list items) */
  subtleHover: "hover:bg-sage/10",
  /** Calendar / export selected date */
  calendarSelected: "bg-accent text-white font-semibold",
  /** Sticky column background (schedule grid) */
  stickyCol: "bg-cream",
  /** Add-form panel (emerald tinted) */
  addForm: "bg-emerald-50 backdrop-blur-xl border border-emerald-200",
  /** Accent-tinted panel */
  accentPanel: "bg-accent/[0.07] backdrop-blur-xl border border-accent/[0.15]",
};

// ── Borders & Dividers ──

export const border = {
  /** Standard structural border */
  default: "border-sage/20",
  /** Subtle / lighter border */
  subtle: "border-sage/15",
  /** Stronger / more prominent border */
  strong: "border-sage/30",
  /** Accent-colored border (active states) */
  accent: "border-accent",
  /** Accent border, lighter variant */
  accentLight: "border-accent/20",
  /** Hover border for interactive elements */
  hoverAccent: "hover:border-accent/30",
  /** Table body row dividers */
  divider: "divide-sage/15",
};

// ── Inline Action Links (Edit / Save / Delete / Cancel) ──

export const action = {
  edit: "text-accent hover:text-accent-light text-sm font-medium",
  save: "text-emerald-400 hover:text-emerald-300 text-sm font-medium",
  delete: "text-red-400 hover:text-red-300 text-sm font-medium",
  cancel: "text-gray-500 hover:text-gray-400 text-sm font-medium",
  /** Text link (login/register footers, etc.) */
  link: "text-accent hover:text-accent-light",
};

// ── Navigation ──

export const nav = {
  /** Active sidebar link */
  active:
    "bg-accent/20 text-accent-dark border border-accent/20 shadow-lg shadow-accent/10",
  /** Inactive sidebar link */
  inactive: "text-gray-500 hover:bg-sage/10 hover:text-gray-800",
};

// ── Tabs ──

export const tab = {
  active: "border-accent text-accent",
  inactive: "border-transparent text-gray-500",
};

// ── Badges / Pills ──

export const badge = {
  role: "bg-accent/10 text-accent-dark border border-accent/20",
  roleSkill: "text-accent",
  location: "bg-emerald-100 text-emerald-700 border border-emerald-300",
  company: "bg-amber-100 text-amber-700 border border-amber-300",
  /** Status badge variants (used by StatusBadge component) */
  status: {
    draft: "bg-yellow-100 text-yellow-700 border border-yellow-300",
    approved: "bg-emerald-100 text-emerald-700 border border-emerald-300",
    ok: "bg-emerald-100 text-emerald-700 border border-emerald-300",
    rejected: "bg-red-100 text-red-700 border border-red-300",
    CONFLICT: "bg-orange-100 text-orange-700 border border-orange-300",
    PARSE_ERROR: "bg-red-100 text-red-700 border border-red-300",
    default: "bg-sage/10 text-gray-600 border border-sage/20",
  } as Record<string, string>,
};

// ── Role Color Palettes (for shift grids, calendars, templates) ──

/** Light-bg palette: used in Schedule grid and ShiftTemplates list */
export const roleColorsLight = [
  "bg-blue-100 border-blue-300 text-blue-700",
  "bg-emerald-100 border-emerald-300 text-emerald-700",
  "bg-purple-100 border-purple-300 text-purple-700",
  "bg-amber-100 border-amber-300 text-amber-700",
  "bg-rose-100 border-rose-300 text-rose-700",
  "bg-cyan-100 border-cyan-300 text-cyan-700",
  "bg-indigo-100 border-indigo-300 text-indigo-700",
  "bg-orange-100 border-orange-300 text-orange-700",
];

/** Calendar block palette: bg/border/text triples for ShiftCalendar */
export const roleColorsCalendar = [
  { bg: "bg-blue-200", border: "border-blue-400", text: "text-blue-800" },
  { bg: "bg-emerald-200", border: "border-emerald-400", text: "text-emerald-800" },
  { bg: "bg-purple-200", border: "border-purple-400", text: "text-purple-800" },
  { bg: "bg-orange-200", border: "border-orange-400", text: "text-orange-800" },
  { bg: "bg-pink-200", border: "border-pink-400", text: "text-pink-800" },
  { bg: "bg-cyan-200", border: "border-cyan-400", text: "text-cyan-800" },
  { bg: "bg-yellow-200", border: "border-yellow-400", text: "text-yellow-800" },
  { bg: "bg-red-200", border: "border-red-400", text: "text-red-800" },
];

// ── Spinner ──

export const spinner = "border-accent border-t-transparent";

// ── Calendar ring (today) ──

export const calendarToday = "ring-2 ring-accent";

// ── Marketing & auth surface ("The Rota") ───────────────────────
// Deliberately separate from the app tokens above: the public
// surface is restyled first and the app inherits this later.
// No .glass-* class may appear on a page that imports this.

export const marketing = {
  /** Page ground. Applied by the marketing shell, not by body. */
  page: "bg-newsprint text-ink",
  /** Raised surface: form panels, the hero grid, the receipt. */
  surface: "bg-paper",
  /** The dark band — used exactly once per page, on the demo. */
  inverse: "bg-ink text-newsprint",

  text: {
    display: "font-display text-ink",
    body: "font-body text-ink",
    /** Secondary prose. 70% ink keeps AA on newsprint. */
    muted: "font-body text-ink/70",
    /** Eyebrows, captions, table headers. */
    meta: "font-data text-ink/70 uppercase tracking-[0.14em] text-xs",
    /** Figures, times, prices. Always tabular. */
    data: "font-data tabular-nums text-ink",
    clear: "text-clear",
  },

  /** The highlighter. A fill with ink on top — never a text colour. */
  mark: "bg-marker text-ink px-1.5 -mx-0.5 box-decoration-clone",

  rule: {
    /** Standard 1px divider. */
    line: "border-rule",
    /** Section boundary — heavier, structural. */
    heavy: "border-ink/25",
    /** Grid interior lines in the rota and the receipt. */
    grid: "border-rule/70",
  },

  btn: {
    /** The one action per view. */
    primary:
      "inline-flex items-center justify-center font-display uppercase " +
      "tracking-wide px-6 py-3 bg-ink text-newsprint border border-ink " +
      // hover flips to the highlighter; text must go to ink or it drops to
      // ~2.3:1 and becomes unreadable.
      "hover:bg-marker hover:border-marker hover:text-ink transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-ink " +
      "disabled:opacity-40 disabled:pointer-events-none",
    /** Everything else. */
    secondary:
      "inline-flex items-center justify-center font-display uppercase " +
      "tracking-wide px-6 py-3 bg-transparent text-ink border border-ink/40 " +
      "hover:border-ink transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-ink " +
      "disabled:opacity-40 disabled:pointer-events-none",
    /** Inline text link. */
    link:
      "font-body underline underline-offset-4 decoration-rule " +
      "hover:decoration-marker transition-colors " +
      "focus-visible:outline focus-visible:outline-2 " +
      "focus-visible:outline-offset-2 focus-visible:outline-ink",
  },

  input:
    "w-full font-body bg-paper text-ink border border-rule px-3 py-2 " +
    "placeholder:text-ink/40 focus:outline-none focus:border-ink " +
    "focus-visible:outline focus-visible:outline-2 " +
    "focus-visible:outline-offset-1 focus-visible:outline-ink " +
    "transition-colors",

  label: "block font-data text-xs uppercase tracking-[0.14em] text-ink/70 mb-1.5",

  alert: {
    error: "border-s-2 border-marker bg-marker/5 text-ink px-4 py-3 font-body text-sm",
    success: "border-s-2 border-clear bg-clear/5 text-ink px-4 py-3 font-body text-sm",
    info: "border-s-2 border-rule bg-ink/[0.03] text-ink px-4 py-3 font-body text-sm",
  },
};

// ── Convenience re-export ──

export const theme = {
  text,
  bg,
  border,
  action,
  nav,
  tab,
  badge,
  roleColorsLight,
  roleColorsCalendar,
  spinner,
  calendarToday,
  marketing,
};

export default theme;
