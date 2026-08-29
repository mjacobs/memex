import { useEffect, useRef, useState } from "react";
import { api, relativeTime, streamChatMessage } from "./api.js";
import { ErrorBanner, Loading, Markdown, TraceEvent } from "./components.jsx";

// One conversation event. User turns are plain text bubbles (the user's own
// words, verbatim — no markdown surprises); model text renders as markdown
// like note bodies, so #/notes/<id> citations become working in-app links;
// tool calls/results reuse the trace-event rendering the run and note views
// already use.
function ChatEvent({ event }) {
  if (event.tool) {
    return (
      <div className="trace chat-tool">
        <TraceEvent event={event} />
      </div>
    );
  }
  if (!event.text) return null;
  if (event.role === "user") {
    return <div className="chat-msg user">{event.text}</div>;
  }
  return (
    <div className="chat-msg model">
      <Markdown text={event.text} />
    </div>
  );
}

function SessionList({ sessions, onOpen, onNew }) {
  return (
    <div className="chat-sessions">
      <button className="btn primary chat-new" onClick={onNew}>
        New chat
      </button>
      {sessions === null ? (
        <Loading />
      ) : sessions.length === 0 ? (
        <p className="empty">No chats yet.</p>
      ) : (
        sessions.map((s) => (
          <div key={s.id} className="card clickable" onClick={() => onOpen(s.id)}>
            <p className="chat-session-title">{s.title || "Untitled chat"}</p>
            <span className="muted">{relativeTime(s.updated_at)}</span>
          </div>
        ))
      )}
    </div>
  );
}

/** Persistent chat sidebar. Mounted once in App so an in-flight turn and the
 * open conversation survive navigation between views. */
export default function Chat({ open, onClose }) {
  const [sessions, setSessions] = useState(null);
  const [activeId, setActiveId] = useState(null);
  const [session, setSession] = useState(null);
  const [trace, setTrace] = useState(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);
  const logRef = useRef(null);

  // Session list, refreshed each time the panel lands on it (a finished turn
  // may have retitled or reordered sessions).
  useEffect(() => {
    if (!open || activeId) return;
    let alive = true;
    setSessions(null);
    api
      .listChatSessions()
      .then((d) => alive && setSessions(d.sessions))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [open, activeId]);

  useEffect(() => {
    if (!activeId) {
      setSession(null);
      setTrace(null);
      return;
    }
    let alive = true;
    setSession(null);
    setTrace(null);
    api
      .getChatSession(activeId)
      .then((d) => {
        if (!alive) return;
        setSession(d.session);
        setTrace(d.session.trace || []);
      })
      .catch((e) => {
        if (!alive) return;
        setError(e.status === 404 ? "This chat no longer exists." : e.message);
        setActiveId(null);
      });
    return () => {
      alive = false;
    };
  }, [activeId]);

  // Follow the stream: keep the newest event in view as the turn executes.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [trace, sending]);

  function newSession() {
    setError(null);
    api
      .createChatSession()
      .then((d) => setActiveId(d.session.id))
      .catch((e) => setError(e.message));
  }

  async function send(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending || !activeId) return;
    setInput("");
    setSending(true);
    setError(null);
    try {
      // The server streams the user turn back as the first trace event, so
      // nothing is appended optimistically — the log shows what was recorded.
      await streamChatMessage(activeId, text, {
        onTrace: (ev) => setTrace((t) => [...(t || []), ev]),
        onDone: (d) => setSession(d.session),
        onError: (err) => setError(err.message || err.code || "chat turn failed"),
      });
    } catch (e2) {
      setError(e2.message);
    } finally {
      setSending(false);
    }
  }

  if (!open) return null;

  return (
    <aside className="chat-panel">
      <div className="chat-header">
        {activeId && (
          <button
            className="back-link chat-back"
            disabled={sending}
            onClick={() => setActiveId(null)}
          >
            ← chats
          </button>
        )}
        <span className="chat-header-title">
          {activeId ? session?.title || "Chat" : "Chat"}
        </span>
        <button className="chat-close" onClick={onClose} aria-label="Close chat">
          ×
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!activeId ? (
        <SessionList sessions={sessions} onOpen={setActiveId} onNew={newSession} />
      ) : (
        <>
          <div className="chat-log" ref={logRef}>
            {trace === null ? (
              <Loading />
            ) : trace.length === 0 && !sending ? (
              <p className="empty">Ask about your notes, tasks, or kick off research.</p>
            ) : (
              trace.map((ev, i) => <ChatEvent key={i} event={ev} />)
            )}
            {sending && (
              <p className="muted chat-thinking">
                <span className="spinner" /> thinking…
              </p>
            )}
          </div>
          <form className="chat-input" onSubmit={send}>
            <input
              type="text"
              value={input}
              placeholder="Message memex…"
              disabled={sending}
              onChange={(e) => setInput(e.target.value)}
            />
            <button
              className="icon-btn send"
              type="submit"
              disabled={sending || !input.trim()}
              aria-label="Send"
            >
              ➤
            </button>
          </form>
        </>
      )}
    </aside>
  );
}
