import { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

function pickMime() {
  if (typeof MediaRecorder === "undefined") return null;
  for (const m of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return null;
}

/**
 * Capture composer: text input + record button.
 * Reports optimistic entries upward via onPending/onSettled so the feed can
 * show them; audio captures are polled until enriched/failed.
 */
export default function Composer({ onPending, onUpdatePending, onSettled, onError }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const recorderRef = useRef(null);
  const pollTimers = useRef([]);
  useEffect(
    () => () => {
      pollTimers.current.forEach(clearTimeout);
      if (recorderRef.current) {
        recorderRef.current.stream.getTracks().forEach((t) => t.stop());
      }
    },
    [],
  );

  // `research` is not a mode the composer holds: it is which button was
  // pressed. There is no armed state to forget, mis-read, or reset.
  const submitText = async (e, research = false) => {
    e.preventDefault();
    const value = text.trim();
    if (!value || sending) return;
    setSending(true);
    const tempId = `pending-${Date.now()}`;
    onPending({ id: tempId, label: value, status: "processing" });
    setText("");
    try {
      const result = await api.captureText(value, { research });
      // A kickoff that only ever failed into the server log looks exactly
      // like one that was never asked for, so say which happened.
      if (result?.research?.error) {
        onError(`captured, but research did not start: ${result.research.error}`);
      }
      onSettled(tempId); // enriched note now exists; feed refetch shows it
    } catch (err) {
      onSettled(tempId);
      onError(err.message);
      setText(value); // give the text back
    } finally {
      setSending(false);
    }
  };

  const pollCapture = (captureId, tempId, attempt = 0) => {
    if (attempt > 60) {
      onSettled(tempId);
      onError("audio capture is taking too long — check the feed later");
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const d = await api.getCapture(captureId);
        const status = d.capture.status;
        if (status === "enriched") {
          onSettled(tempId);
        } else if (status === "failed") {
          onSettled(tempId);
          onError(`audio enrichment failed: ${d.capture.error || "unknown error"}`);
        } else {
          onUpdatePending(tempId, { status });
          pollCapture(captureId, tempId, attempt + 1);
        }
      } catch (err) {
        onSettled(tempId);
        onError(err.message);
      }
    }, 2000);
    pollTimers.current.push(timer);
  };

  const startRecording = async () => {
    const mime = pickMime();
    if (!mime) {
      onError("this browser does not support audio recording");
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      onError("microphone access denied");
      return;
    }
    const recorder = new MediaRecorder(stream, { mimeType: mime });
    const chunks = [];
    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      recorderRef.current = null;
      setRecording(false);
      const blob = new Blob(chunks, { type: mime });
      if (blob.size === 0) {
        onError("empty recording");
        return;
      }
      const contentType = mime.split(";")[0]; // server keys on the bare type
      const tempId = `pending-${Date.now()}`;
      onPending({ id: tempId, label: "🎙 voice capture", status: "uploading" });
      try {
        // A voice note is captured, not researched: there is no armed state
        // to carry across the recording, and the note it becomes can be
        // researched from its own page.
        const d = await api.captureAudio(blob, contentType);
        onUpdatePending(tempId, { status: "pending" });
        pollCapture(d.id, tempId);
      } catch (err) {
        onSettled(tempId);
        onError(err.message);
      }
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  };

  const stopRecording = () => {
    recorderRef.current?.stop();
  };

  return (
    <div className="composer">
      <form className="composer-inner" onSubmit={submitText}>
        <button
          type="button"
          className={`icon-btn ${recording ? "recording" : ""}`}
          onClick={recording ? stopRecording : startRecording}
          title={recording ? "stop recording" : "record a voice note"}
          aria-label={recording ? "stop recording" : "record a voice note"}
        >
          {recording ? "■" : "🎙"}
        </button>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={recording ? "recording… tap ■ to send" : "Capture a thought…"}
          disabled={sending}
        />
        <div className="composer-actions">
          <button
            type="submit"
            className="composer-send"
            disabled={sending || !text.trim()}
            title="capture"
          >
            {sending ? <span className="spinner" /> : "Send"}
          </button>
          {/* Deliberately quiet, and deliberately not a peer of Send: it
              spends real money, so it should be reachable without being the
              thing your thumb finds first. */}
          <button
            type="button"
            className="composer-research"
            disabled={sending || !text.trim()}
            onClick={(e) => submitText(e, true)}
            title="capture this and research it in the background"
          >
            research
          </button>
        </div>
      </form>
    </div>
  );
}
