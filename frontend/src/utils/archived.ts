// Archived employees, locations and roles (#126) stay in the database for
// history; the manager pages hide them unless "Show archived" is on.
export function isArchived(row: { archived_at?: string | null }): boolean {
  return row.archived_at != null;
}

export function visibleRows<T extends { archived_at?: string | null }>(rows: T[], showArchived: boolean): T[] {
  return showArchived ? rows : rows.filter((r) => !isArchived(r));
}
