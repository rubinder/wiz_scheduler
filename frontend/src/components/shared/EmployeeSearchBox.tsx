import { useEffect, useRef, useState } from "react";
import type { Employee } from "../../types";

interface Props {
  employees: Employee[];
  value: string; // selected employee id
  onChange: (id: string) => void;
  excludeIds?: string[];
  placeholder?: string;
  /** When true, the results list renders inline (pushes content down) instead of overlaying */
  inline?: boolean;
}

export default function EmployeeSearchBox({
  employees,
  value,
  onChange,
  excludeIds = [],
  placeholder = "Search employees...",
  inline = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const selectedName =
    employees.find((e) => e.id === value)?.full_name ?? "";

  // When value changes externally (e.g. editing starts), sync the display
  useEffect(() => {
    if (!open) {
      setQuery(selectedName);
    }
  }, [selectedName, open]);

  const excludeSet = new Set(excludeIds);

  const filtered = employees.filter((e) => {
    if (excludeSet.has(e.id)) return false;
    if (!query) return true;
    return e.full_name.toLowerCase().includes(query.toLowerCase());
  });

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
        // Reset query to selected name if user clicked away without picking
        setQuery(selectedName);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [selectedName]);

  const handleFocus = () => {
    setOpen(true);
    setQuery("");
  };

  const handleSelect = (emp: Employee) => {
    onChange(emp.id);
    setQuery(emp.full_name);
    setOpen(false);
  };

  const handleInputChange = (text: string) => {
    setQuery(text);
    if (!open) setOpen(true);
    // If user clears the box, clear the selection
    if (text === "" && value) {
      onChange("");
    }
  };

  // ~40px per item (name + email) × 3 = 120px
  const listClasses = inline
    ? "mt-1 w-full max-h-[120px] overflow-auto bg-white border rounded shadow text-sm"
    : "absolute z-20 mt-1 w-full max-h-48 overflow-auto bg-white border rounded shadow-lg text-sm";

  return (
    <div ref={containerRef} className={inline ? "" : "relative"}>
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => handleInputChange(e.target.value)}
        onFocus={handleFocus}
        placeholder={placeholder}
        className="w-full border rounded px-2 py-1 text-sm"
        autoComplete="off"
      />
      {open && (
        <ul className={listClasses}>
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-gray-400">No matches</li>
          ) : (
            filtered.map((emp) => (
              <li
                key={emp.id}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => handleSelect(emp)}
                className={`px-3 py-2 cursor-pointer hover:bg-indigo-50 ${
                  emp.id === value
                    ? "bg-indigo-100 text-indigo-800 font-medium"
                    : "text-gray-800"
                }`}
              >
                <div>{emp.full_name}</div>
                {emp.email && (
                  <div className="text-xs text-gray-400">{emp.email}</div>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
