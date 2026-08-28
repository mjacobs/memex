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
              <Tags tags={t.tags} />
            </div>
          </div>
        ))
      )}
    </div>
  );
}

/** Tags round-trip as a comma- or space-separated line. */
function tagsToText(tags) {
  return (tags || []).join(", ");
}

function textToTags(text) {
  return text
    .split(/[,\s]+/)
    .map((t) => t.replace(/^#/, "").trim())
    .filter(Boolean);
}

function sameTags(a, b) {
  return a.length === b.length && a.every((t, i) => t === b[i]);
}

export default function NoteDetail({ id }) {
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);
  const [missing, setMissing] = useState(false);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .getNote(id)
      .then((d) => alive && setNote(d.note))
      .catch((e) => {
        if (!alive) return;
        // A task or run can link a note that has since been deleted.
        if (e.status === 404) setMissing(true);
        else setError(e.message);
      });
    return () => {
      alive = false;
    };
  }, [id]);

  function startEdit() {
    setConfirmingDelete(false);
    setDraft({
      summary: note.summary || "",
      body: note.body || "",
      tags: tagsToText(note.tags),
    });
  }

  function save() {
    // Send only what actually changed, so the trace event names real edits.
    const changes = {};
    if (draft.summary !== (note.summary || "")) changes.summary = draft.summary;
    if (draft.body !== (note.body || "")) changes.body = draft.body;
    const tags = textToTags(draft.tags);
    if (!sameTags(tags, note.tags || [])) changes.tags = tags;
    if (Object.keys(changes).length === 0) {
      setDraft(null);
      return;
    }
    setSaving(true);
    api
      .patchNote(id, changes)
      .then((d) => {
        setNote(d.note);
        setDraft(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setSaving(false));
  }

  function remove() {
    setDeleting(true);
    api
      .deleteNote(id)
      .then(() => navigate(""))
      .catch((e) => {
        setError(e.message);
        setDeleting(false);
        setConfirmingDelete(false);
      });
  }

  const editing = draft !== null;

  if (missing) {
    return (
      <div className="view">
        <button className="back-link" onClick={() => navigate("")}>
          ← feed
        </button>
        <p className="empty">This note no longer exists.</p>
      </div>
    );
  }

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
            <div className="row">
              <Badge value={note.kind} />
              <span className="muted">{relativeTime(note.created_at)}</span>
            </div>
            {!editing && !confirmingDelete && (
              <div className="row note-actions">
                <button onClick={startEdit}>edit</button>
                <button className="danger" onClick={() => setConfirmingDelete(true)}>
                  delete
                </button>
              </div>
            )}
            {!editing && confirmingDelete && (
              <div className="row note-actions">
                <span className="muted">delete this note?</span>
                <button className="danger" disabled={deleting} onClick={remove}>
                  {deleting ? "deleting…" : "yes, delete"}
                </button>
                <button disabled={deleting} onClick={() => setConfirmingDelete(false)}>
                  cancel
                </button>
              </div>
            )}
          </div>

          {(editing || note.summary) && (
            <div className="section">
              <h3>Summary</h3>
              {editing ? (
                <textarea
                  className="note-edit"
                  rows={2}
                  value={draft.summary}
                  onChange={(e) => setDraft({ ...draft, summary: e.target.value })}
                />
              ) : (
                <p className="body-text">{note.summary}</p>
              )}
            </div>
          )}

          {/* The transcript is what the recording said — read-only; edits go
              to the body, which is the note's canonical text. */}
          {note.transcript && (
            <div className="section">
              <h3>Transcript</h3>
              <p className="body-text">{note.transcript}</p>
            </div>
          )}

          {(editing || !note.transcript || note.body !== note.transcript) && (
            <div className="section">
              <h3>Body</h3>
              {editing ? (
                <textarea
                  className="note-edit"
                  rows={10}
                  value={draft.body}
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                />
              ) : (
                <Markdown text={note.body} />
              )}
            </div>
          )}

          {editing ? (
            <div className="section">
              <h3>Tags</h3>
              <input
                type="text"
                className="note-edit"
                value={draft.tags}
                placeholder="errands, home"
                onChange={(e) => setDraft({ ...draft, tags: e.target.value })}
              />
              <div className="row note-edit-actions">
                <button className="btn primary" disabled={saving} onClick={save}>
                  {saving ? "saving…" : "Save"}
                </button>
                <button className="btn" disabled={saving} onClick={() => setDraft(null)}>
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <Tags tags={note.tags} onTagClick={(t) => navigate(`?tags=${encodeURIComponent(t)}`)} />
          )}

          <LinkedTasks taskIds={note.task_ids} />
          <Trace trace={note.trace} />
        </>
      ) : null}
    </div>
  );
}
