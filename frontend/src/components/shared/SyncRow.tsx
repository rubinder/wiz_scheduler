import { text } from "../../theme";

/** Every import row reports a subset of these; a missing key renders as "–". */
export type SyncStats = Partial<
  Record<"created" | "updated" | "skipped" | "archived" | "restored" | "deleted", number>
>;

export default function SyncRow({
  label,
  stats,
}: {
  label: string;
  stats: SyncStats;
}) {
  const cell = (n: number | undefined) => (n === undefined ? "–" : n);
  return (
    <tr>
      <td className={`px-3 py-1 text-sm font-medium ${text.secondary}`}>{label}</td>
      <td className="px-3 py-1 text-sm text-emerald-400">{cell(stats.created)}</td>
      <td className="px-3 py-1 text-sm text-blue-400">{cell(stats.updated ?? stats.skipped)}</td>
      <td className="px-3 py-1 text-sm text-amber-400">{cell(stats.archived)}</td>
      <td className="px-3 py-1 text-sm text-emerald-400">{cell(stats.restored)}</td>
      <td className="px-3 py-1 text-sm text-red-400">{cell(stats.deleted)}</td>
    </tr>
  );
}
