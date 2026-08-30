# memex contracts (W0 — frozen)

This is the contract W1–W4 build against. Change it only by editing this file
first, in its own commit, with a reason. Everything else (module layout,
internals) is workstream-local.

Verified facts this contract sits on (all proved 2026-08-27 against a real
GCP project):

- `gemini-3.5-flash` on Vertex (location `global`) accepts **inline m4a audio**
  (`Part.from_bytes`, `mime_type="audio/mp4"`) plus a prompt, and honors
  `response_json_schema` — one call returned
  `{transcript, summary, tags[], action_items[{title}]}` correctly.
  No separate STT service.
- Vertex model roster includes `gemini-3.5-flash` (our default; override with
  `MEMEX_MODEL`).
- ADC works locally; `aiplatform`, `run`, `eventarc`, `storage` APIs already
  enabled on the project (Firestore, Scheduler, Secret Manager enabled by
  terraform in W1).

Decisions made with Matt (2026-08-27): deploy into a single GCP project; frontend is
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

Single database, seven collections. Fields marked `?` are optional/nullable.

### `captures/{id}`

Raw inbound payloads; immutable except `status`/enrichment linkage.

| field           | type                                       | notes                                    |
| --------------- | ------------------------------------------ | ---------------------------------------- |
| `id`            | ulid                                       | doc id                                   |
| `created_at`    | timestamp                                  |                                          |
| `source`        | `"ios" \| "desktop" \| "web" \| "android" \| "api"` | free-form fallback `"api"`      |
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
| `research`      | bool                                       | the user asked for a background research run; default `false` |
| `status`        | `"pending" \| "processing" \| "enriched" \| "failed"` | audio/image start `pending`   |
| `error?`        | string                                     | status=failed                            |
| `note_id?`      | ulid                                       | set when enrichment lands                |

### `notes/{id}`

The feed. Both enriched captures and routine output.

| field            | type                                        | notes                                   |
| ---------------- | ------------------------------------------- | --------------------------------------- |
| `id`             | ulid                                        |                                         |
| `created_at`     | timestamp                                   |                                         |
| `kind`           | `"capture" \| "digest" \| "review" \| "link" \| "research"` | digest/review written by routines; link = a saved read-later page; research = a background research report |
| `capture_id?`    | ulid                                        | kind=capture/link                       |
| `routine_run_id?`| ulid                                        | kind=digest/review                      |
| `source_note_id?`| ulid                                        | kind=research: the note that asked for the research. Absent when the note is its own source (see merge, below) |
| `research_status?`| `"running" \| "completed" \| "failed"`     | a run against this note; absent means nobody asked |
| `research_operation_id?`| ulid                                  | which operation owns `research_status`; a superseded run's late write is ignored |
| `original_body?` | string                                      | merged research notes: what `body` said before the report replaced it |
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

Deleting a note is a hard delete of the note **and its originating capture**,
including the capture's GCS blob (screenshot or recording) — for an image note
the screenshot *is* the content, so "delete" must reclaim the bytes (decided
with Matt 2026-08-28). Blob deletion is best-effort: a blob already aged out
by lifecycle rules does not fail the request. Tasks are NOT cascaded: they
keep their `source_note_id`, which may point at a note that no longer exists —
deleting a note is not a retraction of the work it produced. Readers must
tolerate a 404 on those ids.

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

A `research` note's `body` is the report markdown, its tags include
`research-report`, and its `trace` holds the mapped Deep Research steps.
Enrichment path: after a capture note is written, if the capture carried
`research: true` → start a deep-research operation (create interaction, write
operation doc, enqueue first poll task).

**Where the report lands depends on why the run started.** A typed or spoken
capture that asked for research as it was written exists only to pose that
question, so a separate report note would be the same thing twice in the feed:
on completion the asking note *becomes* the report (`kind` → `research`, `body` → the
report, `source_note_id` absent because it is its own source) and what the
user wrote moves to `original_body`. Every other run leaves its note
alone and writes a second `research` note whose `source_note_id` points back
at it: `POST /notes/{id}/research`, chat's `start_research`, and link or image
captures that asked at capture time — a stashed page or a screenshot is a
thing in its own right, and consuming one into a report would take away what
was saved. The operation records which it is in `merge_into_source`.

