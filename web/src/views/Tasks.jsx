import { useCallback, useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { Badge, ErrorBanner, Loading, Tags } from "../components.jsx";

function TaskRow({ task, onToggle, busy }) {
  const done = task.status === "done";
  return (
    <div className={`card task ${done ? "done" : ""}`}>
      <input
        type="checkbox"
        checked={done}
        disabled={busy || task.status === "dropped"}
        onChange={() => onToggle(task)}
        aria-label={done ? "reopen task" : "mark done"}
      />
      <div style={{ flex: 1 }}>
        <div className="row spread">
          <span className="task-title">{task.title}</span>
          {task.status !== "open" && <Badge value={task.status} />}
        </div>
        <div className="row">
          <span className="muted">created {relativeTime(task.created_at)}</span>
        </div>
        <Tags tags={task.tags} />
      </div>
    </div>
  );
}

export default function Tasks() {
  const [showResolved, setShowResolved] = useState(false);
  const [tasks, setTasks] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    const statuses = showResolved ? ["open", "done", "dropped"] : ["open"];
    Promise.all(statuses.map((s) => api.listTasks(s)))
      .then((results) => setTasks(results.flatMap((r) => r.tasks)))
      .catch((e) => setError(e.message));
  }, [showResolved]);

  useEffect(() => {
    setTasks(null);
    load();
  }, [load]);

  const toggle = async (task) => {
    setBusyId(task.id);
    const next = task.status === "done" ? "open" : "done";
    try {
      const d = await api.patchTask(task.id, { status: next });
      setTasks((ts) =>
        ts
          .map((t) => (t.id === task.id ? d.task : t))
          .filter((t) => showResolved || t.status === "open"),
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="view">
      <h2 className="view-title">Tasks</h2>
      <p className="toggle-line">
        <button onClick={() => setShowResolved(!showResolved)}>
          {showResolved ? "show open only" : "show done / dropped too"}
        </button>
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {tasks === null ? (
        <Loading />
      ) : tasks.length === 0 ? (
        <p className="empty">No {showResolved ? "" : "open "}tasks.</p>
      ) : (
        tasks.map((t) => (
          <TaskRow key={t.id} task={t} onToggle={toggle} busy={busyId === t.id} />
        ))
      )}
    </div>
  );
}
