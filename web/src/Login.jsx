import { useState } from "react";
import { getKey, setKey } from "./api.js";

export default function Login({ onDone, reason }) {
  const [value, setValue] = useState(getKey());

  const submit = (e) => {
    e.preventDefault();
    if (!value.trim()) return;
    setKey(value.trim());
    onDone();
  };

  return (
    <div className="login">
      <h1>
        mem<span style={{ color: "var(--accent)" }}>ex</span>
      </h1>
      <p className="muted">
        {reason === "unauthorized"
          ? "That device key was rejected. Enter a valid one to continue."
          : "Enter your device key to connect."}
      </p>
      <form onSubmit={submit}>
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="device key"
          autoFocus
          autoComplete="off"
        />
        <button className="btn primary" type="submit" disabled={!value.trim()}>
          Connect
        </button>
      </form>
    </div>
  );
}
