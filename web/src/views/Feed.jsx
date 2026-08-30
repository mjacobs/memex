import { useEffect, useMemo, useRef, useState } from "react";
import { api, relativeTime } from "../api.js";
import { navigate, useQuery } from "../router.js";
import {
  Badge,
  ErrorBanner,
  Loading,
  ResearchStatus,
  Tags,
  TagFilterBar,
} from "../components.jsx";

function NoteCard({ note, selectedTags, onTagClick }) {
  // Agent output (digest/review/research) gets the accented card; captures
  // and saved links are the user's own material and stay plain.
  const agentNote = ["digest", "review", "research"].includes(note.kind);
  return (
    <div
      className={`card clickable ${agentNote ? `note-routine kind-${note.kind}` : ""}`}
      onClick={() => navigate(`notes/${note.id}`)}
    >
      <div className="row spread">
        <Badge value={note.kind} />
        <span className="muted">{relativeTime(note.created_at)}</span>
      </div>
      <p className="note-summary">{note.summary || note.body}</p>
      <ResearchStatus status={note.research_status} />
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

// Deep Research runs take minutes; a 15 s poll is timely without hammering
// the API while something is actually running.
const OPS_POLL_MS = 15000;
// Research also starts from chat, which never touches refreshToken — so the
// poll drops to a slow heartbeat rather than stopping, or a run started
// elsewhere would show neither its badge nor its report until a reload.
const OPS_IDLE_POLL_MS = 60000;

/** pendingCaptures: [{id, label, status}] — optimistic entries from the composer. */
export default function Feed({ pendingCaptures, refreshToken }) {
  const [notes, setNotes] = useState(null);
  // True when the scan stopped at MAX_PAGES with matches still possible
  // further back — the empty state has to say "none found here", not "none".
  const [partial, setPartial] = useState(false);
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

  // Notes carry their own research_status now, so the cards say what is
  // pending. This poll is only the refetch trigger: any change in the running
  // set means some note's status moved. Watching for the count to reach zero
  // was not enough — a run started from chat takes it 0 -> 1 and its card
  // would show nothing, and one of three runs finishing takes it 3 -> 2 with
  // a report already in the feed.
  const [opsSettled, setOpsSettled] = useState(0);
  const prevRunning = useRef(null);
  useEffect(() => {
    let alive = true;
    let timer = null;
    async function poll() {
      let count = null; // null = this poll failed; refetching is best-effort
      try {
        count = (await api.listOperations("running")).operations.length;
      } catch {
        // the feed already surfaces its own errors
      }
      if (!alive) return;
      if (count !== null) {
        if (prevRunning.current !== null && count !== prevRunning.current) {
          setOpsSettled((n) => n + 1);
        }
        prevRunning.current = count;
      }
      timer = setTimeout(poll, count ? OPS_POLL_MS : OPS_IDLE_POLL_MS);
    }
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [refreshToken]);

  useEffect(() => {
    let alive = true;
    const tags = tagKey ? tagKey.split(",") : [];
    // Filtering has to reach past the newest 50 notes, or a tag whose notes
    // have all scrolled out of that window reads as "no matches". The API
    // filters on one tag; a second tag narrows further, so keep paging until
    // a screenful of notes carries every selected tag (or the feed runs out).
    async function load() {
      if (tags.length === 0) {
        return { matches: (await api.listNotes({ limit: PAGE })).notes, partial: false };
      }
      const matches = [];
      let before;
      let exhausted = false;
      let page = 0;
      for (; page < MAX_PAGES && matches.length < PAGE; page++) {
        const { notes: batch } = await api.listNotes({ limit: PAGE, tag: tags[0], before });
        matches.push(...batch.filter((n) => tags.every((t) => n.tags?.includes(t))));
        if (batch.length < PAGE) {
          exhausted = true;
          break;
        }
        before = batch[batch.length - 1].id;
      }
      return { matches: matches.slice(0, PAGE), partial: !exhausted && matches.length < PAGE };
    }
    load()
      .then(({ matches, partial: cut }) => {
        if (!alive) return;
        setNotes(matches);
        setPartial(cut);
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [refreshToken, tagKey, opsSettled]);

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
          {selectedTags.size === 0
            ? "Nothing captured yet. Type or record a thought below."
            : partial
              ? `No notes with all of these tags in the last ${PAGE * MAX_PAGES} — try one tag at a time.`
              : "No notes match this tag filter."}
        </p>
      ) : (
        notes.map((n) => (
          <NoteCard key={n.id} note={n} selectedTags={selectedTags} onTagClick={toggleTag} />
        ))
      )}
      {partial && notes && notes.length > 0 && (
        <p className="empty">
          Showing matches from the last {PAGE * MAX_PAGES} notes tagged #
          {tagKey.split(",")[0]}.
        </p>
      )}
    </div>
  );
}
