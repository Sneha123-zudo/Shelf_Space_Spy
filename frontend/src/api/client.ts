import { ApiError } from "./errors";

/** When VITE_USE_MOCK=1 every api() call is answered by ./mock instead of the backend. */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === "1";

const FALLBACK_ERROR = "Something went wrong. Please try again.";

/** FastAPI sends `detail` as a string, or as a list of {msg} objects for validation errors. */
function detailMessage(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
      .filter(Boolean);
    if (messages.length) return messages.join("; ");
  }
  return null;
}

/**
 * Fetch helper for the FastAPI backend: JSON in/out, optional bearer token,
 * throws ApiError(detail) on non-2xx. Paths are relative to /api.
 */
export async function api<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  if (USE_MOCK) {
    const { mockApi } = await import("./mock");
    return mockApi<T>(path, options, token);
  }

  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
    });
  } catch {
    throw new ApiError("Cannot reach the Shelf Space Spy API. Is the backend running?", 0);
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    throw new ApiError(detailMessage(body) || FALLBACK_ERROR, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** api() with the token already bound; provided by AuthProvider. */
export type AuthedApi = <T>(path: string, options?: RequestInit) => Promise<T>;
