import { useCallback, useRef, useState } from "react";
import type { LocationResult } from "../types";
import { ScheduleLockedError, generateScheduleBody, generateScheduleStream } from "../api/schedules";
import type { GenerateOptions } from "../api/schedules";

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

      generateScheduleStream(
        generateScheduleBody(weekStartDate, locationIds, templateIds, options),
        controller.signal,
      )
        .then(async (stream) => {
          const reader = stream.getReader();

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
