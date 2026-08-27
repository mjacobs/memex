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

deploy: build
	cd terraform && terraform apply
