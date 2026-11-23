import { useMemo } from "react";
import type { Flow } from "../../types/reservations";
import { NODE_H, NODE_W, layoutFlow } from "../../utils/conversationFlow";

interface Props {
  flow: Flow;
  /** Box text for a step: its title, or a fallback. */
  label: (stepId: string) => string;
  /** Accessible name of the whole diagram. */
  title: string;
}

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/**
 * A read-only picture of the flow (decision 102): boxes in layers from the
 * start, arrows for every link. Forward links go from a box's bottom to the
 * next box's top; links back up (an "ask again" loop) or across a layer
 * curve round the side.
 */
export default function FlowDiagram({ flow, label, title }: Props) {
  const layout = useMemo(() => layoutFlow(flow), [flow]);
  const pos = new Map(layout.nodes.map((n) => [n.id, n]));
  return (
    <div className="overflow-x-auto" dir="ltr">
      <svg
        role="img"
        aria-label={title}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="text-gray-500"
      >
        <defs>
          <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        {layout.edges.map((e, i) => {
          const a = pos.get(e.from)!;
          const b = pos.get(e.to)!;
          let d: string;
          let lx: number;
          let ly: number;
          if (b.layer > a.layer) {
            const x1 = a.x + NODE_W / 2;
            const y1 = a.y + NODE_H;
            const x2 = b.x + NODE_W / 2;
            const y2 = b.y;
            const my = (y1 + y2) / 2;
            d = `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
            lx = (x1 + x2) / 2;
            ly = my;
          } else {
            const x1 = a.x + NODE_W;
            const y1 = a.y + NODE_H / 2;
            const x2 = b.x + NODE_W;
            const y2 = b.y + NODE_H / 2;
            const bulge = Math.max(x1, x2) + 28;
            d = `M ${x1} ${y1} C ${bulge} ${y1}, ${bulge} ${y2}, ${x2} ${y2}`;
            lx = bulge - 4;
            ly = (y1 + y2) / 2;
          }
          return (
            <g key={i}>
              <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} markerEnd="url(#flow-arrow)" />
              {e.label && (
                <text x={lx} y={ly - 3} textAnchor="middle" fontSize={10} className="fill-gray-600">
                  {truncate(e.label, 24)}
                </text>
              )}
            </g>
          );
        })}
        {layout.nodes.map((n) => (
          <g key={n.id}>
            <rect
              x={n.x}
              y={n.y}
              width={NODE_W}
              height={NODE_H}
              rx={8}
              className={n.id === flow.start ? "fill-emerald-50 stroke-emerald-600" : "fill-white stroke-gray-400"}
              strokeWidth={n.id === flow.start ? 2 : 1}
            />
            <text x={n.x + NODE_W / 2} y={n.y + NODE_H / 2 + 4} textAnchor="middle" fontSize={12} className="fill-gray-800">
              {truncate(label(n.id), 24)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
