import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Role } from "../../types";
import { roleColorsCalendar } from "../../theme";

const DEFAULT_DAYS = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];
const DAY_SHORT_MAP: Record<string, string> = {
  Monday: "MON",
  Tuesday: "TUE",
  Wednesday: "WED",
  Thursday: "THU",
  Friday: "FRI",
  Saturday: "SAT",
  Sunday: "SUN",
  Weekday: "WKDY",
  Weekend: "WKND",
};
const START_HOUR = 0;
const END_HOUR = 24; // 12 AM next day
const HOURS = Array.from(
  { length: END_HOUR - START_HOUR },
  (_, i) => START_HOUR + i
);

// Each shift block in the schedule
interface ShiftBlock {
  id: string;
  day: string;
  role_id: string;
  role_name: string;
  headcount: number;
  start_time: string; // "HH:MM"
  end_time: string; // "HH:MM"
}

interface Props {
  roles: Role[];
  value: ShiftBlock[];
  onChange: (blocks: ShiftBlock[]) => void;
  days?: string[];
}

function formatHour(hour: number): string {
  if (hour === 0 || hour === 24) return "12 AM";
  if (hour === 12) return "12 PM";
  if (hour < 12) return `${hour} AM`;
  return `${hour - 12} PM`;
}

function timeToMinutes(time: string): number {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + m;
}

// Color palette for roles
const ROLE_COLORS = roleColorsCalendar;

let _nextId = 1;
function nextId(): string {
  return `block-${_nextId++}`;
}

