# memex — build plan

Voice/text capture agent for the All Things Agentic Hackathon (Taskmaster
track). Capture a thought from phone or desktop → an ADK agent transcribes,
enriches, extracts action items, tracks tasks, and runs scheduled background
reviews — all on GCP, one `terraform apply`, everything scale-to-zero.

- **Merge path:** this new repo, local `main`, no PRs (solo, 4-day clock).
  Public GitHub push before submission (run `scrub-private-data` first).
- **Reviewer:** roborev whole-branch pass before the public push; per-commit
  hook optional (skip triaging chore commits).
- **Kill criterion:** capture → agent turn → tasks visible in UI, deployed on
  Cloud Run, not working end-to-end by **Sat Aug 29 morning** → stop, polish
  aftershift, submit that instead.
- **Size class:** bounded-large. This plan is the spec; no separate design doc.
- **Deadline:** submission closes **Sun Aug 31, 5 PM PT**. Last half-day
  reserved for video + diagram + write-up (judged artifacts).

## Hackathon compliance (locked)

Gemini 3.5+ via Vertex ✓ · Google ADK ✓ · GCP infra: Cloud Run, Firestore, GCS,
Eventarc, Cloud Scheduler, Secret Manager ✓ · Track: **Taskmaster**
(event-driven workflow, autonomous routing). New code only; disclose lifted
patterns (adk-samples long-horizon-harness, serverless-memex design) in the
write-up.

## Architecture (1 paragraph)

One public Cloud Run service (FastAPI + ADK runner + static frontend) with
bearer-token auth (per-device keys in Secret Manager — iOS-Shortcuts- and
curl-friendly; no IAP). `POST /capture` (text JSON) and `POST /capture/audio`
(raw m4a/wav body → GCS, `202 {id}`). Both paths create a Firestore capture doc;
audio finalize in GCS fires **Eventarc → agent turn**: Gemini (audio-native, no
STT service) produces transcript + summary + tags + action items in one
structured call; tools write notes/tasks back to Firestore. Cloud Scheduler
drives two **routines** as real agent sessions (Horizon fire-path pattern):
daily task review (nag, staleness) and nightly digest (consolidation
over the Firestore corpus — our "dreaming", fully inspectable). Consequential
actions (calendar/email drafts) queue as **pending approvals** the UI resurfaces
(Horizon HITL pattern, scoped down). Memory Bank is optional garnish behind an
adapter that degrades to noop — never load-bearing. Everything min-instances=0.

## Explicitly cut

Sandbox/code-execution, OAuth "Connect Google", Cloud SQL (Firestore instead),
IAP, multi-user, A2A surface, web-research subagent. Dream-review via Memory
Bank `memories.generate`: stretch only.

## Workstreams

Contract-first: **W0 freezes the data model + API shapes on day 1**; after that
W1–W4 are independent and parallelizable (subagent-friendly). Each workstream =
focused commits on main.

| #   | Workstream              | Contents                                                                                                                                                                                                     | Depends on     |
| --- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------- |
| W0  | Contracts               | Firestore schema (captures, notes, tasks, approvals, routine_runs), API OpenAPI sketch, agent tool signatures, repo scaffold (`agents-cli scaffold` or hand-rolled uv layout), Makefile dev loop             | —              |
| W1  | Infra                   | Terraform: Cloud Run, Firestore, GCS bucket, Eventarc trigger, Cloud Scheduler jobs, Secret Manager, SAs/IAM. `make deploy`. Prune from Horizon's terraform/                                                 | W0             |
| W2  | Capture API + auth      | FastAPI: /capture, /capture/audio, /notes, /tasks, /approvals, bearer-key middleware, GCS upload, Firestore writes                                                                                           | W0             |
| W3  | Agent                   | ADK agent + enrichment turn (structured output: transcript/summary/tags/action-items), Firestore tools, Eventarc-triggered runner endpoint, routine turns (task review, nightly digest), approval-queue tool | W0             |
| W4  | Frontend                | Capture-first feed (memex-style), note detail w/ agent trace, tasks view, approval buttons, record button (MediaRecorder)                                                                                    | W0 (API mocks) |
| W5  | Clients                 | Adapt `memex-capture-voice.sh` (fix the pidfile bug or send audio straight up — no Parakeet dependency), iOS Shortcut + doc                                                                                  | W2 deployed    |
| W6  | Integration + hardening | End-to-end on real GCP, cost check (scale-to-zero verified), roborev branch review, scrub-private-data                                                                                                       | W1–W4          |
| W7  | Submission              | Demo video (capture→task→routine→approval arc), architecture diagram, write-up, public repo                                                                                                                  | W6             |

## Schedule (4 days)

- **Wed (today):** W0 complete; W1+W2+W3 started in parallel (subagent
  workflows); core loop working locally against Firestore emulator by EOD.
- **Thu:** deploy to GCP; W4 frontend; Eventarc path live; routines firing.
- **Fri:** W5 clients; HITL approvals; nightly digest; polish. Kill-criterion
  checkpoint Sat AM.
- **Sat:** W6 integration + review + scrub; stretch: Memory Bank garnish.
- **Sun (by noon):** W7 video/diagram/write-up; submit with buffer.

## Subagent / workflow execution notes

- W0 is done inline (it's the contract everything reads).
- W1–W4 fan out as parallel worktree-isolated agents once W0 lands; each gets
  the contract doc + its workstream row as the prompt.
- Verification passes (W6) use an adversarial-review workflow over the diff
  (find → verify) before the public push.
- Routine prompt-tuning and eval cases can run as background agents while
  integration proceeds.

## Facts this design depends on that live outside the code

- Gemini accepts inline audio (m4a/wav/ogg) via Vertex — verify with one
  `google-genai` call before W3 builds on it.
- Eventarc GCS-finalize → Cloud Run requires the trigger SA to have
  `roles/run.invoker` + `eventarc.eventReceiver` — encode in W1, verify on first
  deploy.
- iOS Shortcuts can POST a recorded audio file with custom headers — known good
  from serverless-memex.
- $150 credit is applied to the billing account (Matt confirmed received).
- Scale-to-zero: no min-instances anywhere; Firestore/GCS/Scheduler idle cost ≈
  $0.
