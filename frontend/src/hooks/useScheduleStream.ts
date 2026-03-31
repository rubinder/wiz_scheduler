import { useCallback, useRef, useState } from "react";
import type { LocationResult } from "../types";

interface GenerateOptions {
  useLocal?: boolean;
  strategy?: "random" | "rotation";
  numDays?: number;
}

interface UseScheduleStreamReturn {
  results: LocationResult[];
  isStreaming: boolean;
  error: string | null;
  generate: (weekStartDate: string, locationIds?: string[], templateIds?: string[], options?: GenerateOptions) => void;
  reset: () => void;
}

export function useScheduleStream(): UseScheduleStreamReturn {
  const [results, setResults] = useState<LocationResult[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
    }
    setResults([]);
    setIsStreaming(false);
    setError(null);
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
          if (err.name !== "AbortError") {
            setError(err.message || "Stream failed");
            setIsStreaming(false);
          }
        });
    },
    [reset]
  );

  return { results, isStreaming, error, generate, reset };
}
