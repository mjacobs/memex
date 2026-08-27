import { useState } from "react";

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