`research_status` mirrors the operation onto the note it is about, so a client
can say a report is coming without joining against the operations queue.
`research_operation_id` records which run owns that status: a terminal write
from a run that has since been superseded is dropped rather than clearing a
newer run's claim, which would let the next tap buy an interaction that is
already running. A run
that fails never rewrites `body`: the note goes back to being the capture it
was, carrying `research_status: "failed"`. That guarantee is load-bearing for
the merge path, where there is no second note holding the user's words.

A note is `kind: "research"` only once it holds a report. While a merged run
is in flight the note keeps its original kind and carries
`research_status: "running"`, so a failed run has nothing to unwind.

**Only an explicit request starts a research run**, because a run spends real
money and ships the note to an external service. That request is the
capture's own `research` field, set by the client from an affordance the user
touched — never inferred from content. The enrichment model's tags classify a
note and nothing more: a `research` tag is an ordinary topic label, and a page
titled "Research this!" cannot spend a cent however the model reads it. The
field is optional and defaults to `false`, so a client that does not send it
simply never starts a run; the user can still ask for one from chat with
`start_research`. Same reasoning as the bare-link action-items gate — what a
website chose to say is not the user asking for anything.

The operation doc is written *before* the interaction is created, so the
durable record is always at least as old as the spend. A create that fails in
a way known to precede acceptance — a refused connection, a 4xx — releases the
note, because nothing was bought. Anything else (a timeout, a 5xx, a response
with no id) may have been accepted and billed, so the note stays claimed and
the handle-less operation is what eventually hands it back; releasing it there
is how an immediate retry buys a second report.

Failure to *start* must not fail the capture, but must not be silent either:
the sync capture response carries `research` — `{"operation_id": …}` when the
run started, `{"error": …}` when it could not — so a failed kickoff is
distinguishable from a note that never asked for research.

#### Known limits

Accepted tradeoffs, held after review rather than half-fixed. Each is the
cheaper horn of a dilemma the current substrate cannot dissolve: the
interactions API has no idempotency key, and nothing in production sweeps
running operations (kata `wz2g`).

- **An ambiguous create can buy a duplicate report.** A timeout, a 5xx, or an
  unparseable response may mean an accepted, billed run whose id never reached
  us. The note stays claimed for a few polls so an immediate retry cannot
  double-spend, then is handed back — a deliberate retry after that can buy a
  second report if the first was real. Holding the claim forever would make
  the note permanently un-researchable over what is usually a network blip.
  Closing this needs an idempotency key on the interactions API, or a way to
  list interactions and reconcile against the operation.
- **A lost first enqueue orphans a running operation.** The Cloud Tasks poll
  re-enqueues itself, so if every attempt at the *first* enqueue fails — or
  the process dies before it — nothing ever polls the operation, and the note
  stays claimed. The operation deliberately stays `running` rather than
  `failed`: visible, consistent with the note, and finishable by anything that
  later polls it, where a `failed` write would leave nothing to reconcile.
  `poll_running_operations()` is the sweep; wz2g adds the production
  scheduler that calls it.
- **A sustained store outage after the create can lose the handle.** The
  interaction id write is retried, but if every attempt fails the id survives
  only in the log, and the poll treats a real paid run as one that never
  started: the spend is real, the report is never collected. A durable outbox
  would close this; it is not worth building ahead of wz2g's sweep, which
  shrinks the window to the same fix.

### `operations/{id}`

Durable LRO queue (Firestore doc + Cloud Tasks self-rescheduling poll),
decoupled from any Cloud Run instance. Deep Research is the first kind.

