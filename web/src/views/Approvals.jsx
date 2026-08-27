import { useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { Badge, ErrorBanner, Loading } from "../components.jsx";

function fmt(v) {
  if (v == null) return "—";
  if (Array.isArray(v)) return v.join(", ") || "—";
  return String(v);
}

/** Readable rendering of the Action contract's discriminated union. */
function ActionDiff({ action }) {
  if (!action) return null;
  if (action.type === "task_update") {
    return (
      <div className="diff">
        <div className="diff-line">
          <span className="diff-field">update</span>
          <span className="diff-val">task {action.task_id}</span>
        </div>
        {Object.entries(action.changes || {}).map(([field, value]) => (
          <div key={field} className="diff-line">
            <span className="diff-field">{field}</span>
            <span className="diff-val">→ {fmt(value)}</span>
          </div>
        ))}
      </div>
    );
  }
  if (action.type === "task_create") {
    const t = action.task || {};
    return (
      <div className="diff">
        <div className="diff-line">
          <span className="diff-field">create</span>
          <span className="diff-val">new task</span>
        </div>
        {Object.entries(t).map(([field, value]) => (
          <div key={field} className="diff-line">
            <span className="diff-field">{field}</span>
            <span className="diff-val">{fmt(value)}</span>
          </div>
        ))}
      </div>
    );
  }
  return <pre className="diff">{JSON.stringify(action, null, 2)}</pre>;
}

function ApprovalCard({ approval, onResolved }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const pending = approval.status === "pending";

  const act = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      const d = await fn(approval.id);
      onResolved(d.approval);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="row spread">
        <Badge value={approval.status} />
        <span className="muted">{relativeTime(approval.created_at)}</span>
      </div>
      <p className="note-summary">{approval.reason}</p>
      <ActionDiff action={approval.action} />
      {error && <div className="error-banner">{error}</div>}
      {pending ? (
        <div className="approval-actions">
          <button className="btn primary" disabled={busy} onClick={() => act(api.approve)}>
            Approve
          </button>
          <button className="btn danger" disabled={busy} onClick={() => act(api.reject)}>
            Reject
          </button>
        </div>
      ) : (
        approval.result && <div className="result-line">✓ {approval.result}</div>
      )}
    </div>
  );
}

export default function Approvals() {
  const [approvals, setApprovals] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .listApprovals("pending")
      .then((d) => alive && setApprovals(d.approvals))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  const onResolved = (resolved) =>
    setApprovals((as) => as.map((a) => (a.id === resolved.id ? resolved : a)));

  return (
    <div className="view">
      <h2 className="view-title">Approvals</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {approvals === null ? (
        <Loading />
      ) : approvals.length === 0 ? (
        <p className="empty">No pending approvals.</p>
      ) : (
        approvals.map((a) => <ApprovalCard key={a.id} approval={a} onResolved={onResolved} />)
      )}
    </div>
  );
}
