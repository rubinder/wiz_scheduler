import { useRef, useState } from "react";
import { useLanguage } from "../../i18n/LanguageContext";

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
  const { t } = useLanguage();
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
    <div className="glass-modal-overlay">
      <div className="glass-modal w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
          <h2 className="text-lg font-semibold text-gray-200">{title}</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl leading-none">&times;</button>
        </div>

        {/* Tab selector */}
        <div className="px-6 pt-4">
          <div className="flex gap-1 bg-white/[0.05] rounded-lg p-1 w-fit">
            <button
              onClick={() => setTab("csv")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === "csv" ? "bg-white/[0.1] shadow text-white" : "text-gray-400 hover:text-gray-200"
              }`}
            >
              {t.importModal.csv}
            </button>
            <button
              onClick={() => setTab("json")}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                tab === "json" ? "bg-white/[0.1] shadow text-white" : "text-gray-400 hover:text-gray-200"
              }`}
            >
              {t.importModal.json}
            </button>
          </div>
        </div>

        {/* Format preview */}
        <div className="px-6 py-4">
          <p className="text-sm text-gray-400 mb-2">{t.importModal.expectedFormat}</p>
          <pre className="bg-white/[0.04] border border-white/[0.08] rounded-lg p-4 text-xs text-gray-400 overflow-x-auto whitespace-pre">
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
                ? "bg-white/10 text-gray-500 cursor-not-allowed"
                : "glass-btn-primary"
            }`}
          >
            {uploading ? t.importModal.uploading : `${t.importModal.chooseFile} ${tab.toUpperCase()} ${t.importModal.file}`}
          </label>
        </div>

        {/* Result / Error */}
        {error && (
          <div className="mx-6 mb-4 glass-alert-error">
            {error}
          </div>
        )}
        {result && (
          <div className="mx-6 mb-4 p-3 bg-blue-500/10 border border-blue-400/20 text-blue-300 rounded text-sm">
            <p>{t.importModal.createdCount} {result.created} | {t.importModal.skippedCount} {result.skipped}</p>
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
        <div className="flex justify-end px-6 py-4 border-t border-white/[0.08]">
          <button
            onClick={onClose}
            className="glass-btn-secondary px-4 py-2 text-sm font-medium"
          >
            {result ? t.common.done : t.common.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
