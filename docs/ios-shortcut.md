# Voice capture (iOS Shortcut)

Dictate a thought on the go → audio POSTed to the deployed service → Gemini
transcribes and enriches it server-side into a note with tasks.

## Endpoint

`POST https://YOUR-SERVICE-URL/api/v1/capture/audio`

- Body: raw audio bytes (AAC-in-MP4 as recorded by iOS)
- Headers:
  - `Authorization: Bearer <phone device key>`
  - `Content-Type: audio/mp4`
  - `X-Memex-Source: ios`
  - `X-Memex-Research: 1` (optional) — ask for a background research run on
    this capture. Only this header starts one; nothing the model reads out of
    the audio can (docs/contracts.md). Leave it off for ordinary captures, or
    make a second shortcut that sends it for "dig into this" notes.
- Response: `202 {"id": "<capture id>"}` — enrichment is asynchronous
  (GCS → Eventarc → Gemini, ~10 s); the note then appears in the feed.

The phone key lives in Secret Manager (and your password manager):

```bash
gcloud secrets versions access latest --secret memex-device-keys \
  --project YOUR_PROJECT_ID | jq -r .phone
```

## Shortcut steps

1. **Shortcuts app → New Shortcut → Add Action**
2. **Record Audio**
   - Audio Quality: Normal
   - Start Recording: On Tap
   - Stop Recording: On Tap
3. **Get Contents of URL**
   - URL: `https://YOUR-SERVICE-URL/api/v1/capture/audio`
   - Method: `POST`
   - Headers:
     - `Authorization`: `Bearer <phone key>` (literal word "Bearer", space, key)
     - `Content-Type`: `audio/mp4`
     - `X-Memex-Source`: `ios`
   - Request Body: **File** → select the recorded audio variable
4. **Show Notification** (optional)
   - Title: "memex"
   - Body: `Get Dictionary Value` → key `id` (the capture is still enriching;
     the note shows up in the web UI shortly after)

## Mounting it

- **Action Button** (iPhone 15 Pro+): Settings → Action Button → Shortcut →
  pick this one. One press, talk, tap to stop.
- **Lock Screen widget**: long-press lock screen → Customize → add Shortcuts
  widget.
- **Home Screen icon**: in Shortcuts, share → Add to Home Screen.

## Smoke test from desktop

The same request shape, verified working 2026-08-27:

```bash
ffmpeg -f pulse -i default -t 5 -c:a aac /tmp/test.m4a
# key goes through a file, not argv (argv is readable in /proc)
hdr=$(umask 077 && mktemp)
gcloud secrets versions access latest --secret memex-device-keys \
  --project YOUR_PROJECT_ID | jq -r '"Authorization: Bearer " + .phone' > "$hdr"
curl -X POST "https://YOUR-SERVICE-URL/api/v1/capture/audio" \
  -H "@$hdr" \
  -H "Content-Type: audio/mp4" \
  -H "X-Memex-Source: ios" \
  --data-binary @/tmp/test.m4a
rm -f "$hdr"
```

## Notes

- Accepted content types: `audio/mp4` (m4a), `audio/wav`, `audio/ogg`,
  `audio/webm`. Anything else is a 415.
- An unrecognized `X-Memex-Source` falls back to `api`; the device identity
  comes from the bearer key regardless.
- If enrichment fails, the capture's `status` shows `failed` at
  `GET /api/v1/captures/<id>` — nothing surfaces in the feed.
