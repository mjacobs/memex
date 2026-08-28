# memex contracts (W0 — frozen)

This is the contract W1–W4 build against. Change it only by editing this file
first, in its own commit, with a reason. Everything else (module layout,
internals) is workstream-local.

Verified facts this contract sits on (all proved 2026-08-27 against project
`m4tt-xyz`):

- `gemini-3.5-flash` on Vertex (location `global`) accepts **inline m4a audio**
  (`Part.from_bytes`, `mime_type="audio/mp4"`) plus a prompt, and honors
  `response_json_schema` — one call returned
  `{transcript, summary, tags[], action_items[{title}]}` correctly.
  No separate STT service.
- Vertex model roster includes `gemini-3.5-flash` (our default; override with
  `MEMEX_MODEL`).
- ADC works locally; `aiplatform`, `run`, `eventarc`, `storage` APIs already
  enabled on `m4tt-xyz` (Firestore, Scheduler, Secret Manager enabled by
  terraform in W1).

Decisions made with Matt (2026-08-27): deploy into **`m4tt-xyz`**; frontend is
**Vite + React SPA**; routine output lands **both** as a feed note and in a
routine-runs view; approval-queue actions at launch are **task mutations only**.
Region default: `us-central1` (tfvar; confirm with Matt before first
`terraform apply`).

## System invariants

- One public Cloud Run service: FastAPI + ADK runner + static frontend,
  `min-instances=0`. **No background work after a response returns** (CPU is
  throttled between requests): text capture enriches synchronously in-request;
  audio enriches inside the Eventarc-delivered request.
- Firestore (native mode) is the system of record. Memory Bank, if it ever
  appears, sits behind a noop-degradable adapter and is never load-bearing.
- Client auth: `Authorization: Bearer <device-key>`. Keys live in one Secret
  Manager secret `memex-device-keys`, JSON `{"<device_id>": "<key>", ...}`,
  loaded at startup (env `MEMEX_DEVICE_KEYS_JSON` for local dev). No IAP.
- Internal endpoints (`/internal/*`) reject bearer keys and instead verify a
  Google-signed **OIDC token** (audience = service URL) from the Eventarc /
  Cloud Scheduler service accounts.
- IDs are lowercase ULIDs (sortable == feed order). Timestamps are UTC ISO-8601
  strings in API JSON, Firestore native timestamps in documents.

## Firestore schema

Single database, five collections. Fields marked `?` are optional/nullable.

### `captures/{id}`

Raw inbound payloads; immutable except `status`/enrichment linkage.

| field           | type                                       | notes                                    |
| --------------- | ------------------------------------------ | ---------------------------------------- |
| `id`            | ulid                                       | doc id                                   |
| `created_at`    | timestamp                                  |                                          |
| `source`        | `"ios" \| "desktop" \| "web" \| "api"`     | free-form fallback `"api"`               |
| `device_id`     | string                                     | from the bearer key that authenticated   |
| `kind`          | `"text" \| "audio" \| "image" \| "link"`   |                                          |
| `text?`         | string                                     | kind=text: the text; kind=image: optional caption; kind=link: the user's note |
| `url?`          | string                                     | kind=link, http(s) only                  |
| `audio_gcs_uri?`| string                                     | kind=audio, `gs://…`                     |
| `audio_mime?`   | string                                     | e.g. `audio/mp4`, `audio/wav`            |
| `image_gcs_uri?`| string                                     | kind=image, `gs://…` (same bucket/prefix as audio) |
| `image_mime?`   | string                                     | e.g. `image/png`, `image/jpeg`           |
| `source_url?`   | string                                     | kind=image, page the screenshot came from |
| `title?`        | string                                     | kind=image/link, page title as the client reported it |
| `status`        | `"pending" \| "processing" \| "enriched" \| "failed"` | audio/image start `pending`   |
| `error?`        | string                                     | status=failed                            |
| `note_id?`      | ulid                                       | set when enrichment lands                |

### `notes/{id}`

The feed. Both enriched captures and routine output.

| field            | type                                        | notes                                   |
| ---------------- | ------------------------------------------- | --------------------------------------- |
| `id`             | ulid                                        |                                         |
| `created_at`     | timestamp                                   |                                         |
| `kind`           | `"capture" \| "digest" \| "review" \| "link"` | digest/review written by routines; link = a saved read-later page |
| `capture_id?`    | ulid                                        | kind=capture/link                       |
| `routine_run_id?`| ulid                                        | kind=digest/review                      |
| `transcript?`    | string                                      | audio captures                          |
| `body`           | string                                      | canonical text (original text, transcript, image description + caption + source link, or routine markdown) |
| `summary`        | string                                      |                                         |
| `tags`           | string[]                                    | lowercase kebab, normalized on write by the model layer |
| `task_ids`       | ulid[]                                      | tasks extracted from this note          |
| `trace`          | array<event>                                | agent events for the turn that produced it, plus a `role:"user"` event per owner edit (see Trace) |

### `tasks/{id}`

