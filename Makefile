### memex dev loop ############################################################
# Local dev runs against the Firestore emulator + real Vertex via ADC.

FIRESTORE_EMULATOR_PORT ?= 8790
export FIRESTORE_EMULATOR_HOST_VALUE = localhost:$(FIRESTORE_EMULATOR_PORT)

.PHONY: dev emulator api web test lint build deploy

## Run the Firestore emulator (foreground; separate terminal)
emulator:
	gcloud emulators firestore start --host-port=$(FIRESTORE_EMULATOR_HOST_VALUE)

## Run the API locally against the emulator
api:
	FIRESTORE_EMULATOR_HOST=$(FIRESTORE_EMULATOR_HOST_VALUE) \
	MEMEX_DEVICE_KEYS_JSON='{"dev": "dev-key"}' \
	MEMEX_INSECURE_LOCAL=1 \
	uv run uvicorn memex.api.app:app --reload --port 8780

## Frontend dev server (proxies /api to :8780)
web:
	cd web && pnpm dev

test:
	FIRESTORE_EMULATOR_HOST=$(FIRESTORE_EMULATOR_HOST_VALUE) uv run pytest -q

lint:
	uv run ruff check memex tests

## Build frontend into memex/static for the container
build:
	cd web && pnpm install && pnpm build

## Full code rollout: SPA build -> container build/push -> Cloud Run deploy.
## (terraform ignores the container image on purpose; run `terraform apply`
## separately for infrastructure changes.)
PROJECT ?= m4tt-xyz
REGION ?= us-central1
IMAGE ?= $(REGION)-docker.pkg.dev/$(PROJECT)/memex/memex:latest

deploy: build
	gcloud builds submit --project $(PROJECT) --tag $(IMAGE) .
	gcloud run deploy memex --project $(PROJECT) --region $(REGION) --image $(IMAGE)
