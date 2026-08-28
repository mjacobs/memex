import { useCallback, useEffect, useState } from "react";
import { api, relativeTime } from "../api.js";
import { Badge, ErrorBanner, Loading, Tags, TagFilterBar } from "../components.jsx";

function TaskRow({ task, onToggle, busy, selectedTags, onTagClick }) {
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
        <Tags tags={task.tags} selected={selectedTags} onTagClick={onTagClick} />
      </div>
    </div>
  );
}

export default function Tasks() {
  const [showResolved, setShowResolved] = useState(false);
  const [tasks, setTasks] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [selectedTags, setSelectedTags] = useState(new Set());

  const toggleTag = (tag) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const visibleTasks =
    tasks && selectedTags.size > 0
      ? tasks.filter((t) => t.tags && [...selectedTags].every((tag) => t.tags.includes(tag)))
      : tasks;

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
      <TagFilterBar
        selected={selectedTags}
        onRemove={toggleTag}
        onClear={() => setSelectedTags(new Set())}
      />
      {tasks === null ? (
        <Loading />
      ) : visibleTasks.length === 0 ? (
        <p className="empty">
          {selectedTags.size > 0
            ? "No tasks match this tag filter."
            : `No ${showResolved ? "" : "open "}tasks.`}
        </p>
      ) : (
        visibleTasks.map((t) => (
          <TaskRow
            key={t.id}
            task={t}
            onToggle={toggle}
            busy={busyId === t.id}
            selectedTags={selectedTags}
            onTagClick={toggleTag}
          />
        ))
      )}
    </div>
  );
}
