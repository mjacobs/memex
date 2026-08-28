import { useMemo, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";

// External links (http/https) open in a new tab with noopener/noreferrer;
// in-app links (href="#/...", e.g. from routine-generated citations like
// "[note](#/notes/<id>)") stay as plain same-tab anchors so the hash router
// picks them up natively.
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

/** Render note/run body text as sanitized markdown. */
export function Markdown({ text }) {
  const html = useMemo(() => {
    if (!text) return "";
    return DOMPurify.sanitize(marked.parse(text, { breaks: true, gfm: true }), {
      ADD_ATTR: ["target", "rel"],
    });
  }, [text]);
  if (!text) return null;
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Badge({ value }) {
  if (!value) return null;
  return <span className={`badge ${value}`}>{value}</span>;
}

export function Tags({ tags }) {
  if (!tags || tags.length === 0) return null;
  return (
    <div className="tags">
      {tags.map((t) => (
        <span key={t} className="tag">
          #{t}
        </span>
      ))}
    </div>
  );
}

function Json({ value }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

function TraceEvent({ event }) {
  const role = event.role || "model";
  return (
    <div className="trace-event">
      <div className="row">
        <span className={`trace-role ${role}`}>{role}</span>
        {event.tool && <span className="muted">{event.tool}</span>}
        {event.t && <span className="muted">{event.t}</span>}
      </div>
      {event.text && <p className="trace-text">{event.text}</p>}
      {event.args !== undefined && event.args !== null && (
        <>
          <div className="trace-label">args</div>
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
