import { useCallback, useEffect, useState } from "react";
import { getKey, setKey, setUnauthorizedHandler } from "./api.js";
import { useRoute } from "./router.js";
import Composer from "./Composer.jsx";
import Login from "./Login.jsx";
import { ErrorBanner } from "./components.jsx";
import Feed from "./views/Feed.jsx";
import NoteDetail from "./views/NoteDetail.jsx";
import Tasks from "./views/Tasks.jsx";
import Approvals from "./views/Approvals.jsx";
import { RunDetail, RunList } from "./views/Runs.jsx";

const NAV = [
  ["", "Feed"],
  ["tasks", "Tasks"],
  ["approvals", "Approvals"],
  ["runs", "Runs"],
];

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getKey()));
  const [authReason, setAuthReason] = useState(null);
  const [pendingCaptures, setPendingCaptures] = useState([]);
  const [refreshToken, setRefreshToken] = useState(0);
  const [globalError, setGlobalError] = useState(null);
  const route = useRoute();

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setAuthReason("unauthorized");
      setAuthed(false);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const onPending = useCallback(
    (p) => setPendingCaptures((ps) => [p, ...ps]),
    [],
  );
  const onUpdatePending = useCallback(
    (id, patch) =>
      setPendingCaptures((ps) => ps.map((p) => (p.id === id ? { ...p, ...patch } : p))),
    [],
  );
  const onSettled = useCallback((id) => {
    setPendingCaptures((ps) => ps.filter((p) => p.id !== id));
    setRefreshToken((n) => n + 1);
    if (window.location.hash && window.location.hash !== "#/") {
      // stay put; the feed refetches on next visit via refreshToken
    }
  }, []);

  if (!authed) {
    return (
      <Login
        reason={authReason}
        onDone={() => {
          setAuthReason(null);
          setAuthed(true);
          setRefreshToken((n) => n + 1);
        }}
      />
    );
  }

  const [section, id] = route;
  let view;
  if (section === "notes" && id) view = <NoteDetail id={id} />;
  else if (section === "tasks") view = <Tasks />;
  else if (section === "approvals") view = <Approvals />;
  else if (section === "runs" && id) view = <RunDetail id={id} />;
  else if (section === "runs") view = <RunList />;
  else view = <Feed pendingCaptures={pendingCaptures} refreshToken={refreshToken} />;

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          mem<span>ex</span>
        </a>
        <nav className="nav">
          {NAV.map(([path, label]) => (
            <a
              key={path}
              href={`#/${path}`}
              className={(section || "") === path || (path === "" && !section) ? "active" : ""}
            >
              {label}
            </a>
          ))}
        </nav>
        <button
          className="settings-btn"
          title="change device key"
          aria-label="change device key"
          onClick={() => {
            setKey("");
            setAuthReason(null);
            setAuthed(false);
          }}
        >
          ⚙
        </button>
      </header>
      <ErrorBanner error={globalError} onDismiss={() => setGlobalError(null)} />
      {view}
      <Composer
        onPending={onPending}
        onUpdatePending={onUpdatePending}
        onSettled={onSettled}
        onError={setGlobalError}
      />
    </div>
  );
}
