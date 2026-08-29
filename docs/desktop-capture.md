# Desktop voice capture

`scripts/memex-capture-voice.sh` is a toggle-style voice recorder for the
deployed memex service. One hotkey press starts recording from the default
PulseAudio microphone; the second press stops it and POSTs the wav to
`POST /api/v1/capture/audio`. Transcription and enrichment happen server-side
(Gemini hears the audio natively), so there is no local transcription
dependency — unlike v0, which needed the Parakeet daemon.

## Setup

1. Device key: put the `desktop` bearer key (Secret Manager
   `memex-device-keys`, and in your password manager) in `~/.secrets`:

   ```bash
   export MEMEX_DESKTOP_KEY=<key>
   ```

   Without it the script falls back to fetching the key with `gcloud` and
   caches it at `$XDG_RUNTIME_DIR/memex-desktop.key` (mode 0600, gone on
   logout).

2. Endpoint: defaults to the deployed Cloud Run URL. Override with
   `MEMEX_V1_URL` if the deployment moves. (The variable is deliberately NOT
   `MEMEX_URL` — `~/.secrets` still exports that name for the retired v0
   Cloudflare worker, and it must not leak into this client.)

3. Bind a KDE global shortcut: System Settings → Shortcuts → Custom Shortcuts
   (or `kmenuedit`), command:

   ```
   <absolute path to your checkout>/scripts/memex-capture-voice.sh
   ```

   Suggested key: Meta+V. Both the start and stop press run the same command;
   the script toggles on its pidfile.

## Behavior

- Recording is 16 kHz mono wav under `$XDG_RUNTIME_DIR`, capped at 10 minutes
  (`-t 600`) as a runaway failsafe — the v0 script once left a mic recording
  running for days.
- The pidfile stores ffmpeg's own PID (no `setsid` indirection, which is what
  broke v0's stop path), and the stop path double-checks the PID still names
  an ffmpeg process before signalling it.
- kdialog passive popups report state when a display is available; on a bare
  shell the script prints to stdout/stderr instead.

## Non-interactive use

POST an existing audio file (wav/m4a/ogg/webm) without touching the mic:

```bash
scripts/memex-capture-voice.sh --file recording.wav
```

Useful for testing and for feeding recordings made elsewhere.
