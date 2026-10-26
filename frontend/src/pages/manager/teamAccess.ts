/**
 * Team page access editing (#149, #150), kept free of React so the flow
 * can be tested without a DOM: draft from a manager row, toggle locations,
 * validate, build the PUT /managers/{id} body, and turn a failure into the
 * inline message.
 */
import { ApiError, errorMessage } from "../../api/client";
import type { UpdateManagerBody } from "../../api/managers";
import type { ManagerSummary, ManagerType } from "../../types";

export interface AccessDraft {
  managerType: ManagerType;
  locationIds: string[];
}

/** The editable state of a manager: their type and active assignments. */
export function draftFromManager(m: ManagerSummary): AccessDraft {
  return {
    managerType: m.manager_type,
    locationIds: m.assigned_locations.filter((l) => !l.archived).map((l) => l.id),
  };
}

export function toggleLocation(draft: AccessDraft, locationId: string): AccessDraft {
  const has = draft.locationIds.includes(locationId);
  return {
    ...draft,
    locationIds: has
      ? draft.locationIds.filter((id) => id !== locationId)
      : [...draft.locationIds, locationId],
  };
}

/** A regular manager needs at least one location (server: 400 otherwise). */
export function draftError(draft: AccessDraft): "locationsRequired" | null {
  return draft.managerType === "regular" && draft.locationIds.length === 0
    ? "locationsRequired"
    : null;
}

const sameSet = (a: string[], b: string[]) =>
  a.length === b.length && a.every((id) => b.includes(id));

/**
 * The PUT /managers/{id} body for a draft.
 *
 * - admin: just the type; the server drops every assignment.
 * - regular: the whole location set, except when an already-regular
 *   manager's active set is unchanged. Then it is omitted, because the
 *   server's replace accepts only active locations and would otherwise
 *   drop archived assignments the admin never touched (spec Q7).
 */
export function updateBody(manager: ManagerSummary, draft: AccessDraft): UpdateManagerBody {
  if (draft.managerType === "admin") return { manager_type: "admin" };
  const unchanged =
    manager.manager_type === "regular" &&
    sameSet(draftFromManager(manager).locationIds, draft.locationIds);
  return unchanged
    ? { manager_type: "regular" }
    : { manager_type: "regular", location_ids: [...draft.locationIds] };
}

/** True when the manager has archived locations that a save would drop. */
export function hasArchivedAssignments(m: ManagerSummary): boolean {
  return m.manager_type === "regular" && m.assigned_locations.some((l) => l.archived);
}

/**
 * The inline message for a failed save: the last-admin refusal (409) gets
 * its own translated text; a 400 (bad location set) shows the server's
 * reason.
 */
export function accessErrorMessage(
  err: unknown,
  messages: { lastAdminError: string },
  fallback: string,
): string {
  if (err instanceof ApiError && err.status === 409) return messages.lastAdminError;
  return errorMessage(err, fallback);
}
