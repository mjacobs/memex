#!/usr/bin/env bash
# Smoke-test the deployed capture path end to end.
#   MEMEX_URL=https://memex-<project#>.us-central1.run.app \
#   MEMEX_KEY=<device key> scripts/smoke.sh
set -euo pipefail

: "${MEMEX_URL:?set MEMEX_URL to the deployed service URL}"
: "${MEMEX_KEY:?set MEMEX_KEY to a device bearer key}"
auth=(-H "Authorization: Bearer ${MEMEX_KEY}")

say() { printf '\n== %s\n' "$*"; }

say "health"
curl -fsS "${MEMEX_URL}/health"

say "text capture (sync enrichment)"
note_id=$(curl -fsS "${auth[@]}" -H 'Content-Type: application/json' \
  -X POST "${MEMEX_URL}/api/v1/capture" \
  -d '{"text": "Smoke test: remind me to water the plants tomorrow", "source": "api"}' |
  python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["note"]["id"])')
echo "note: ${note_id}"

say "note has summary + tags + trace"
curl -fsS "${auth[@]}" "${MEMEX_URL}/api/v1/notes/${note_id}" |
  python3 -c 'import json,sys; n=json.load(sys.stdin)["note"]; assert n["summary"] and n["trace"], n; print("summary:", n["summary"][:80]); print("tags:", n["tags"], "tasks:", len(n["task_ids"]))'

say "audio capture (async via Eventarc)"
wav=$(mktemp --suffix=.wav)
trap 'rm -f "${wav}"' EXIT
if command -v espeak-ng >/dev/null; then
  espeak-ng -w "${wav}" "Smoke test audio: schedule the dentist appointment for next week."
else
  echo "espeak-ng not found; skipping audio leg" && exit 0
fi
cap_id=$(curl -fsS "${auth[@]}" -H 'Content-Type: audio/wav' \
  -H 'X-Memex-Source: api' --data-binary @"${wav}" \
  -X POST "${MEMEX_URL}/api/v1/capture/audio" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "capture: ${cap_id} — polling for enrichment (Eventarc)"

for i in $(seq 1 30); do
  status=$(curl -fsS "${auth[@]}" "${MEMEX_URL}/api/v1/captures/${cap_id}" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["capture"]["status"])')
  printf '  %s status=%s\n' "$i" "${status}"
  [[ "${status}" == "enriched" ]] && { echo "AUDIO PATH OK"; exit 0; }
  [[ "${status}" == "failed" ]] && { echo "AUDIO PATH FAILED" >&2; exit 1; }
  sleep 5
done
echo "timed out waiting for audio enrichment" >&2
exit 1
