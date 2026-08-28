#!/usr/bin/env bash
################################################################################
# memex-capture-voice — push-to-talk-ish voice capture for memex (v1).
#
# Single hotkey, toggle behavior:
#   1st press: starts recording from the default PulseAudio source.
#   2nd press: stops recording and POSTs the wav to the deployed service
#              (POST /api/v1/capture/audio). Transcription + enrichment happen
#              server-side (Gemini hears the audio natively) — no local
#              transcription daemon involved.
#
# Also usable non-interactively:
#   memex-capture-voice.sh --file recording.wav   # POST an existing audio file
#
# Designed to be bound to a global KDE shortcut. See docs/desktop-capture.md.
#
# v0's stop-path bug is fixed here: ffmpeg runs directly in the background (no
# setsid), so the pidfile holds ffmpeg's own PID, the liveness check confirms
# the PID is still ffmpeg (guards against PID reuse), and -t caps a runaway
# recording at 10 minutes regardless.
################################################################################
set -euo pipefail

# ~/.secrets is sourced for MEMEX_DESKTOP_KEY. It also exports a v0-era
# MEMEX_URL (the retired Cloudflare worker) into every shell, so the v1
# endpoint deliberately uses its own variable name: MEMEX_V1_URL.
[[ -f "$HOME/.secrets" ]] && source "$HOME/.secrets"
MEMEX_URL="${MEMEX_V1_URL:-https://memex-PROJECT_NUMBER.us-central1.run.app}"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
PIDFILE="$RUNTIME_DIR/memex-voice.pid"
WAVFILE="$RUNTIME_DIR/memex-voice.wav"
KEYCACHE="$RUNTIME_DIR/memex-desktop.key"
MAX_SECONDS=600

GUI=1 # --file mode clears this so CLI runs stay off the desktop

notify_err() {
  if [[ "$GUI" == 1 ]] && command -v kdialog >/dev/null && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    kdialog --error "$1"
  else
    echo "memex-capture-voice: $1" >&2
  fi
}

notify_ok() {
  if [[ "$GUI" == 1 ]] && command -v kdialog >/dev/null && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    kdialog --title "memex" --passivepopup "$1" 4
  else
    echo "memex-capture-voice: $1"
  fi
}

# Device bearer key: env > ~/.secrets (MEMEX_DESKTOP_KEY) > cached Secret
# Manager fetch (needs gcloud auth; cached so the hotkey path stays fast).
device_key() {
  if [[ -n "${MEMEX_DESKTOP_KEY:-}" ]]; then
    printf '%s' "$MEMEX_DESKTOP_KEY"
    return
  fi
  if [[ -s "$KEYCACHE" ]]; then
    cat "$KEYCACHE"
    return
  fi
  local key
  key=$(gcloud secrets versions access latest --secret memex-device-keys \
    --project m4tt-xyz 2>/dev/null | jq -r '.desktop // empty') || true
  if [[ -z "$key" ]]; then
    notify_err "no device key: set MEMEX_DESKTOP_KEY in ~/.secrets or run gcloud auth login"
    exit 1
  fi
  (umask 077 && printf '%s' "$key" > "$KEYCACHE")
  printf '%s' "$key"
}

post_audio() {
  local file="$1" mime="$2" response
  response=$(
    curl -sS --fail-with-body -X POST "$MEMEX_URL/api/v1/capture/audio" \
      -H "Authorization: Bearer $(device_key)" \
      -H "Content-Type: $mime" \
      -H "X-Memex-Source: desktop" \
      --data-binary "@$file" 2>&1
  ) || {
    notify_err "memex capture failed: $response"
    exit 1
  }
  local capture_id
  capture_id=$(printf '%s' "$response" | jq -r '.id // "?"')
  notify_ok "captured ($capture_id) — enriching in the cloud"
}

mime_for() {
  case "${1##*.}" in
    wav) echo audio/wav ;;
    m4a | mp4) echo audio/mp4 ;;
    ogg | oga | opus) echo audio/ogg ;;
    webm) echo audio/webm ;;
    *)
      notify_err "unsupported audio extension: $1"
      exit 1
      ;;
  esac
}

start_recording() {
  command -v ffmpeg >/dev/null || {
    notify_err "ffmpeg not installed"
    exit 1
  }
  rm -f "$WAVFILE"
  # 16 kHz mono keeps uploads small; -t is the runaway-recording failsafe.
  ffmpeg -nostdin -hide_banner -loglevel error \
    -f pulse -i default -ac 1 -ar 16000 -t "$MAX_SECONDS" -y "$WAVFILE" \
    >/dev/null 2>&1 &
  echo $! > "$PIDFILE"
  notify_ok "🔴 recording — tap again to stop"
}

# True only if the pidfile PID is alive AND still an ffmpeg process.
recording_pid() {
  local pid
  [[ -f "$PIDFILE" ]] || return 1
  pid=$(<"$PIDFILE")
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null || return 1
  [[ "$(cat "/proc/$pid/comm" 2>/dev/null)" == "ffmpeg" ]] || return 1
  printf '%s' "$pid"
}

stop_recording_and_capture() {
  local pid="$1"
  rm -f "$PIDFILE"
  # SIGINT lets ffmpeg flush the wav cleanly; SIGTERM truncates it.
  kill -INT "$pid" 2>/dev/null || true
  for _ in $(seq 1 50); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done

  if [[ ! -s "$WAVFILE" ]]; then
    notify_err "empty recording"
    exit 1
  fi
  post_audio "$WAVFILE" audio/wav
}

if [[ "${1:-}" == "--file" ]]; then
  GUI=0
  [[ -s "${2:-}" ]] || {
    notify_err "usage: $0 --file <audio file>"
    exit 1
  }
  post_audio "$2" "$(mime_for "$2")"
  exit 0
fi

if pid=$(recording_pid); then
  stop_recording_and_capture "$pid"
else
  rm -f "$PIDFILE"
  start_recording
fi