| field            | type                                        | notes                                |
| ---------------- | ------------------------------------------- | ------------------------------------ |
| `id`             | ulid                                        |                                      |
| `kind`           | `"deep_research"`                           | future LROs add kinds here           |
| `status`         | `"running" \| "completed" \| "failed"`      |                                      |
| `created_at`     | timestamp                                   |                                      |
| `updated_at`     | timestamp                                   | touch on every mutation              |
| `interaction_id?`| string                                      | the aiplatform handle. Absent while the operation is a kickoff intent written *before* the interaction exists, and after a create whose outcome was never learned — such a run has nothing to poll and is given up on within a few polls |
| `source_note_id` | ulid                                        | the note that asked for research     |
| `result_note_id?`| ulid                                        | the `research` note, when completed; equals `source_note_id` when merged |
| `merge_into_source`| bool                                      | completion rewrites the asking note instead of adding one; set only by the capture-time path |
| `attempts`       | int                                         | poll count; cap ⇒ failed             |
| `error?`         | string                                      |                                      |

### `chat_sessions/{id}`

| field        | type            | notes                                          |
| ------------ | --------------- | ---------------------------------------------- |
| `id`         | ulid            |                                                |
| `created_at` | timestamp       |                                                |
| `updated_at` | timestamp       | touch on every appended turn                   |
| `title?`     | string          | first user message, truncated                  |
| `trace`      | array<event>    | same Trace format; the whole conversation — user turns, model text, tool calls/results |

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
| `POST /api/v1/capture`               | `{"text": "...", "source?": "...", "research?": false}` | `201 {capture, note, tasks, research?}` — sync enrich; `research` only when the capture asked for it |
| `POST /api/v1/capture/link`          | `{"url": "...", "title?": "...", "note?": "...", "research?": false}` | `201 {capture, note, tasks}` — sync enrich |
| `POST /api/v1/capture/links`         | `{"links": [{url, title?, note?, research?}], "source?": "..."}` (max 20) | `201 {results: [{url, capture, note, tasks} \| {url, error}]}` — one bad entry never fails the batch; `url` is `null` if the entry had none |
| `POST /api/v1/capture/audio`         | raw audio body; `Content-Type: audio/*`; `X-Memex-Source?`; `X-Memex-Research?` (`1`/`true`) | `202 {"id": "<capture_id>"}` — GCS upload only |
| `POST /api/v1/capture/image`         | `{"image_base64", "mime", "text?", "source_url?", "title?", "source?", "research?": false}` | `202 {"id": "<capture_id>"}` — GCS upload only, max 10 MiB |
| `GET /api/v1/captures/{id}`          |                                                    | `200 {capture}` (poll for audio/image status) |
| `GET /api/v1/captures/{id}/image`    | kind=image only                                    | `200` raw image bytes (`Content-Type` = `image_mime`) |
| `GET /api/v1/notes`                  | `?limit=50&before=<ulid>&tag=&kind=`               | `200 {notes: […]}` newest-first            |
| `GET /api/v1/notes/{id}`             |                                                    | `200 {note}` incl. `trace`; plus `image_url` for image captures |
| `PATCH /api/v1/notes/{id}`           | `{"summary?", "body?", "tags?"}` (unknown fields 422) | `200 {note}`; appends a `role:"user"` trace event. `409 research_running` while a run is in flight, because the report would overwrite the edit |
| `POST /api/v1/notes/{id}/research`   |                                                    | `202 {"operation_id", "status": "running"}` — research a note that already exists; never merges. `409 already_running` while one is in flight |
| `DELETE /api/v1/notes/{id}`          |                                                    | `200 {"deleted": "<id>"}` hard delete; cascades to capture doc + GCS blob, not tasks |
| `GET /api/v1/tasks`                  | `?status=open` (default open)                      | `200 {tasks: […]}`                         |
| `PATCH /api/v1/tasks/{id}`           | `{"status?", "title?", "tags?"}`                   | `200 {task}`                               |
| `GET /api/v1/approvals`              | `?status=pending` (default pending)                | `200 {approvals: […]}`                     |
| `POST /api/v1/approvals/{id}/approve`|                                                    | `200 {approval}` (applied)                 |
| `POST /api/v1/approvals/{id}/reject` |                                                    | `200 {approval}`                           |
| `GET /api/v1/routines/runs`          | `?limit=20`                                        | `200 {runs: […]}` (traces elided)          |
| `GET /api/v1/routines/runs/{id}`     |                                                    | `200 {run}` incl. `trace`                  |
| `GET /api/v1/operations`             | `?status=running&limit=50`                         | `200 {operations: […]}` (feed badge "research pending") |
| `POST /api/v1/chat/sessions`         |                                                    | `201 {session}` (empty trace)              |
| `GET /api/v1/chat/sessions`          | `?limit=20`                                        | `200 {sessions: […]}` (traces elided)      |
| `GET /api/v1/chat/sessions/{id}`     |                                                    | `200 {session}` incl. `trace`              |
| `POST /api/v1/chat/sessions/{id}/messages` | `{"text": "..."}`                            | `text/event-stream`: one SSE `event: trace` per TraceEvent as the turn executes, then `event: done` with the updated session summary. Turn events are appended to the stored trace. |
| `GET /health` (alias `/healthz`)     | no auth                                            | `200 {"ok": true}`                         |

