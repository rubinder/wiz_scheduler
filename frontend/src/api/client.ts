const BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

async function apiErrorFrom(res: Response): Promise<ApiError> {
  const body = await res.json().catch(() => ({ detail: res.statusText }));
  const detail = body.detail;
  const message = typeof detail === "string" ? detail : (detail?.message || res.statusText);
  return new ApiError(res.status, message, detail);
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  // Only set Content-Type to JSON if body is a string (not FormData)
  if (options.body && typeof options.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  });

  if (!res.ok) throw await apiErrorFrom(res);

  if (res.status === 204) {
    return undefined as unknown as T;
  }

  return res.json() as Promise<T>;
}

/**
 * Best-effort human-readable message for a caught error.
 *
 * ApiError.data (== the response body's `detail`) is a plain string for
 * HTTPException detail (e.g. the "start_time and end_time must not be
 * equal" 422 raised from update handlers), but a list of pydantic
 * error objects for validation errors raised by a model_validator on the
 * *Create schemas. Handle both so a manager always sees the real reason a
 * save failed instead of a generic status text.
 */
export function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const data = err.data;
    if (typeof data === "string") return data;
    if (Array.isArray(data) && data.length > 0) {
      const first = data[0] as { msg?: string } | undefined;
      if (first?.msg) return first.msg;
    }
    if (err.message) return err.message;
  }
  if (err instanceof Error) return err.message;
  return fallback;
}

/** POST a JSON body and resolve to the response body as a byte stream. */
export async function apiStream(
  path: string,
  body: unknown,
  signal?: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  const token = localStorage.getItem("token");
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) throw await apiErrorFrom(res);
  if (!res.body) throw new Error("No readable stream available");
  return res.body;
}
