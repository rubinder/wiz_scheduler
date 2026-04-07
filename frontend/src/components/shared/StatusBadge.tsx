const STATUS_COLORS: Record<string, string> = {
  draft: "bg-yellow-500/15 text-yellow-300 border border-yellow-400/20",
  approved: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/20",
  rejected: "bg-red-500/15 text-red-300 border border-red-400/20",
  ok: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/20",
  CONFLICT: "bg-orange-500/15 text-orange-300 border border-orange-400/20",
  PARSE_ERROR: "bg-red-500/15 text-red-300 border border-red-400/20",
};

interface StatusBadgeProps {
  status: string;
}

export default function StatusBadge({ status }: StatusBadgeProps) {
  const colorClass = STATUS_COLORS[status] ?? "bg-white/10 text-gray-300 border border-white/10";
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${colorClass}`}
    >
      {status}
    </span>
  );
}
