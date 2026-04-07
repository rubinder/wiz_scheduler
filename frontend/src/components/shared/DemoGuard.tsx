import { useState } from "react";
import { useAuth } from "../../hooks/useAuth";

interface DemoGuardProps {
  children: React.ReactElement;
  /** Override the default tooltip message */
  message?: string;
}

/**
 * Wraps a button/element and disables it with a tooltip when the user
 * is in the demo company. Passes through unchanged for non-demo users.
 */
export default function DemoGuard({ children, message }: DemoGuardProps) {
  const { user } = useAuth();
  const [showTooltip, setShowTooltip] = useState(false);

  if (!user?.is_demo) {
    return children;
  }

  const tooltipText = message || "Disabled for the Demo Company";

  return (
    <div
      className="relative inline-block"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="opacity-50 pointer-events-none">
        {children}
      </div>
      {showTooltip && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg bg-gray-900 border border-white/10 text-xs text-gray-200 whitespace-nowrap shadow-lg">
          {tooltipText}
          <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-px border-4 border-transparent border-t-gray-900" />
        </div>
      )}
    </div>
  );
}
