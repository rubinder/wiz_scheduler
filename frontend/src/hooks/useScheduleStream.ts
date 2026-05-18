import { useCallback, useRef, useState } from "react";
import type { LocationResult } from "../types";

export class ScheduleLockedError extends Error {
  constructor(
    public readonly lockedBy: string,
    public readonly expiresAt: Date,
  ) {
    super(`Schedule locked by ${lockedBy} until ${expiresAt.toISOString()}`);
    this.name = "ScheduleLockedError";
  }
}

interface GenerateOptions {
  useLocal?: boolean;
  strategy?: "random" | "rotation" | "rotation_history" | "max_hours";
  strategyParam?: number;
  strategyParam2?: number;
  numDays?: number;
}

interface UseScheduleStreamReturn {
  results: LocationResult[];
  isStreaming: boolean;
  error: string | null;
  lockedError: ScheduleLockedError | null;
  generate: (weekStartDate: string, locationIds?: string[], templateIds?: string[], options?: GenerateOptions) => void;
  reset: () => void;
}

export function useScheduleStream(): UseScheduleStreamReturn {
  const [results, setResults] = useState<LocationResult[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockedError, setLockedError] = useState<ScheduleLockedError | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    setResults([]);
    setIsStreaming(false);
    setError(null);
    setLockedError(null);
  }, []);

  const generate = useCallback(
    (weekStartDate: string, locationIds?: string[], templateIds?: string[], options?: GenerateOptions) => {
      reset();
      setIsStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      const token = localStorage.getItem("token");
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (token) {
        headers["Authorization"] = `Bearer ${token}`;
      }

      const body: Record<string, unknown> = {
        week_start_date: weekStartDate,
      };
      if (locationIds && locationIds.length > 0) {
        body.location_ids = locationIds;
      }
      if (templateIds && templateIds.length > 0) {
        body.template_ids = templateIds;
      }
      if (options?.useLocal) {
        body.use_local = true;
        body.strategy = options.strategy || "random";
        if (options.strategyParam !== undefined) {
            body.strategy_param = options.strategyParam;
        }
        if (options.strategyParam2 !== undefined) {
            body.strategy_param2 = options.strategyParam2;
        }
      }
      if (options?.numDays && options.numDays !== 7) {
        body.num_days = options.numDays;
      }

      fetch("/api/v1/schedules/generate", {
        method: "POST",
        headers,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
        .then(async (response) => {
          if (response.status === 409) {
            const body = await response.json().catch(() => null);
            const detail = body?.detail ?? {};
            if (detail.code === "schedule_locked") {
              throw new ScheduleLockedError(
                String(detail.locked_by ?? "another manager"),
                new Date(String(detail.expires_at ?? Date.now())),
              );
            }
          }

          if (!response.ok) {
            const errBody = await response
              .json()
              .catch(() => ({ detail: response.statusText }));
            throw new Error(errBody.detail || response.statusText);
          }

          const reader = response.body?.getReader();
          if (!reader) {
            throw new Error("No readable stream available");
          }

          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            // Keep the last potentially-incomplete line in the buffer
            buffer = lines.pop() || "";

            for (const line of lines) {
              const trimmed = line.trim();
              if (!trimmed) continue;
              try {
                const locationResult: LocationResult = JSON.parse(trimmed);
                setResults((prev) => [...prev, locationResult]);
              } catch {
                // Skip malformed lines
              }
            }
          }

          // Process any remaining buffer
          if (buffer.trim()) {
            try {
              const locationResult: LocationResult = JSON.parse(buffer.trim());
              setResults((prev) => [...prev, locationResult]);
            } catch {
              // Skip malformed line
            }
          }

          setIsStreaming(false);
        })
        .catch((err) => {
          if (err.name === "AbortError") return;
          if (err instanceof ScheduleLockedError) {
            setLockedError(err);
            setIsStreaming(false);
            return;
          }
          setError(err.message || "Stream failed");
          setIsStreaming(false);
        });
    },
    [reset]
  );

  return { results, isStreaming, error, lockedError, generate, reset };
}
