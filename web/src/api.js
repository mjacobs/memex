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
  captureText: (text) =>
    request("/api/v1/capture", {
      method: "POST",
      body: { text, source: "web" },
      headers: { "X-Memex-Source": "web" },
    }),
  captureAudio: (blob, mime) =>
    request("/api/v1/capture/audio", {
      method: "POST",
      body: blob,
      raw: true,
      headers: { "Content-Type": mime, "X-Memex-Source": "web" },
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
  listTasks: (status = "open") => request(`/api/v1/tasks?status=${status}`),
  patchTask: (id, changes) =>
    request(`/api/v1/tasks/${id}`, { method: "PATCH", body: changes }),
  listApprovals: (status = "pending") => request(`/api/v1/approvals?status=${status}`),
  approve: (id) => request(`/api/v1/approvals/${id}/approve`, { method: "POST" }),
  reject: (id) => request(`/api/v1/approvals/${id}/reject`, { method: "POST" }),
  listRuns: (limit = 20) => request(`/api/v1/routines/runs?limit=${limit}`),
  getRun: (id) => request(`/api/v1/routines/runs/${id}`),
};

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
