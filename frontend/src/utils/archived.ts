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
