import React, { useEffect, useState } from "react";

function getNextMonday(): string {
  const now = new Date();
  const day = now.getDay();
  const diff = day === 0 ? 1 : 8 - day;
  const monday = new Date(now);
  monday.setDate(now.getDate() + diff);
  return monday.toISOString().split("T")[0];
}

interface WeekPickerProps {
  value?: string;
  onChange: (isoDate: string) => void;
}

export default function WeekPicker({ value, onChange }: WeekPickerProps) {
  const [date, setDate] = useState(value || getNextMonday());

  useEffect(() => {
    if (value !== undefined) {
      setDate(value);
    }
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newDate = e.target.value;
    setDate(newDate);
    onChange(newDate);
  };

  return (
    <div className="flex items-center gap-3">
      <label className="text-sm font-medium text-gray-700">
        Week starting:
      </label>
      <input
        type="date"
        value={date}
        onChange={handleChange}
        className="border border-gray-300 rounded px-3 py-2 text-sm"
      />
    </div>
  );
}
