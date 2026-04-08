import { useCallback, useState } from "react";
import { useLanguage } from "../../i18n/LanguageContext";
import DemoGuard from "./DemoGuard";
import { text, bg, border, action } from "../../theme";

export interface Column {
  key: string;
  label: string;
  type: "text" | "select" | "date" | "readonly";
  options?: { value: string; label: string }[];
}

interface DataTableProps {
  columns: Column[];
  data: Record<string, unknown>[];
  onSave: (rowIndex: number, updatedRow: Record<string, unknown>) => void;
  onDelete?: (rowIndex: number) => void;
  onCreate?: (newRow: Record<string, unknown>) => void;
}

export default function DataTable({
  columns,
  data,
  onSave,
  onDelete,
  onCreate,
}: DataTableProps) {
  const { t } = useLanguage();
  const [editingRow, setEditingRow] = useState<number | null>(null);
  const [editValues, setEditValues] = useState<Record<string, unknown>>({});
  const [showAddRow, setShowAddRow] = useState(false);
  const [addValues, setAddValues] = useState<Record<string, unknown>>({});

  const startEdit = useCallback(
    (idx: number) => {
      setEditingRow(idx);
      setEditValues({ ...data[idx] });
    },
    [data]
  );

  const cancelEdit = useCallback(() => {
    setEditingRow(null);
    setEditValues({});
  }, []);

  const saveEdit = useCallback(() => {
    if (editingRow !== null) {
      onSave(editingRow, editValues);
      setEditingRow(null);
      setEditValues({});
    }
  }, [editingRow, editValues, onSave]);

  const handleAdd = useCallback(() => {
    if (onCreate) {
      onCreate(addValues);
      setAddValues({});
      setShowAddRow(false);
    }
  }, [addValues, onCreate]);

  const renderCell = (
    col: Column,
    value: unknown,
    isEditing: boolean,
    onChange: (key: string, val: unknown) => void
  ) => {
    if (col.type === "readonly") {
      return (
        <span className={`${text.muted} text-sm`}>{String(value ?? "")}</span>
      );
    }

    if (!isEditing) {
      if (col.type === "select" && col.options) {
        const opt = col.options.find((o) => o.value === value);
        return <span className={`text-sm ${text.body}`}>{opt?.label ?? String(value ?? "")}</span>;
      }
      return <span className={`text-sm ${text.body}`}>{String(value ?? "")}</span>;
    }

    if (col.type === "select" && col.options) {
      return (
        <select
          className="w-full glass-input-sm"
          value={String(value ?? "")}
          onChange={(e) => onChange(col.key, e.target.value)}
        >
          <option value="">{t.common.select}</option>
          {col.options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      );
    }

    return (
      <input
        type={col.type === "date" ? "date" : "text"}
        className="w-full glass-input-sm"
        value={String(value ?? "")}
        onChange={(e) => onChange(col.key, e.target.value)}
      />
    );
  };

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full glass-table">
        <thead className={bg.tableHeader}>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className={`px-4 py-3 text-left text-xs font-medium ${text.muted} uppercase tracking-wider`}
              >
                {col.label}
              </th>
            ))}
            <th className={`px-4 py-3 text-right text-xs font-medium ${text.muted} uppercase tracking-wider`}>
              {t.common.actions}
            </th>
          </tr>
        </thead>
        <tbody className={`divide-y ${border.divider}`}>
          {data.map((row, idx) => {
            const isEditing = editingRow === idx;
            return (
              <tr key={idx} className={isEditing ? bg.editingRow : ""}>
                {columns.map((col) => (
                  <td key={col.key} className="px-4 py-2">
                    {renderCell(
                      col,
                      isEditing ? editValues[col.key] : row[col.key],
                      isEditing,
                      (key, val) =>
                        setEditValues((prev) => ({ ...prev, [key]: val }))
                    )}
                  </td>
                ))}
                <td className="px-4 py-2 text-right space-x-2">
                  {isEditing ? (
                    <>
                      <DemoGuard>
                        <button
                          onClick={saveEdit}
                          className={action.save}
                        >
                          {t.common.save}
                        </button>
                      </DemoGuard>
                      <button
                        onClick={cancelEdit}
                        className={action.cancel}
                      >
                        {t.common.cancel}
                      </button>
                    </>
                  ) : (
                    <>
                      <DemoGuard>
                        <button
                          onClick={() => startEdit(idx)}
                          className={action.edit}
                        >
                          {t.common.edit}
                        </button>
                      </DemoGuard>
                      {onDelete && (
                        <DemoGuard>
                          <button
                            onClick={() => onDelete(idx)}
                            className={action.delete}
                          >
                            {t.common.delete}
                          </button>
                        </DemoGuard>
                      )}
                    </>
                  )}
                </td>
              </tr>
            );
          })}

          {/* Add new row */}
          {showAddRow && (
            <tr className={bg.addRow}>
              {columns.map((col) => (
                <td key={col.key} className="px-4 py-2">
                  {col.type === "readonly" ? (
                    <span className={`${text.muted} text-sm italic`}>{t.common.auto}</span>
                  ) : (
                    renderCell(col, addValues[col.key], true, (key, val) =>
                      setAddValues((prev) => ({ ...prev, [key]: val }))
                    )
                  )}
                </td>
              ))}
              <td className="px-4 py-2 text-right space-x-2">
                <button
                  onClick={handleAdd}
                  className={action.save}
                >
                  {t.common.add}
                </button>
                <button
                  onClick={() => {
                    setShowAddRow(false);
                    setAddValues({});
                  }}
                  className={action.cancel}
                >
                  {t.common.cancel}
                </button>
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {onCreate && !showAddRow && (
        <div className="mt-3">
          <DemoGuard>
            <button
              onClick={() => setShowAddRow(true)}
              className="glass-btn-primary px-4 py-2 text-sm"
            >
              {t.dataTable.addRow}
            </button>
          </DemoGuard>
        </div>
      )}
    </div>
  );
}
