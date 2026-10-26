import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { listLocations } from "../../api/locations";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { badge } from "../../theme";
import { isRestricted, restrictedLocationNames } from "../../utils/permissions";

/**
 * "Restricted to: <locations>" for a regular (location-scoped) manager,
 * rendered once by the layout above every manager page (#149, spec Q10).
 * Renders nothing for admins, employees, and outside /manager.
 */
export default function RestrictedLocationsBadge() {
  const { user, isAdmin, accessibleLocationIds } = useAuth();
  const { t } = useLanguage();
  const { pathname } = useLocation();
  const restricted = isRestricted({ isAdmin, accessibleLocationIds });
  const [names, setNames] = useState<string[] | null>(null);

  useEffect(() => {
    if (!restricted) return;
    let cancelled = false;
    // GET /locations is already scoped to the manager's locations and
    // hides archived ones (spec Q7).
    listLocations()
      .then((locs) => {
        if (!cancelled) {
          setNames(restrictedLocationNames({ isAdmin, accessibleLocationIds }, locs));
        }
      })
      .catch(() => !cancelled && setNames(null));
    return () => {
      cancelled = true;
    };
  }, [restricted, isAdmin, accessibleLocationIds, user?.company_id]);

  if (!restricted || !pathname.startsWith("/manager") || names === null) return null;

  return (
    <div className="mb-4 flex">
      <span
        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${badge.location}`}
        data-testid="restricted-locations-badge"
      >
        {names.length > 0
          ? t.access.restrictedTo.replace("{locations}", names.join(", "))
          : t.access.noLocations}
      </span>
    </div>
  );
}
