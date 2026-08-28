# memex

Speak a thought at your phone or desktop; seconds later it's a searchable
note with extracted tasks, and every night an agent reviews the backlog and
proposes cleanups you approve with one click. memex is a voice/text capture
agent built for the All Things Agentic Hackathon (Taskmaster track), running
entirely on Google Cloud with everything scaled to zero when idle.

![Architecture](docs/architecture.png)

## What it does

- **Capture from anywhere.** An iOS Shortcut, a desktop hotkey, or the web UI
  POSTs text or raw audio. Gemini hears the audio natively — there is no
  separate speech-to-text service.
- **Enrichment.** One structured Gemini call turns a capture into a note:
  transcript, summary, tags, and extracted tasks, with the full model trace
  stored on the note.
- **Scheduled routines as real agent sessions.** Cloud Scheduler fires a
  daily task review (09:00) and a nightly digest (03:00). Each run is a
  multi-step Gemini tool loop over the note/task corpus, and each run's
  complete trace is stored and replayable in the UI.
- **Human in the loop.** Routines don't mutate your tasks. They queue
  approvals ("drop these five duplicate tasks", "set this due date") that you
  accept or reject in the web UI; only an accepted approval applies the
  change.

## How it's built

One Cloud Run container serves everything: a FastAPI API, the agent
(Google ADK + Gemini on Vertex AI), and the built React SPA. State lives in
Firestore (ULID-keyed notes, tasks, captures, approvals, routine runs) and a
GCS bucket for raw audio.

Two paths in:

1. **Text** — `POST /api/v1/capture` enriches synchronously and returns the
   note.
2. **Audio** — `POST /api/v1/capture/audio` stores the file in GCS and
   returns `202`; the GCS object-finalized event fires Eventarc, which pushes
   to an internal endpoint that runs enrichment (~10 s end to end).

Auth is deliberately simple for a single-user system: per-device bearer keys
held in Secret Manager. The `/internal/*` endpoints (Eventarc and Cloud
Scheduler push targets) verify Google-signed OIDC tokens against an audience
set plus a service-account invoker allowlist — nothing public can reach them.

All infrastructure is Terraform (`terraform/`): Cloud Run, Firestore with its
composite indexes, GCS, Eventarc, two Scheduler jobs, Secret Manager, and
least-privilege service accounts. Cloud Run runs min-instances=0, so an idle
deployment costs approximately nothing.

## Deploying

```bash
make build                  # build the React SPA into memex/static/
gcloud builds submit --project <project> \
  --tag us-central1-docker.pkg.dev/<project>/memex/memex:latest .
cd terraform && terraform apply   # first time: provisions everything
gcloud run deploy memex --region us-central1 \
  --image us-central1-docker.pkg.dev/<project>/memex/memex:latest
```

Add device keys as a JSON object (`{"phone": "...", "desktop": "...", "web":
"..."}`) to the `memex-device-keys` secret. Client setup:
[docs/ios-shortcut.md](docs/ios-shortcut.md) and
[docs/desktop-capture.md](docs/desktop-capture.md).

## Developing

```bash
make emulator   # Firestore emulator
make test       # pytest (53 tests) against the emulator
make api        # local API server
make web        # Vite frontend dev server
scripts/smoke.sh  # end-to-end smoke against a deployed instance
```

Design docs: [PLAN.md](PLAN.md) (spec and schedule),
[docs/contracts.md](docs/contracts.md) (frozen data model and API contracts).

## Lifted patterns (disclosure)

All code here is new for the hackathon, but two designs are consciously
borrowed:

- The routine "fire path" (scheduler → push endpoint → agent session with
  tools → stored trace) and the human-in-the-loop approval queue follow the
  long-horizon-harness pattern from Google's ADK samples (Project Horizon).
- The capture-first UX (one low-friction inbox, enrichment after the fact)
  carries over from an earlier personal project, serverless-memex.