| field            | type                                        | notes                     |
| ---------------- | ------------------------------------------- | ------------------------- |
| `id`             | ulid                                        |                           |
| `title`          | string                                      |                           |
| `status`         | `"open" \| "done" \| "dropped"`             |                           |
| `created_at`     | timestamp                                   |                           |
| `updated_at`     | timestamp                                   | touch on every mutation   |
| `tags`           | string[]                                    |                           |
| `source_note_id?`| ulid                                        |                           |

### `approvals/{id}`

HITL queue. Launch scope: task mutations only.

| field         | type                                                        | notes                          |
| ------------- | ----------------------------------------------------------- | ------------------------------ |
| `id`          | ulid                                                        |                                |
| `created_at`  | timestamp                                                   |                                |
| `status`      | `"pending" \| "approved" \| "rejected"`                     | `approved` implies applied     |
| `action`      | object (see Action)                                         |                                |
| `reason`      | string                                                      | agent's one-line justification |
| `routine_run_id?` | ulid                                                    | who proposed it                |
| `resolved_at?`| timestamp                                                   |                                |
| `result?`     | string                                                      | what applying did / error      |

**Action** (discriminated on `type`):

```json
{"type": "task_update", "task_id": "…", "changes": {"status?": "…", "title?": "…", "tags?": []}}
{"type": "task_create", "task": {"title": "…", "tags?": []}}
```

Approving applies the action server-side in the same request, then sets
`status=approved`, `result`. Direct user edits via `PATCH /tasks/{id}` or
`PATCH /notes/{id}` do NOT go through approvals — the queue is only for
**agent-proposed** mutations from routines.

Deleting a note is a hard delete and does **not** cascade: tasks keep their
`source_note_id` and captures keep their `note_id`, so both may point at a
note that no longer exists. Readers must tolerate a 404 on those ids.

### `routine_runs/{id}`

| field        | type                                          | notes                              |
| ------------ | --------------------------------------------- | ---------------------------------- |
| `id`         | ulid                                          |                                    |
| `routine`    | `"daily_review" \| "nightly_digest"`          |                                    |
| `fired_at`   | timestamp                                     |                                    |
| `status`     | `"running" \| "succeeded" \| "failed"`        |                                    |
| `summary?`   | string                                        | agent's final reply                |
| `note_id?`   | ulid                                          | digest/review note it wrote        |
| `approval_ids` | ulid[]                                      | approvals it queued                |
| `trace`      | array<event>                                  | full agent session (see Trace)     |
| `error?`     | string                                        |                                    |

### Trace (replayability)

`trace` is an ordered array of compact event objects, enough to replay the
session in the UI without Vertex session storage:

```json
{"t": "2026-08-27T21:00:00Z", "role": "user" | "model" | "tool",
 "text?": "…", "tool?": "create_tasks", "args?": {…}, "result?": {…}}
```

## HTTP API

All non-`/internal` routes require the bearer key. Errors:
`{"error": {"code": "<snake_case>", "message": "…"}}` with a matching HTTP
status. Static frontend served at `/` (SPA fallback); API under `/api/v1`.

| method & path                        | req                                                | resp                                       |
| ------------------------------------ | -------------------------------------------------- | ------------------------------------------ |
| `POST /api/v1/capture`               | `{"text": "...", "source?": "..."}`                | `201 {capture, note, tasks}` — sync enrich |
| `POST /api/v1/capture/link`          | `{"url": "...", "title?": "...", "note?": "..."}`  | `201 {capture, note, tasks}` — sync enrich |
| `POST /api/v1/capture/links`         | `{"links": [{url, title?, note?}], "source?": "..."}` (max 20) | `201 {results: [{url, capture, note, tasks} \| {url, error}]}` — one bad entry never fails the batch; `url` is `null` if the entry had none |
| `POST /api/v1/capture/audio`         | raw audio body; `Content-Type: audio/*`; `X-Memex-Source?` | `202 {"id": "<capture_id>"}` — GCS upload only |
| `POST /api/v1/capture/image`         | `{"image_base64", "mime", "text?", "source_url?", "title?", "source?"}` | `202 {"id": "<capture_id>"}` — GCS upload only, max 10 MiB |
| `GET /api/v1/captures/{id}`          |                                                    | `200 {capture}` (poll for audio/image status) |
| `GET /api/v1/captures/{id}/image`    | kind=image only                                    | `200` raw image bytes (`Content-Type` = `image_mime`) |
| `GET /api/v1/notes`                  | `?limit=50&before=<ulid>&tag=&kind=`               | `200 {notes: […]}` newest-first            |
| `GET /api/v1/notes/{id}`             |                                                    | `200 {note}` incl. `trace`; plus `image_url` for image captures |
| `PATCH /api/v1/notes/{id}`           | `{"summary?", "body?", "tags?"}` (unknown fields 422) | `200 {note}`; appends a `role:"user"` trace event |
| `DELETE /api/v1/notes/{id}`          |                                                    | `200 {"deleted": "<id>"}` hard delete      |
| `GET /api/v1/tasks`                  | `?status=open` (default open)                      | `200 {tasks: […]}`                         |
| `PATCH /api/v1/tasks/{id}`           | `{"status?", "title?", "tags?"}`                   | `200 {task}`                               |
| `GET /api/v1/approvals`              | `?status=pending` (default pending)                | `200 {approvals: […]}`                     |
| `POST /api/v1/approvals/{id}/approve`|                                                    | `200 {approval}` (applied)                 |
| `POST /api/v1/approvals/{id}/reject` |                                                    | `200 {approval}`                           |
| `GET /api/v1/routines/runs`          | `?limit=20`                                        | `200 {runs: […]}` (traces elided)          |
| `GET /api/v1/routines/runs/{id}`     |                                                    | `200 {run}` incl. `trace`                  |
| `GET /health` (alias `/healthz`)     | no auth                                            | `200 {"ok": true}`                         |

