import { useCallback, useState } from "react";

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
        <span className="text-gray-500 text-sm">{String(value ?? "")}</span>
      );
    }

    if (!isEditing) {
      if (col.type === "select" && col.options) {
        const opt = col.options.find((o) => o.value === value);
        return <span className="text-sm">{opt?.label ?? String(value ?? "")}</span>;
      }
      return <span className="text-sm">{String(value ?? "")}</span>;
    }

    if (col.type === "select" && col.options) {
      return (
        <select
          className="w-full border border-gray-300 rounded px-2 py-1 text-sm"
          value={String(value ?? "")}
          onChange={(e) => onChange(col.key, e.target.value)}
        >
          <option value="">-- select --</option>
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
        className="w-full border border-gray-300 rounded px-2 py-1 text-sm"
        value={String(value ?? "")}
        onChange={(e) => onChange(col.key, e.target.value)}
      />
    );
  };

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
              >
                {col.label}
              </th>
            ))}
            <th className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {data.map((row, idx) => {
            const isEditing = editingRow === idx;
            return (
              <tr key={idx} className={isEditing ? "bg-blue-50" : ""}>
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
                      <button
                        onClick={saveEdit}
                        className="text-green-600 hover:text-green-800 text-sm font-medium"
                      >
                        Save
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="text-gray-500 hover:text-gray-700 text-sm font-medium"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={() => startEdit(idx)}
                        className="text-indigo-600 hover:text-indigo-800 text-sm font-medium"
                      >
                        Edit
                      </button>
                      {onDelete && (
                        <button
                          onClick={() => onDelete(idx)}
                          className="text-red-600 hover:text-red-800 text-sm font-medium"
                        >
                          Delete
                        </button>
                      )}
                    </>
                  )}
                </td>
              </tr>
            );
          })}

          {/* Add new row */}
          {showAddRow && (
            <tr className="bg-green-50">
              {columns.map((col) => (
                <td key={col.key} className="px-4 py-2">
                  {col.type === "readonly" ? (
                    <span className="text-gray-400 text-sm italic">auto</span>
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
                  className="text-green-600 hover:text-green-800 text-sm font-medium"
                >
                  Add
                </button>
                <button
                  onClick={() => {
                    setShowAddRow(false);
                    setAddValues({});
                  }}
                  className="text-gray-500 hover:text-gray-700 text-sm font-medium"
                >
                  Cancel
                </button>
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {onCreate && !showAddRow && (
        <div className="mt-3">
          <button
            onClick={() => setShowAddRow(true)}
            className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm"
          >
            + Add Row
          </button>
        </div>
      )}
    </div>
  );
}
