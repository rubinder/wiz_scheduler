import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import { getCompany } from "../../api/company";
import { listEmployees } from "../../api/employees";
import { getOpenShiftCount, OPEN_SHIFTS_CHANGED } from "../../api/openShifts";
import { PREFERENCE_SUBMISSIONS_CHANGED } from "../../api/preferenceSubmissions";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, nav } from "../../theme";
import {
  isGroup,
  visibleEmployeeNav,
  visibleManagerNav,
  type NavGroup,
} from "./navItems";

interface SidebarProps {
  /** Below md the sidebar is an off-canvas drawer (#215); whether it shows. */
  open: boolean;
  onClose: () => void;
}

export default function Sidebar({ open, onClose }: SidebarProps) {
  const { user, isAdmin } = useAuth();
  const { t } = useLanguage();
  const { pathname } = useLocation();

  // Navigating closes the drawer, as does Escape.
  useEffect(() => {
    onClose();
    // onClose is a fresh closure each render; only a route change should close.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  const isManager = user?.user_role === "manager";
  const [hasEmployees, setHasEmployees] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!isManager) return;
    listEmployees()
      .then((emps) => setHasEmployees(emps.length > 0))
      .catch(() => setHasEmployees(false));
  }, [isManager]);

  // Employee preference submissions (#189) are a per-company switch; both
  // roles can read it from GET /company. Refetched when an admin flips it.
  const [prefSubmissions, setPrefSubmissions] = useState(false);
  const userId = user?.id;
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    const load = () =>
      getCompany()
        .then((c) => !cancelled && setPrefSubmissions(!!c.employee_preference_submissions_enabled))
        .catch(() => !cancelled && setPrefSubmissions(false));
    load();
    window.addEventListener(PREFERENCE_SUBMISSIONS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(PREFERENCE_SUBMISSIONS_CHANGED, load);
    };
  }, [userId]);

  const [openShiftCount, setOpenShiftCount] = useState(0);

  // Open approved shifts in the next 14 days (#174). Refetched on every
  // navigation and whenever something announces a change.
  useEffect(() => {
    if (!isManager) return;
    let cancelled = false;
    const load = () =>
      getOpenShiftCount()
        .then((n) => !cancelled && setOpenShiftCount(n))
        .catch(() => !cancelled && setOpenShiftCount(0));
    load();
    window.addEventListener(OPEN_SHIFTS_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(OPEN_SHIFTS_CHANGED, load);
    };
  }, [isManager, pathname]);

  const badge = (count: number) =>
    count > 0 ? (
      <span className="ms-2 inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-amber-500 text-white text-[11px] font-semibold">
        {count}
      </span>
    ) : null;

  const entries = useMemo(
    () =>
      isManager
        ? visibleManagerNav({ isAdmin }, hasEmployees, prefSubmissions)
        : visibleEmployeeNav(prefSubmissions),
    [isManager, isAdmin, hasEmployees, prefSubmissions],
  );

  // Auto-expand whichever group owns the active route, so a deep link never
  // lands the user inside a collapsed tree. Only ever opens a group — it must
  // not re-close one the user opened by hand.
  useEffect(() => {
    const owner = entries.find(
      (e) => isGroup(e) && e.children.some((c) => pathname === c.to),
    );
    if (owner) {
      setOpenGroups((prev) => ({ ...prev, [(owner as NavGroup).labelKey]: true }));
    }
  }, [pathname, entries]);

  const label = (key: string) => t.nav[key as keyof typeof t.nav];

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `block px-3 py-2 rounded-lg text-sm font-medium transition-all ${
      isActive ? nav.active : nav.inactive
    }`;

  // md and up: a static column. Below md: a drawer pinned to the inline
  // start edge (left in LTR, right in RTL), slid off-screen when closed.
  const drawer = open
    ? "translate-x-0"
    : "-translate-x-full rtl:translate-x-full";
  return (
    <>
      {/* Portaled: see layoutOverlays.test.ts. */}
      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-30 bg-black/50 md:hidden"
            aria-hidden="true"
            onClick={onClose}
          />,
          document.body,
        )}
    <aside
      id="app-sidebar"
      className={`w-64 shrink-0 ${bg.sidebar} border-e ${border.default} ${text.primary} flex flex-col
        fixed inset-y-0 start-0 z-40 overflow-y-auto max-md:bg-white/95 transition-transform duration-200 ${drawer}
        md:static md:z-auto md:min-h-screen md:translate-x-0 md:rtl:translate-x-0 md:transition-none`}
    >
      <div className={`p-5 border-b ${border.default} flex items-center justify-between gap-2`}>
        <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">{t.common.appName} <img src="/favicon.svg" alt="" className="w-7 h-7" /></h1>
        <button
          type="button"
          onClick={onClose}
          aria-label={t.common.close}
          className={`md:hidden p-1 rounded ${text.muted} hover:opacity-80`}
        >
          <span aria-hidden="true" className="text-xl leading-none">×</span>
        </button>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {entries.map((entry) => {
          if (!isGroup(entry)) {
            return (
              <NavLink key={entry.to} to={entry.to} className={linkClass}>
                {label(entry.labelKey)}
              </NavLink>
            );
          }

          const children = entry.children;

          const isOpen = openGroups[entry.labelKey] ?? false;
          return (
            <div key={entry.labelKey}>
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() =>
                  setOpenGroups((prev) => ({ ...prev, [entry.labelKey]: !isOpen }))
                }
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-all ${nav.inactive}`}
              >
                <span>
                  {label(entry.labelKey)}
                  {!isOpen &&
                    entry.children.some((c) => c.to === "/manager/approved-schedules") &&
                    badge(openShiftCount)}
                </span>
                <span aria-hidden="true" className={`text-xs ${isOpen ? "" : "rtl:-scale-x-100"}`}>{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen && (
                <div className={`ms-3 ps-2 border-s ${border.default} space-y-1 mt-1`}>
                  {children.map((child) => (
                    <NavLink key={child.to} to={child.to} className={linkClass}>
                      {label(child.labelKey)}
                      {child.to === "/manager/approved-schedules" && badge(openShiftCount)}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      {user && (
        <div className={`p-4 border-t ${border.default} text-sm ${text.muted}`}>
          {user.full_name ?? user.email}
        </div>
      )}
    </aside>
    </>
  );
}
