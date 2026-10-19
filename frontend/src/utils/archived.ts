// Archived employees, locations and roles (#126) stay in the database for
// history; the manager pages hide them unless "Show archived" is on.
export function isArchived(row: { archived_at?: string | null }): boolean {
  return row.archived_at != null;
}

export function visibleRows<T extends { archived_at?: string | null }>(rows: T[], showArchived: boolean): T[] {
  return showArchived ? rows : rows.filter((r) => !isArchived(r));
}

/**
 * Ids in `heldIds` that name an archived row in `rows`. Archiving a role or
 * location keeps employees' assignments to it (so a restore brings them
 * back); the editors only offer active rows, so these are shown read-only and
 * stay in the saved payload untouched.
 */
export function archivedHeldIds(
  heldIds: string[],
  rows: { id: string; archived_at?: string | null }[]
): string[] {
  const archived = new Set(rows.filter(isArchived).map((r) => r.id));
  return heldIds.filter((id) => archived.has(id));
}

/** `name`, marked "(Archived)" when `row` is archived — for history lookups. */
export function withArchivedMark(
  name: string,
  row: { archived_at?: string | null } | undefined,
  archivedWord: string
): string {
  return row && isArchived(row) ? `${name} (${archivedWord})` : name;
}

/**
 * The rows a picker may offer: the active ones, plus the currently selected
 * row first when it is archived, so the control shows what is actually held
 * instead of silently falling back to the first option.
 */
export function choiceRows<T extends { id: string; archived_at?: string | null }>(
  rows: T[],
  currentId: string | null | undefined
): T[] {
  const current = rows.find((r) => r.id === currentId);
  const active = visibleRows(rows, false);
  return current && isArchived(current) ? [current, ...active] : active;
}
