/** Shown when the server refused a flow save as stale (409 flow_conflict, decision 104). */
export default function FlowConflictNotice({
  message,
  reloadLabel,
  onReload,
}: {
  message: string;
  reloadLabel: string;
  onReload: () => void;
}) {
  return (
    <div className="glass-alert-error flex flex-wrap items-center gap-3" role="alert">
      <span>{message}</span>
      <button type="button" className="glass-btn-secondary" onClick={onReload}>
        {reloadLabel}
      </button>
    </div>
  );
}
