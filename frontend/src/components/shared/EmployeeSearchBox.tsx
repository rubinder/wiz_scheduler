import { useEffect, useRef, useState } from "react";
import type { Employee } from "../../types";
import { useLanguage } from "../../i18n/LanguageContext";
import { text } from "../../theme";

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
  const { t } = useLanguage();
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

  // ~40px per item (name + email) x 3 = 120px
  const listClasses = inline
    ? "mt-1 w-full max-h-[120px] overflow-auto bg-cream/95 backdrop-blur-xl border border-sage/25 rounded-lg shadow-2xl text-sm"
    : "absolute z-20 mt-1 w-full max-h-48 overflow-auto bg-cream/95 backdrop-blur-xl border border-sage/25 rounded-lg shadow-2xl text-sm";

  return (
    <div ref={containerRef} className={inline ? "" : "relative"}>
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => handleInputChange(e.target.value)}
        onFocus={handleFocus}
        placeholder={placeholder}
        className="w-full glass-input-sm"
        autoComplete="off"
      />
      {open && (
        <ul className={listClasses}>
          {filtered.length === 0 ? (
            <li className={`px-3 py-2 ${text.muted}`}>{t.search.noMatches}</li>
          ) : (
            filtered.map((emp) => (
              <li
                key={emp.id}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => handleSelect(emp)}
                className={`px-3 py-2 cursor-pointer hover:bg-white/[0.08] ${
                  emp.id === value
                    ? "bg-accent/10 text-accent-dark font-medium"
                    : text.body
                }`}
              >
                <div>{emp.full_name}</div>
                {emp.email && (
                  <div className={`text-xs ${text.muted}`}>{emp.email}</div>
                )}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}
