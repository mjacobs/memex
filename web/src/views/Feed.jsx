import { useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { navigate, useQuery } from "../router.js";
import { Badge, ErrorBanner, Loading, Tags, TagFilterBar } from "../components.jsx";

function NoteCard({ note, selectedTags, onTagClick }) {
  // Routine output (digest/review) gets the accented card; captures and saved
  // links are the user's own material and stay plain.
  const routine = note.kind === "digest" || note.kind === "review";
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
      <Tags tags={note.tags} selected={selectedTags} onTagClick={onTagClick} />
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
  const query = useQuery();
  const [selectedTags, setSelectedTags] = useState(() => {
    const t = query.get("tags");
    return t ? new Set(t.split(",").filter(Boolean)) : new Set();
  });

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

  const toggleTag = (tag) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const visibleNotes =
    notes && selectedTags.size > 0
      ? notes.filter((n) => n.tags && [...selectedTags].every((t) => n.tags.includes(t)))
      : notes;

  return (
    <div className="view">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <TagFilterBar
        selected={selectedTags}
        onRemove={toggleTag}
        onClear={() => setSelectedTags(new Set())}
      />
      {pendingCaptures.map((p) => (
        <PendingCard key={p.id} pending={p} />
      ))}
      {notes === null ? (
        <Loading />
      ) : visibleNotes.length === 0 && pendingCaptures.length === 0 ? (
        <p className="empty">
          {selectedTags.size > 0
            ? "No notes match this tag filter."
            : "Nothing captured yet. Type or record a thought below."}
        </p>
      ) : (
        visibleNotes.map((n) => (
          <NoteCard key={n.id} note={n} selectedTags={selectedTags} onTagClick={toggleTag} />
        ))
      )}
    </div>
  );
}
