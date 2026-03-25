import { useRef, useState } from "react";

interface FormatExample {
  csv: string;
  json: string;
}

interface ImportModalProps {
  title: string;
  format: FormatExample;
  onUpload: (file: File) => Promise<{ created: number; skipped: number; errors: string[] }>;
  onClose: () => void;
}

export default function ImportModal({ title, format, onUpload, onClose }: ImportModalProps) {
  const [tab, setTab] = useState<"csv" | "json">("csv");
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<{ created: number; skipped: number; errors: string[] } | null>(null);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const accept = tab === "csv" ? ".csv" : ".json";

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    setResult(null);
    try {
      const res = await onUpload(file);
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
        </div>

        {/* Tab selector */}
        <div className="px-6 pt-4">
          <div className="flex gap-1 bg-gray-100 rounded-lg p-1 w-fit">
            <button
              onClick={() => setTab("csv")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === "csv" ? "bg-white shadow text-gray-900" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              CSV
            </button>
            <button
              onClick={() => setTab("json")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === "json" ? "bg-white shadow text-gray-900" : "text-gray-500 hover:text-gray-700"
              }`}
            >
              JSON
            </button>
          </div>
        </div>

        {/* Format preview */}
        <div className="px-6 py-4">
          <p className="text-sm text-gray-600 mb-2">Expected format:</p>
          <pre className="bg-gray-50 border border-gray-200 rounded-lg p-4 text-xs text-gray-700 overflow-x-auto whitespace-pre">
            {tab === "csv" ? format.csv : format.json}
          </pre>
        </div>

        {/* Upload area */}
        <div className="px-6 pb-4">
          <input
            ref={fileInputRef}
            type="file"
            accept={accept}
            onChange={handleFileSelect}
            className="hidden"
            id="import-file-input"
          />
          <label
            htmlFor="import-file-input"
            className={`inline-flex items-center gap-2 px-4 py-2 rounded text-sm font-medium cursor-pointer transition-colors ${
              uploading
                ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                : "bg-indigo-600 text-white hover:bg-indigo-700"
            }`}
          >
            {uploading ? "Uploading..." : `Choose ${tab.toUpperCase()} File`}
          </label>
        </div>

        {/* Result / Error */}
        {error && (
          <div className="mx-6 mb-4 p-3 bg-red-50 text-red-700 rounded text-sm">
            {error}
          </div>
        )}
        {result && (
          <div className="mx-6 mb-4 p-3 bg-blue-50 text-blue-800 rounded text-sm">
            <p>Created: {result.created} | Skipped: {result.skipped}</p>
            {result.errors.length > 0 && (
              <ul className="mt-2 list-disc list-inside text-sm">
                {result.errors.map((err, i) => (
                  <li key={i}>{err}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {/* Footer */}
        <div className="flex justify-end px-6 py-4 border-t">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200 text-sm font-medium"
          >
            {result ? "Done" : "Cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}
