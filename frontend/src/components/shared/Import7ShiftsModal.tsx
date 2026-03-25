import { useState } from "react";
import { importFrom7Shifts, type ImportResult } from "../../api/import7shifts";

interface Props {
  onClose: () => void;
}

function SyncRow({
  label,
  stats,
}: {
  label: string;
  stats: { created: number; updated: number; deleted: number };
}) {
  return (
    <tr>
      <td className="px-3 py-1 text-sm font-medium text-gray-700">{label}</td>
      <td className="px-3 py-1 text-sm text-green-700">{stats.created}</td>
      <td className="px-3 py-1 text-sm text-blue-700">{stats.updated}</td>
      <td className="px-3 py-1 text-sm text-red-700">{stats.deleted}</td>
    </tr>
  );
}

export default function Import7ShiftsModal({ onClose }: Props) {
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  const handleImport = async () => {
    if (!token.trim()) {
      setError("Please enter your 7shifts access token");
      return;
    }
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await importFrom7Shifts(token.trim());
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Import failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">Import from 7shifts</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
          >
            &times;
          </button>
        </div>

        <div className="px-6 py-4 space-y-4">
          {!result && (
            <>
              <p className="text-sm text-gray-600">
                Enter your 7shifts access token to import companies, locations,
                departments, roles, employees, and user assignments. You can
                generate an access token in your 7shifts account under{" "}
                <span className="font-medium">
                  Company Settings &gt; Developer Tools
                </span>
                .
              </p>
              <div>
                <label
                  htmlFor="seven-token"
                  className="block text-sm font-medium text-gray-700 mb-1"
                >
                  Access Token
                </label>
                <input
                  id="seven-token"
                  type="password"
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500"
                  placeholder="Paste your 7shifts access token"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  disabled={loading}
                />
              </div>
            </>
          )}

          {error && (
            <div className="p-3 bg-red-50 text-red-700 rounded text-sm">
              {error}
            </div>
          )}

          {loading && (
            <div className="flex items-center gap-3 py-4">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-orange-600 border-t-transparent" />
              <span className="text-sm text-gray-600">
                Importing data from 7shifts... This may take a moment.
              </span>
            </div>
          )}

          {result && (
            <div className="space-y-3">
              <div className="p-3 bg-green-50 text-green-800 rounded text-sm font-medium">
                Import completed successfully
              </div>
              <table className="w-full text-left">
                <thead>
                  <tr className="text-xs text-gray-500 uppercase">
                    <th className="px-3 py-1">Entity</th>
                    <th className="px-3 py-1">Created</th>
                    <th className="px-3 py-1">Updated</th>
                    <th className="px-3 py-1">Deleted</th>
                  </tr>
                </thead>
                <tbody>
                  <SyncRow label="Companies" stats={result.companies} />
                  <SyncRow label="Locations" stats={result.locations} />
                  <SyncRow label="Departments" stats={result.departments} />
                  <SyncRow label="Roles" stats={result.roles} />
                  <SyncRow label="Employees" stats={result.employees} />
                  <SyncRow
                    label="User Assignments"
                    stats={result.user_assignments}
                  />
                </tbody>
              </table>

              {result.errors.length > 0 && (
                <div className="p-3 bg-yellow-50 rounded text-sm">
                  <p className="font-medium text-yellow-800 mb-1">Warnings:</p>
                  <ul className="list-disc list-inside text-yellow-700 space-y-0.5">
                    {result.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 px-6 py-4 border-t bg-gray-50 rounded-b-lg">
          {result ? (
            <button
              onClick={onClose}
              className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm font-medium"
            >
              Done
            </button>
          ) : (
            <>
              <button
                onClick={onClose}
                className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm font-medium"
                disabled={loading}
              >
                Cancel
              </button>
              <button
                onClick={handleImport}
                className="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 text-sm font-medium disabled:opacity-50"
                disabled={loading || !token.trim()}
              >
                {loading ? "Importing..." : "Import"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
