import { useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { navigate } from "../router.js";
import { Badge, ErrorBanner, Loading, Tags } from "../components.jsx";

function NoteCard({ note }) {
  const routine = note.kind !== "capture";
  return (
    <div
      className={`card clickable ${routine ? `note-routine kind-${note.kind}` : ""}`}
      onClick={() => navigate(`notes/${note.id}`)}
    >
      <div className="row spread">
        <Badge value={note.kind} />
        <span className="muted">{relativeTime(note.created_at)}</span>
      </div>
      <p className="note-summary">{note.summary || note.body}</p>
      <Tags tags={note.tags} />
    </div>
  );
}

function PendingCard({ pending }) {
  return (
    <div className="card pending-capture">
      <div className="row spread">
        <Badge value={pending.status || "pending"} />
        <span className="spinner" />
      </div>
      <p className="note-summary">{pending.label}</p>
    </div>
  );
}

/** pendingCaptures: [{id, label, status}] — optimistic entries from the composer. */
export default function Feed({ pendingCaptures, refreshToken }) {
  const [notes, setNotes] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .listNotes({ limit: 50 })
      .then((d) => alive && setNotes(d.notes))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [refreshToken]);

  return (
    <div className="view">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {pendingCaptures.map((p) => (
        <PendingCard key={p.id} pending={p} />
      ))}
      {notes === null ? (
        <Loading />
      ) : notes.length === 0 && pendingCaptures.length === 0 ? (
        <p className="empty">Nothing captured yet. Type or record a thought below.</p>
      ) : (
        notes.map((n) => <NoteCard key={n.id} note={n} />)
      )}
    </div>
  );
}
