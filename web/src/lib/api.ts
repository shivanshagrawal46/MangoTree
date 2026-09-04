"use client";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json();
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T,>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  /** Multipart upload. No Content-Type header: the browser sets the boundary. */
  upload: async <T,>(path: string, form: FormData): Promise<T> => {
    const res = await fetch(`/api${path}`, { method: "POST", credentials: "include", body: form });
    if (!res.ok) {
      let msg = res.statusText;
      try { const j = await res.json(); msg = j.detail || msg; } catch {}
      throw new ApiError(res.status, msg);
    }
    return res.json();
  },
};

export type SSEEvent = { seq: number; t: number; kind: string; data: any };

/** Subscribe to a job's event stream. Returns an unsubscribe function. */
export function subscribeJob(jobId: string, onEvent: (e: SSEEvent) => void, onEnd?: () => void): () => void {
  const es = new EventSource(`/api/jobs/${jobId}/stream`, { withCredentials: true });
  const handler = (ev: MessageEvent) => {
    try { onEvent(JSON.parse(ev.data)); } catch {}
  };
  const kinds = ["job", "agent_start", "agent_step", "agent_sufficiency_gate", "agent_budget", "agent_done",
                 "phase", "second_reader", "status", "result", "error", "done", "end"];
  kinds.forEach((k) => es.addEventListener(k, handler as EventListener));
  es.addEventListener("end", () => { es.close(); onEnd?.(); });
  es.onerror = () => {
    // A network blip: the browser reconnects and the job replays its events.
    // A closed stream (HTTP 410/404 — the job died with a server restart) never
    // reconnects; end the subscription so the UI stops waiting on nothing.
    if (es.readyState === EventSource.CLOSED) {
      onEvent({ seq: -1, t: 0, kind: "error", data: { error: "The server restarted before this answer finished. Please ask again." } });
      onEnd?.();
    }
  };
  return () => es.close();
}
