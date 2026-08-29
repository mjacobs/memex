import { useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { linkifyText } from "./linkify.js";

export { PlainText } from "./linkify.js";

// External links (http/https) open in a new tab with noopener/noreferrer, so
// following a saved read-later link doesn't throw away the feed; in-app links
// (href="#/...", e.g. routine-generated citations like "[note](#/notes/<id>)")
// stay as plain same-tab anchors so the hash router picks them up natively.
// Added after sanitization, where the note text itself can't smuggle them in.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName !== "A") return;
  const href = node.getAttribute("href") || "";
  if (/^https?:\/\//i.test(href)) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  } else {
    node.removeAttribute("target");
  }
});

// What a rendered body is allowed to be: prose, lists, code, tables, links.
// Deliberately no <img>, and no tag or attribute that fetches on its own —
// note text is model-written from captured material, and a screenshot or a
// saved page can carry an injected instruction. An <img> the model was talked
// into emitting would silently GET an attacker URL with whatever it encoded
// into the path the moment the digest is opened. A link still has to be
// clicked, which is the line we draw.
const ALLOWED_TAGS = [
  "p", "br", "hr", "strong", "em", "del", "code", "pre", "blockquote",
  "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
  "a", "table", "thead", "tbody", "tr", "th", "td",
];
// target/rel are absent on purpose: the hook above adds them after attribute
// filtering, so the note text can never supply its own.
const ALLOWED_ATTR = ["href", "title"];

/** Render note/run body text as sanitized markdown. */
export function Markdown({ text }) {
  const html = useMemo(() => {
    if (!text) return "";
    return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }), {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      ALLOW_DATA_ATTR: false,
    });
  }, [text]);
  if (!text) return null;
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Badge({ value }) {
  if (!value) return null;
  return <span className={`badge ${value}`}>{value}</span>;
}

/** tags: string[]. onTagClick(tag): optional, makes chips clickable/toggleable.
 * selected: optional Set/array of tags to render with an active style. */
export function Tags({ tags, onTagClick, selected }) {
  if (!tags || tags.length === 0) return null;
  const selectedSet = selected instanceof Set ? selected : new Set(selected || []);
  return (
    <div className="tags">
      {tags.map((t) => (
        <span
          key={t}
          className={`tag ${onTagClick ? "tag-clickable" : ""} ${selectedSet.has(t) ? "tag-active" : ""}`}
          onClick={
            onTagClick &&
            ((e) => {
              e.stopPropagation();
              onTagClick(t);
            })
          }
        >
          #{t}
        </span>
      ))}
    </div>
  );
}

/** Formatted routine-run error: monospace, wrapped, with linkified URLs. */
export function ErrorBlock({ text }) {
  if (!text) return null;
  return <pre className="run-error">{linkifyText(text)}</pre>;
}

function Json({ value }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

/** One compact trace event — also reused by the chat panel for tool calls. */
export function TraceEvent({ event }) {
  const role = event.role || "model";
  // A role="user" event is either the prompt that started the turn or an owner
  // edit; PATCH /notes/{id} is the only writer of args.fields, so it labels
  // itself rather than hiding among the agent's inputs.
  const isEdit = role === "user" && Array.isArray(event.args?.fields);
  return (
    <div className={`trace-event ${isEdit ? "user-edit" : ""}`}>
      <div className="row">
        <span className={`trace-role ${role}`}>{isEdit ? "user edit" : role}</span>
        {event.tool && <span className="muted">{event.tool}</span>}
        {event.t && <span className="muted">{event.t}</span>}
      </div>
      {event.text && <p className="trace-text">{event.text}</p>}
      {event.args !== undefined && event.args !== null && (
        <>
          <div className="trace-label">{isEdit ? "changed" : "args"}</div>
          <Json value={event.args} />
        </>
      )}
      {event.result !== undefined && event.result !== null && (
        <>
          <div className="trace-label">result</div>
          <Json value={event.result} />
        </>
      )}
    </div>
  );
}

/** Collapsible agent trace — the replayability surface. */
export function Trace({ trace }) {
  const [open, setOpen] = useState(false);
  const events = trace || [];
  if (events.length === 0) return null;
  return (
    <div className="section">
      <button className={`trace-toggle ${open ? "open" : ""}`} onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} Agent trace ({events.length} event{events.length === 1 ? "" : "s"})
      </button>
      {open && (
        <div className="trace">
          {events.map((e, i) => (
            <TraceEvent key={i} event={e} />
          ))}
        </div>
      )}
    </div>
  );
}

/** Active tag filter chips + a "clear" affordance. selected: Set<string>. onRemove(tag), onClear(). */
export function TagFilterBar({ selected, onRemove, onClear }) {
  const tags = selected instanceof Set ? [...selected] : selected || [];
  if (tags.length === 0) return null;
  return (
    <div className="tag-filter-bar">
      <span className="tag-filter-label">filtering by:</span>
      {tags.map((t) => (
        <span key={t} className="tag tag-clickable tag-active" onClick={() => onRemove(t)}>
          #{t} ×
        </span>
      ))}
      <button className="tag-filter-clear" onClick={onClear}>
        clear
      </button>
    </div>
  );
}

export function ErrorBanner({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div className="error-banner" onClick={onDismiss}>
      {error}
    </div>
  );
}

export function Loading() {
  return (
    <p className="muted">
      <span className="spinner" /> loading…
    </p>
  );
}
