import { useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { navigate } from "../router.js";
import { Badge, ErrorBanner, Loading, Markdown, Tags, Trace } from "../components.jsx";

function LinkedTasks({ taskIds }) {
  const [tasks, setTasks] = useState(null);
  useEffect(() => {
    if (!taskIds || taskIds.length === 0) return;
    let alive = true;
    // No batch-get in the contract; the task list endpoints are status-scoped,
    // so fetch open + done + dropped and filter to the linked ids.
    Promise.all(["open", "done", "dropped"].map((s) => api.listTasks(s)))
      .then((results) => {
        if (!alive) return;
        const all = results.flatMap((r) => r.tasks);
        setTasks(all.filter((t) => taskIds.includes(t.id)));
      })
      .catch(() => alive && setTasks([]));
    return () => {
      alive = false;
    };
  }, [taskIds]);

  if (!taskIds || taskIds.length === 0) return null;
  return (
    <div className="section">
      <h3>Linked tasks</h3>
      {tasks === null ? (
        <Loading />
      ) : (
        tasks.map((t) => (
          <div key={t.id} className={`card task ${t.status === "done" ? "done" : ""}`}>
            <div>
              <div className="row">
                <span className="task-title">{t.title}</span>
                <Badge value={t.status} />
              </div>
              {(t.due_hint || t.due_at) && (
                <div className="due">
                  due {t.due_at ? new Date(t.due_at).toLocaleDateString() : t.due_hint}
                </div>
              )}
              <Tags tags={t.tags} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}

export default function NoteDetail({ id }) {
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .getNote(id)
      .then((d) => alive && setNote(d.note))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [id]);

  return (
    <div className="view">
      <button className="back-link" onClick={() => navigate("")}>
        ← feed
      </button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {note === null && !error ? (
        <Loading />
      ) : note ? (
        <>
          <div className="row spread">
            <Badge value={note.kind} />
            <span className="muted">{relativeTime(note.created_at)}</span>
          </div>
          {note.summary && (
            <div className="section">
              <h3>Summary</h3>
              <p className="body-text">{note.summary}</p>
            </div>
          )}
          <div className="section">
            <h3>{note.transcript ? "Transcript" : "Body"}</h3>
            {note.transcript ? (
              <p className="body-text">{note.transcript}</p>
            ) : (
              <Markdown text={note.body} />
            )}
          </div>
          {note.transcript && note.body && note.body !== note.transcript && (
            <div className="section">
              <h3>Body</h3>
              <Markdown text={note.body} />
            </div>
          )}
          <Tags tags={note.tags} />
          <LinkedTasks taskIds={note.task_ids} />
          <Trace trace={note.trace} />
        </>
      ) : null}
    </div>
  );
}