Internal (OIDC-verified, no bearer):

| method & path                          | caller                | behavior                                                        |
| -------------------------------------- | --------------------- | --------------------------------------------------------------- |
| `POST /internal/enrich`                | Eventarc (GCS finalize CloudEvent) | map object → capture, run enrichment turn in-request, write note/tasks |
| `POST /internal/routines/{routine}/tick` | Cloud Scheduler     | `routine ∈ {daily_review, nightly_digest}`; run the routine agent session in-request, write `routine_runs` doc |

Audio object naming: `gs://<bucket>/captures/<capture_id>.<ext>` — the
finalize event's object name is the capture id.

Link captures are enriched from the URL, title, and user note **only** — the
server never fetches a client-supplied URL. The note body's first line is a
markdown link to the page, and `read-later` is always among its tags. In the
batch form each link succeeds or fails independently: a rejected URL or a
failed enrichment is reported in that link's own result, and the request still
returns `201`. A link saved without a user note produces no tasks: only the
user's own words can ask for one, since a URL and a page title are text the
site chose.

Entity JSON in responses mirrors the Firestore schema (timestamps as ISO
strings, `trace` only on detail endpoints).

### Accepted risk: a screenshot can put a task on your list

Capturing a screenshot and getting its to-dos back is the point of image
capture, so a screenshot of a page is allowed to produce tasks. That means a
page you screenshot could word itself to produce a task you did not ask for.
We accept it, bounded like this:

- a task is an inert title in your list. Nothing acts on it — routines can
  only *propose* task changes, and every proposal goes to the approval queue;
- the enrichment prompts state that captured material is content to describe,
  never instructions, and each metadata field is flattened to one line so a
  page title cannot pose as the user's own note;
- a link saved with no note of your own produces no tasks at all, since
  nothing in a bare URL and a site-chosen title is you asking for anything.

Closing it fully would mean extracting tasks from a separate model call over
the user's caption alone — a real option if this ever bites, at the cost of a
second call per capture and of screenshots that no longer yield their to-dos.

## Agent tool signatures (ADK function tools)

Python signatures; all return plain dicts. These are the only writes the model
can make.

```python
def create_note(kind: str, body: str, summary: str, tags: list[str],
                transcript: str | None = None, capture_id: str | None = None,
                routine_run_id: str | None = None) -> dict  # {note_id}

def create_tasks(tasks: list[dict], source_note_id: str) -> dict
    # tasks: [{title, tags?}] → {task_ids: […]}

def list_tasks(status: str = "open", limit: int = 100) -> dict  # {tasks}

def update_task(task_id: str, changes: dict) -> dict
    # ONLY callable from capture enrichment; routines must use queue_approval

def list_recent_notes(limit: int = 50, days: int | None = None) -> dict

def queue_approval(action: dict, reason: str) -> dict  # {approval_id}
    # action per the Action contract; validated before writing
```

Enrichment (capture path) is a **single structured-output call** (the verified
schema above), not a tool loop — tools are for the routine sessions. The
enrichment result is written to Firestore by application code, and the `trace`
records the call.

Routine prompts (W3-owned, shapes fixed):

- `daily_review`: reads open tasks (`list_tasks`), flags stale items,
  queues `task_update` approvals via `queue_approval`, writes a `review` note
  (`create_note`) summarizing.
- `nightly_digest`: reads the last 24 h of notes (`list_recent_notes`),
  consolidates themes, writes a `digest` note. May queue approvals for obvious
  dupes.

## Repo layout (scaffolded in W0)

```
memex/            Python package: api/ (FastAPI routers), agent/ (ADK, tools,
                  prompts), store/ (Firestore + GCS), config.py
web/              Vite + React SPA (pnpm), built into memex/static/ for deploy
terraform/        W1-owned
tests/            contract-level pytest (API + tool I/O against emulator;
                  no LLM-output asserts)
Makefile          dev / test / build / deploy loop
```

Local dev: Firestore emulator (`gcloud emulators firestore`), real Vertex via
ADC. `MEMEX_DEVICE_KEYS_JSON='{"dev": "dev-key"}'` for auth.
