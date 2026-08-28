import { useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { navigate } from "../router.js";
import { Badge, ErrorBanner, ErrorBlock, Loading, Markdown, Trace } from "../components.jsx";

const ROUTINE_LABELS = {
  daily_review: "Daily review",
  nightly_digest: "Nightly digest",
};

export function RunList() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .listRuns(20)
      .then((d) => alive && setRuns(d.runs))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className="view">
      <h2 className="view-title">Routine runs</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {runs === null ? (
        <Loading />
      ) : runs.length === 0 ? (
        <p className="empty">No routine runs yet.</p>
      ) : (
        runs.map((r) => (
          <div key={r.id} className="card clickable" onClick={() => navigate(`runs/${r.id}`)}>
            <div className="row spread">
              <span className="row">
                <strong>{ROUTINE_LABELS[r.routine] || r.routine}</strong>
                <Badge value={r.status} />
              </span>
              <span className="muted">{relativeTime(r.fired_at)}</span>
            </div>
            {r.summary && <p className="note-summary">{r.summary}</p>}
            {r.error && <p className="run-error-preview">{r.error}</p>}
          </div>
        ))
      )}
    </div>
  );
}

export function RunDetail({ id }) {
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .getRun(id)
      .then((d) => alive && setRun(d.run))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id]);

  return (
    <div className="view">
      <button className="back-link" onClick={() => navigate("runs")}>
        ← runs
      </button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {run === null && !error ? (
        <Loading />
      ) : run ? (
        <>
          <div className="row spread">
            <span className="row">
              <strong>{ROUTINE_LABELS[run.routine] || run.routine}</strong>
              <Badge value={run.status} />
            </span>
            <span className="muted">{relativeTime(run.fired_at)}</span>
          </div>
          {run.summary && (
            <div className="section">
              <h3>Summary</h3>
              <Markdown text={run.summary} />
            </div>
          )}
          {run.error && (
            <div className="section">
              <h3>Error</h3>
              <ErrorBlock text={run.error} />
            </div>
          )}
          {run.note_id && (
            <div className="section">
              <h3>Output note</h3>
              <button className="btn" onClick={() => navigate(`notes/${run.note_id}`)}>
                Open note
              </button>
            </div>
          )}
          {run.approval_ids && run.approval_ids.length > 0 && (
            <div className="section">
              <h3>Approvals queued</h3>
              <p className="muted">
                {run.approval_ids.length} approval{run.approval_ids.length === 1 ? "" : "s"} —{" "}
                <a href="#/approvals">review them</a>
              </p>
            </div>
          )}
          <Trace trace={run.trace} />
        </>
      ) : null}
    </div>
  );
}