export default function ShiftCalendar({ roles, value, onChange, days: daysProp }: Props) {
  const DAYS = daysProp ?? DEFAULT_DAYS;
  const ROW_HEIGHT = 40; // px per hour
  const HEADER_HEIGHT = 72;

  const roleColorMap = useMemo(() => {
    const map = new Map<string, (typeof ROLE_COLORS)[0]>();
    roles.forEach((r, i) => map.set(r.id, ROLE_COLORS[i % ROLE_COLORS.length]));
    return map;
  }, [roles]);

  // Drag state for creating new blocks
  const [dragging, setDragging] = useState<{
    dayIdx: number;
    roleIdx: number;
    startHour: number;
    currentHour: number;
  } | null>(null);

  const gridRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const [measuredHeaderHeight, setMeasuredHeaderHeight] = useState(HEADER_HEIGHT);

  useEffect(() => {
    if (headerRef.current) {
      const h = headerRef.current.getBoundingClientRect().height;
      if (h > 0) setMeasuredHeaderHeight(h);
    }
  }, [roles, DAYS]);

  // Editing modal
  const [editBlock, setEditBlock] = useState<ShiftBlock | null>(null);
  const [editHeadcount, setEditHeadcount] = useState(1);
  const [editStart, setEditStart] = useState("09:00");
  const [editEnd, setEditEnd] = useState("17:00");

  const getHourFromY = useCallback(
    (clientY: number): number => {
      if (!gridRef.current) return START_HOUR;
      const rect = gridRef.current.getBoundingClientRect();
      const scrollTop = gridRef.current.scrollTop;
      const relY = clientY - rect.top + scrollTop - measuredHeaderHeight;
      const hour = Math.floor(relY / ROW_HEIGHT) + START_HOUR;
      return Math.max(START_HOUR, Math.min(END_HOUR - 1, hour));
    },
    [ROW_HEIGHT, measuredHeaderHeight]
  );

  const handleMouseDown = (
    e: React.MouseEvent,
    dayIdx: number,
    roleIdx: number
  ) => {
    if (roles.length === 0) return;
    e.preventDefault();
    const hour = getHourFromY(e.clientY);
    setDragging({ dayIdx, roleIdx, startHour: hour, currentHour: hour });
  };

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return;
      const hour = getHourFromY(e.clientY);
      setDragging((d) => (d ? { ...d, currentHour: hour } : null));
    },
    [dragging, getHourFromY]
  );

  const handleMouseUp = useCallback(() => {
    if (!dragging) return;
    const { dayIdx, roleIdx, startHour, currentHour } = dragging;
    const topHour = Math.min(startHour, currentHour);
    const bottomHour = Math.max(startHour, currentHour) + 1;
    const role = roles[roleIdx];
    if (role) {
      const newBlock: ShiftBlock = {
        id: nextId(),
        day: DAYS[dayIdx],
        role_id: role.id,
        role_name: role.name,
        headcount: 1,
        start_time: `${topHour.toString().padStart(2, "0")}:00`,
        end_time: `${bottomHour.toString().padStart(2, "0")}:00`,
      };
      // Open edit modal immediately
      setEditBlock(newBlock);
      setEditHeadcount(1);
      setEditStart(newBlock.start_time);
      setEditEnd(newBlock.end_time);
    }
    setDragging(null);
  }, [dragging, roles, onChange, value]);

  const saveEditBlock = () => {
    if (!editBlock) return;
    const updated: ShiftBlock = {
      ...editBlock,
      headcount: editHeadcount,
      start_time: editStart,
      end_time: editEnd,
    };
    const existing = value.find((b) => b.id === updated.id);
    if (existing) {
      onChange(value.map((b) => (b.id === updated.id ? updated : b)));
    } else {
      onChange([...value, updated]);
    }
    setEditBlock(null);
  };

  const deleteEditBlock = () => {
    if (!editBlock) return;
    onChange(value.filter((b) => b.id !== editBlock.id));
    setEditBlock(null);
  };

  const openBlockEditor = (block: ShiftBlock) => {
    setEditBlock(block);
    setEditHeadcount(block.headcount);
    setEditStart(block.start_time);
    setEditEnd(block.end_time);
  };

  // Total width: time gutter (50px) + 7 days
  // Each day is subdivided by the number of roles (or 1 if no roles)
  const roleCount = Math.max(roles.length, 1);
  const ROLE_COL_WIDTH = 48;
  const DAY_WIDTH = roleCount * ROLE_COL_WIDTH;
  const GUTTER_WIDTH = 56;
  const totalWidth = GUTTER_WIDTH + DAYS.length * DAY_WIDTH;
  const totalHeight = measuredHeaderHeight + HOURS.length * ROW_HEIGHT;

  return (
    <div className="relative">
      <div
        ref={gridRef}
        className="relative overflow-auto border border-gray-300 rounded-lg bg-white select-none"
        style={{ maxHeight: 600 }}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <div style={{ width: totalWidth, height: totalHeight, position: "relative" }}>
          {/* Day headers */}
          <div
            ref={headerRef}
            className="sticky top-0 z-20 bg-white border-b border-gray-300"
            style={{ minHeight: HEADER_HEIGHT, display: "flex" }}
          >
            <div
              style={{ width: GUTTER_WIDTH, minWidth: GUTTER_WIDTH }}
              className="border-e border-gray-200"
            />
            {DAYS.map((day) => (
              <div
                key={day}
                style={{ width: DAY_WIDTH }}
                className="border-e border-gray-200 text-center"
              >
                <div className="text-xs font-medium text-gray-500 pt-1">
                  {DAY_SHORT_MAP[day] ?? day.slice(0, 3).toUpperCase()}
                </div>
                <div className="text-lg font-semibold text-gray-800">
                  {day.length <= 7 ? day : day.slice(0, 3)}
                </div>
                {/* Role sub-headers */}
                {roles.length > 1 && (
                  <div className="flex border-t border-gray-100">
                    {roles.map((role) => {
                      const color = roleColorMap.get(role.id);
                      return (
                        <div
                          key={role.id}
                          style={{ width: ROLE_COL_WIDTH }}
                          className={`text-[8px] leading-[1.1] px-0.5 ${color?.text ?? "text-gray-500"} group relative break-words overflow-hidden`}
                        >
                          {role.name}
                          <div className="hidden group-hover:block absolute left-1/2 -translate-x-1/2 top-full mt-1 z-30 bg-gray-900 text-white text-xs rounded px-2 py-1 whitespace-nowrap shadow-lg pointer-events-none">
                            {role.name}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Time grid rows */}
          {HOURS.map((hour, hi) => (
            <div
              key={hour}
              className="absolute flex"
              style={{
                top: measuredHeaderHeight + hi * ROW_HEIGHT,
                height: ROW_HEIGHT,
                width: totalWidth,
              }}
            >
              {/* Time gutter */}
              <div
                style={{ width: GUTTER_WIDTH, minWidth: GUTTER_WIDTH }}
                className="text-xs text-gray-500 pe-2 text-end pt-0 border-e border-gray-200 leading-none"
              >
                {formatHour(hour)}
              </div>
              {/* Day cells */}
              {DAYS.map((_, dayIdx) => (
                <div
                  key={dayIdx}
                  style={{ width: DAY_WIDTH }}
                  className="border-e border-gray-100 border-b border-b-gray-100 flex"
                >
                  {roles.map((role, roleIdx) => (
                    <div
                      key={role.id}
                      style={{ width: ROLE_COL_WIDTH }}
                      className="border-e border-gray-50 cursor-crosshair"
                      onMouseDown={(e) => handleMouseDown(e, dayIdx, roleIdx)}
                    />
                  ))}
                  {roles.length === 0 && (
                    <div
                      style={{ width: DAY_WIDTH }}
                      className="cursor-not-allowed"
                    />
                  )}
                </div>
              ))}
            </div>
          ))}

          {/* Render existing shift blocks */}
          {value.map((block) => {
            const dayIdx = DAYS.indexOf(block.day);
            if (dayIdx === -1) return null;
            const roleIdx = roles.findIndex((r) => r.id === block.role_id);
            if (roleIdx === -1) return null;

            const startMin = timeToMinutes(block.start_time);
            const endMin = timeToMinutes(block.end_time);
            const topPx =
              measuredHeaderHeight +
              ((startMin - START_HOUR * 60) / 60) * ROW_HEIGHT;
            const heightPx = ((endMin - startMin) / 60) * ROW_HEIGHT;
            const leftPx =
              GUTTER_WIDTH + dayIdx * DAY_WIDTH + roleIdx * ROLE_COL_WIDTH;
            const color = roleColorMap.get(block.role_id) ?? ROLE_COLORS[0];

            return (
              <div
                key={block.id}
                className={`absolute rounded-sm border ${color.bg} ${color.border} ${color.text} cursor-pointer overflow-hidden z-10`}
                style={{
                  top: topPx + 1,
                  left: leftPx + 1,
                  width: ROLE_COL_WIDTH - 2,
                  height: Math.max(heightPx - 2, 12),
                }}
                onClick={() => openBlockEditor(block)}
                title={`${block.role_name} x${block.headcount}\n${block.start_time}-${block.end_time}`}
              >
                <div className="px-1 py-0.5 text-[10px] font-medium leading-tight">
                  <div className="truncate">{block.role_name}</div>
                  <div>x{block.headcount}</div>
                </div>
              </div>
            );
          })}

          {/* Drag preview */}
          {dragging && (
            <DragPreview
              dragging={dragging}
              roles={roles}
              roleColorMap={roleColorMap}
              headerHeight={measuredHeaderHeight}
              rowHeight={ROW_HEIGHT}
              gutterWidth={GUTTER_WIDTH}
              dayWidth={DAY_WIDTH}
              roleColWidth={ROLE_COL_WIDTH}
              startHourOffset={START_HOUR}
            />
          )}
        </div>
      </div>

      {/* Edit modal */}
      {editBlock && (
        <div className="fixed inset-0 bg-black/30 z-50 flex items-center justify-center">
          <div className="bg-white rounded-lg shadow-xl p-6 w-80 space-y-4">
            <h3 className="text-lg font-semibold">
              {value.find((b) => b.id === editBlock.id) ? "Edit" : "New"} Shift
              Block
            </h3>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Role
              </label>
              <input
                type="text"
                disabled
                value={editBlock.role_name}
                className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-gray-50"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Day
              </label>
              <input
                type="text"
                disabled
                value={editBlock.day}
                className="w-full border border-gray-200 rounded px-3 py-2 text-sm bg-gray-50"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Start
                </label>
                <input
                  type="time"
                  value={editStart}
                  onChange={(e) => setEditStart(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  End
                </label>
                <input
                  type="time"
                  value={editEnd}
                  onChange={(e) => setEditEnd(e.target.value)}
                  className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
                />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Headcount
              </label>
              <input
                type="number"
                min={1}
                max={50}
                value={editHeadcount}
                onChange={(e) => setEditHeadcount(Number(e.target.value))}
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm"
              />
            </div>
            <div className="flex justify-between">
              <button
                onClick={deleteEditBlock}
                className="px-3 py-2 text-red-600 hover:text-red-800 text-sm font-medium"
              >
                Delete
              </button>
              <div className="flex gap-2">
                <button
                  onClick={() => setEditBlock(null)}
                  className="px-4 py-2 bg-gray-200 text-gray-700 rounded hover:bg-gray-300 text-sm"
                >
                  Cancel
                </button>
                <button
                  onClick={saveEditBlock}
                  className="px-4 py-2 bg-accent text-white rounded hover:bg-accent-light text-sm"
                >
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DragPreview({
  dragging,
  roles,
  roleColorMap,
  headerHeight,
  rowHeight,
  gutterWidth,
  dayWidth,
  roleColWidth,
  startHourOffset,
}: {
  dragging: {
    dayIdx: number;
    roleIdx: number;
    startHour: number;
    currentHour: number;
  };
  roles: Role[];
  roleColorMap: Map<string, (typeof ROLE_COLORS)[0]>;
  headerHeight: number;
  rowHeight: number;
  gutterWidth: number;
  dayWidth: number;
  roleColWidth: number;
  startHourOffset: number;
}) {
  const topHour = Math.min(dragging.startHour, dragging.currentHour);
  const bottomHour = Math.max(dragging.startHour, dragging.currentHour) + 1;
  const topPx = headerHeight + (topHour - startHourOffset) * rowHeight;
  const heightPx = (bottomHour - topHour) * rowHeight;
  const leftPx =
    gutterWidth + dragging.dayIdx * dayWidth + dragging.roleIdx * roleColWidth;
  const role = roles[dragging.roleIdx];
  const color = role
    ? roleColorMap.get(role.id) ?? ROLE_COLORS[0]
    : ROLE_COLORS[0];

  return (
    <div
      className={`absolute rounded-sm border-2 border-dashed ${color.border} ${color.bg} opacity-60 z-20 pointer-events-none`}
      style={{
        top: topPx,
        left: leftPx + 1,
        width: roleColWidth - 2,
        height: heightPx,
      }}
    />
  );
}

export const WEEKDAY_WEEKEND_DAYS = ["Weekday", "Weekend"];
export const ALL_DAYS = DEFAULT_DAYS;
export const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];
export const WEEKEND_NAMES = ["Saturday", "Sunday"];

// Utility: convert ShiftBlock[] to weekly_schedule JSON format
export function blocksToScheduleJson(
  blocks: { day: string; role_name: string; role_id: string; headcount: number; start_time: string; end_time: string }[]
): Record<string, unknown>[] {
  return blocks.map((b) => ({
    day: b.day,
    role_name: b.role_name,
    role_id: b.role_id,
    headcount: b.headcount,
    start_time: b.start_time,
    end_time: b.end_time,
  }));
}

// Utility: convert weekly_schedule JSON to ShiftBlock[]
export function scheduleJsonToBlocks(
  json: Record<string, unknown>[]
): { id: string; day: string; role_id: string; role_name: string; headcount: number; start_time: string; end_time: string }[] {
  return json.map((entry) => ({
    id: nextId(),
    day: (entry.day as string) ?? "",
    role_id: (entry.role_id as string) ?? "",
    role_name: (entry.role_name as string) ?? "",
    headcount: (entry.headcount as number) ?? 1,
    start_time: (entry.start_time as string) ?? "09:00",
    end_time: (entry.end_time as string) ?? "17:00",
  }));
}
