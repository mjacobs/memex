import { useEffect, useMemo, useState } from "react";
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

const PAGE = 50;
// Enough paging to find matches well down the feed, bounded so a tag with no
// matches costs a handful of requests rather than a walk of every note.
const MAX_PAGES = 10;

/** pendingCaptures: [{id, label, status}] — optimistic entries from the composer. */
export default function Feed({ pendingCaptures, refreshToken }) {
  const [notes, setNotes] = useState(null);
  const [error, setError] = useState(null);
  // The hash query is the one source of truth for the filter, so the back
  // button, the brand link, and a tag clicked on a note detail all land on
  // the filter the URL says — not on whichever set this component last held.
  const query = useQuery();
  const tagKey = (query.get("tags") || "")
    .split(",")
    .filter(Boolean)
    .sort()
    .join(",");
  const selectedTags = useMemo(
    () => new Set(tagKey ? tagKey.split(",") : []),
    [tagKey],
  );

  useEffect(() => {
    let alive = true;
    const tags = tagKey ? tagKey.split(",") : [];
    // Filtering has to reach past the newest 50 notes, or a tag whose notes
    // have all scrolled out of that window reads as "no matches". The API
    // filters on one tag; a second tag narrows further, so keep paging until
    // a screenful of notes carries every selected tag (or the feed runs out).
    async function load() {
      if (tags.length === 0) return (await api.listNotes({ limit: PAGE })).notes;
      const matches = [];
      let before;
      for (let page = 0; page < MAX_PAGES && matches.length < PAGE; page++) {
        const { notes: batch } = await api.listNotes({ limit: PAGE, tag: tags[0], before });
        matches.push(...batch.filter((n) => tags.every((t) => n.tags?.includes(t))));
        if (batch.length < PAGE) break;
        before = batch[batch.length - 1].id;
      }
      return matches.slice(0, PAGE);
    }
    load()
      .then((loaded) => alive && setNotes(loaded))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [refreshToken, tagKey]);

  const setTags = (tags) =>
    navigate(tags.length > 0 ? `?tags=${tags.map(encodeURIComponent).join(",")}` : "");

  const toggleTag = (tag) => {
    const next = new Set(selectedTags);
    if (next.has(tag)) next.delete(tag);
    else next.add(tag);
    setTags([...next]);
  };

  return (
    <div className="view">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <TagFilterBar
        selected={selectedTags}
        onRemove={toggleTag}
        onClear={() => setTags([])}
      />
      {pendingCaptures.map((p) => (
        <PendingCard key={p.id} pending={p} />
      ))}
      {notes === null ? (
        <Loading />
      ) : notes.length === 0 && pendingCaptures.length === 0 ? (
        <p className="empty">
          {selectedTags.size > 0
            ? "No notes match this tag filter."
            : "Nothing captured yet. Type or record a thought below."}
        </p>
      ) : (
        notes.map((n) => (
          <NoteCard key={n.id} note={n} selectedTags={selectedTags} onTagClick={toggleTag} />
        ))
      )}
    </div>
  );
}
