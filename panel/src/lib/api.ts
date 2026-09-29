import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

const listeners = new Set<() => void>();
/** Se dispara cuando el servidor responde 401: la vista vuelve al acceso. */
export function onUnauthorized(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

async function parse<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) listeners.forEach((fn) => fn());
    throw new ApiError(response.status, body.code ?? `HTTP_${response.status}`, body.message ?? "Error del servidor");
  }
  return body as T;
}

export async function getJSON<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } }));
}

export async function postJSON<T>(path: string, body: unknown, extraHeaders: Record<string, string> = {}): Promise<T> {
  return parse<T>(
    await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Botcentro-Panel": "1", ...extraHeaders },
      body: JSON.stringify(body),
    }),
  );
}

export interface ViewState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  receivedAt: number | null;
  reload: () => void;
}

/** Lectura con recarga periódica mientras la pestaña está visible (srs-panel-web.md §10.2). */
export function useView<T>(path: string | null, intervalMs = 15000): ViewState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [receivedAt, setReceivedAt] = useState<number | null>(null);
  const current = useRef(path);
  current.current = path;

  const load = useCallback(() => {
    if (!path) return;
    getJSON<T>(path)
      .then((body) => {
        if (current.current !== path) return;
        setData(body);
        setError(null);
        setReceivedAt(Date.now());
      })
      .catch((err: ApiError) => current.current === path && setError(err))
      .finally(() => current.current === path && setLoading(false));
  }, [path]);

  useEffect(() => {
    setLoading(true);
    setData(null);
    setError(null);
    load();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, intervalMs);
    const onVisible = () => document.visibilityState === "visible" && load();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load, intervalMs]);

  return { data, error, loading, receivedAt, reload: load };
}
