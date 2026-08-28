# memex infrastructure

Terraform for the whole memex stack on GCP project `m4tt-xyz` (override with
`-var project=...`): Cloud Run service, Firestore, audio GCS bucket, Eventarc
trigger, Cloud Scheduler routine jobs, Secret Manager, and the service
accounts wiring them together. Local state only — no remote backend.

## Deploy order

The Cloud Run service needs an image that exists, so build and push first,
then apply.

### 1. Build and push the image

The Artifact Registry repo is created by terraform, so on a completely fresh
project create it (and enable the APIs) with a targeted apply first:

```sh
cd terraform
terraform init
terraform apply -target=google_artifact_registry_repository.docker
```

Then, from the repo root:

```sh
gcloud builds submit --project m4tt-xyz \
  --tag us-central1-docker.pkg.dev/m4tt-xyz/memex/memex:latest .
```

### 2. Apply

```sh
cd terraform
terraform apply -var image=us-central1-docker.pkg.dev/m4tt-xyz/memex/memex:latest
```

Later image rollouts go through `gcloud run deploy` (terraform ignores image
changes after creation):

```sh
gcloud run deploy memex --project m4tt-xyz --region us-central1 \
  --image us-central1-docker.pkg.dev/m4tt-xyz/memex/memex:latest
```

### 3. Add device keys (out of band, required before first use)

Terraform creates the `memex-device-keys` secret but no version — keys never
touch terraform state. Add one:

```sh
echo -n '{"dev": "<long-random-key>"}' | \
  gcloud secrets versions add memex-device-keys --project m4tt-xyz --data-file=-
```

The service reads the `latest` version at startup (env
`MEMEX_DEVICE_KEYS_JSON`), so redeploy (or let it scale to zero and back) after
rotating keys.

## Shape notes

- **Scale-to-zero everywhere**: `min_instance_count = 0` is a project
  invariant; nothing here may raise it.
- **Public ingress, app-level auth**: `allUsers` holds `run.invoker`; the app
  enforces bearer keys on `/api/*` and verifies Google-signed OIDC tokens
  (audience = `MEMEX_SERVICE_URL`) on `/internal/*`.
- **Deterministic service URL**: the OIDC audience and `MEMEX_SERVICE_URL` use
  the project-number URL form (`https://memex-<project#>.us-central1.run.app`)
  to avoid a terraform self-reference cycle. Eventarc mints its OIDC token for
  the service's own URL — if the app rejects Eventarc requests on audience
  mismatch, compare the token `aud` with `MEMEX_SERVICE_URL`.
- **Audio lifecycle**: raw audio objects are deleted after 30 days
  (`audio_retention_days`); Firestore keeps the transcript.
- **Scheduler**: `daily_review` at 09:00, `nightly_digest` at 03:00, both in
  `time_zone` (default `America/Los_Angeles`).
- **Eventarc ack deadline (manual)**: the Eventarc-managed Pub/Sub push
  subscription defaults to a 10 s ack deadline, shorter than a synchronous
  enrichment run, which causes redelivery (the app dedupes, but retries burn
  cycles). Terraform does not own that subscription; after (re)creating the
  trigger, raise it once:
  `gcloud pubsub subscriptions update <eventarc-...-sub-...> --ack-deadline=600 --project <project>`
