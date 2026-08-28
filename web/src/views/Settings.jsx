import { useState } from "react";
import { setKey } from "../api.js";
import { navigate } from "../router.js";

/** Rarely needed: swap the stored device key. The old key keeps working until
 * a new one is actually saved, so wandering in here costs nothing. */
export default function Settings({ onKeyChanged }) {
  const [value, setValue] = useState("");

  const save = (e) => {
    e.preventDefault();
    if (!value.trim()) return;
    setKey(value.trim());
    onKeyChanged();
    navigate("");
  };

  return (
    <div className="view">
      <button className="back-link" onClick={() => navigate("")}>
        ← feed
      </button>
      <h2 className="view-title">Settings</h2>
      <div className="section">
        <h3>Device key</h3>
        <p className="muted">
          This browser is connected with a device key. Saving a new one replaces
          it; leaving this page changes nothing.
        </p>
        <form onSubmit={save}>
          <input
            type="password"
            className="note-edit"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="new device key"
            autoComplete="off"
          />
          <div className="row note-edit-actions">
            <button className="btn primary" type="submit" disabled={!value.trim()}>
              Save
            </button>
            <button className="btn" type="button" onClick={() => navigate("")}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