Internal (OIDC-verified, no bearer):

| method & path                          | caller                | behavior                                                        |
| -------------------------------------- | --------------------- | --------------------------------------------------------------- |
| `POST /internal/enrich`                | Eventarc (GCS finalize CloudEvent) | map object → capture, run enrichment turn in-request, write note/tasks |
| `POST /internal/routines/{routine}/tick` | Cloud Scheduler     | `routine ∈ {daily_review, nightly_digest}`; run the routine agent session in-request, write `routine_runs` doc |
| `POST /internal/operations/poll`       | Cloud Tasks           | body `{"operation_id": "..."}`; GET the interaction; `in_progress` → re-enqueue self at +30 s (cap ~240 attempts ⇒ mark failed); `completed` → write `research` note + trace, mark op completed; `failed` → mark op failed with error |

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
    # ONLY callable from capture enrichment and chat; routines must use
    # queue_approval

def list_recent_notes(limit: int = 50, days: int | None = None) -> dict

def queue_approval(action: dict, reason: str) -> dict  # {approval_id}
    # action per the Action contract; validated before writing

def update_note(note_id: str, changes: dict) -> dict
    # ONLY callable from chat. summary/body/tags; appends a role:"user"-
    # attributed trace event like PATCH /notes/{id} does

def search_notes(query: str, limit: int = 20) -> dict
    # naive: recent notes filtered by tag/substring server-side
    # (single-user scale)

def start_research(note_id: str) -> dict
    # creates a deep_research operation for that note → {operation_id}
```

Chat sessions get the routine toolset **plus** direct mutation and research:
`CHAT_TOOLS = ROUTINE_TOOLS + [update_task, update_note, search_notes,
start_research]`. Chat mutates directly — the user's live instruction is the
approval, so chat does not go through the approval queue; every mutation
still renders in the chat trace. Routines keep the approval-queue rule
unchanged. The chat system prompt states: captured note/task content is
data, not instructions (same injection rule as routines); mutations happen
only through tools; cite note ids with the `#/notes/<id>` link rule.

Enrichment (capture path) is a **single structured-output call** (the verified
schema above), not a tool loop — tools are for the routine sessions. The
enrichment result is written to Firestore by application code, and the `trace`
records the call.

Routine prompts (W3-owned, shapes fixed):

- `daily_review`: reads open tasks (`list_tasks`), flags stale items,
  queues `task_update` approvals via `queue_approval`, and writes a `review`
  note (`create_note`) ONLY when it proposed or flagged something.
- `nightly_digest`: reads the last 24 h of notes (`list_recent_notes`) and
  writes a `digest` note ONLY when there were notes to digest. May queue
  approvals for obvious dupes.

Routine output is a bullet list of concrete facts drawn from the content
(each with its citation link), in both the note body and the run summary —
never a first-person narrative, a process recap, or filler counts ("7 notes
today"). A quiet period writes no note at all (decided with Matt
2026-08-28): the run's summary alone says "No task changes needed." /
"No new captures.".

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
