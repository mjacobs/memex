// Thin API client for the frozen /api/v1 contract (docs/contracts.md).

const KEY_STORAGE = "memex.deviceKey";

export function getKey() {
  try {
    return localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setKey(key) {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key);
    else localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* private mode etc. */
  }
}

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function request(path, { method = "GET", body, headers = {}, raw = false } = {}) {
  const h = { Authorization: `Bearer ${getKey()}`, ...headers };
  let payload = body;
  if (body !== undefined && !raw) {
    h["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers: h, body: payload });
  } catch (e) {
    throw new ApiError(0, "network", e.message || "network error");
  }
  if (res.status === 401) {
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(401, "unauthorized", "invalid or missing device key");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const err = data && data.error ? data.error : {};
    throw new ApiError(res.status, err.code || "error", err.message || res.statusText);
  }
  return data;
}

// Image captures are served as bytes behind the device key, so an <img src>
// can't load one directly — fetch it and hand back an object URL the caller
// revokes when it unmounts.
export async function fetchImageObjectUrl(path) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${getKey()}` } });
  if (res.status === 401) {
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(401, "unauthorized", "invalid or missing device key");
  }
  if (!res.ok) throw new ApiError(res.status, "error", res.statusText);
  return URL.createObjectURL(await res.blob());
}

export const api = {
  // `research` is the user's explicit "dig into this" — the only thing that
  // starts a paid research run (contracts.md). Audio has a raw body, so its
  // flag rides a header.
  captureText: (text, { research = false } = {}) =>
    request("/api/v1/capture", {
      method: "POST",
      body: { text, source: "web", research },
      headers: { "X-Memex-Source": "web" },
    }),
  captureAudio: (blob, mime, { research = false } = {}) =>
    request("/api/v1/capture/audio", {
      method: "POST",
      body: blob,
      raw: true,
      headers: {
        "Content-Type": mime,
        "X-Memex-Source": "web",
        ...(research ? { "X-Memex-Research": "1" } : {}),
      },
    }),
  getCapture: (id) => request(`/api/v1/captures/${id}`),
  listNotes: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null && v !== "") q.set(k, v);
    const qs = q.toString();
    return request(`/api/v1/notes${qs ? `?${qs}` : ""}`);
  },
  getNote: (id) => request(`/api/v1/notes/${id}`),
  patchNote: (id, changes) =>
    request(`/api/v1/notes/${id}`, { method: "PATCH", body: changes }),
  deleteNote: (id) => request(`/api/v1/notes/${id}`, { method: "DELETE" }),
  // Research a note that already exists. The capture flag above is the other
  // half: same rule, same reason — only the owner starts a run.
  researchNote: (id) => request(`/api/v1/notes/${id}/research`, { method: "POST" }),
  listTasks: (status = "open") => request(`/api/v1/tasks?status=${status}`),
  patchTask: (id, changes) =>
    request(`/api/v1/tasks/${id}`, { method: "PATCH", body: changes }),
  listApprovals: (status = "pending") => request(`/api/v1/approvals?status=${status}`),
  approve: (id) => request(`/api/v1/approvals/${id}/approve`, { method: "POST" }),
  reject: (id) => request(`/api/v1/approvals/${id}/reject`, { method: "POST" }),
  listRuns: (limit = 20) => request(`/api/v1/routines/runs?limit=${limit}`),
  getRun: (id) => request(`/api/v1/routines/runs/${id}`),
  listOperations: (status) =>
    request(`/api/v1/operations${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  createChatSession: () => request("/api/v1/chat/sessions", { method: "POST" }),
  listChatSessions: (limit = 20) => request(`/api/v1/chat/sessions?limit=${limit}`),
  getChatSession: (id) => request(`/api/v1/chat/sessions/${id}`),
};

// POST /chat/sessions/{id}/messages streams text/event-stream, which
// EventSource can't do (GET only) — so read the fetch body and split SSE
// frames by hand. The server emits one single-line JSON `data:` per frame:
// `event: trace` per TraceEvent, then `event: done` with the session summary,
// or `event: error` if the turn crashed mid-stream.
export async function streamChatMessage(sessionId, text, { onTrace, onDone, onError }) {
  let res;
  try {
    res = await fetch(`/api/v1/chat/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${getKey()}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text }),
    });
  } catch (e) {
    throw new ApiError(0, "network", e.message || "network error");
  }
  if (res.status === 401) {
    if (onUnauthorized) onUnauthorized();
    throw new ApiError(401, "unauthorized", "invalid or missing device key");
  }
  if (!res.ok) {
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* non-JSON body */
    }
    const err = data && data.error ? data.error : {};
    throw new ApiError(res.status, err.code || "error", err.message || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data = line.slice(5).trim();
      }
      if (!data) continue;
      let payload;
      try {
        payload = JSON.parse(data);
      } catch {
        continue;
      }
      if (event === "trace" && onTrace) onTrace(payload);
      else if (event === "done" && onDone) onDone(payload);
      else if (event === "error" && onError) onError(payload.error || payload);
    }
  }
}

export function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}
