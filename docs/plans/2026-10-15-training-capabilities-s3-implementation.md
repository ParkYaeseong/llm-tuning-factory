# Training capabilities and S3 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose pinned LLaMA-Factory training stages/methods with consistent validation and immutable S3 imports.

**Architecture:** Keep training, storage and infrastructure separate. The UI and worker share strict Python capability rules; S3 keys stay in the Windows vault and downloads become verified local registry snapshots. Existing source manifests remain immutable and old jobs require their matching engine.

**Tech Stack:** Python 3.12/Pydantic, LLaMA-Factory 0.9.5, boto3, Tauri/Rust, TypeScript, pytest/Playwright.

## 1. Stage-aware datasets (root)

Files: registry.py, desktop_inspection.py, manifest.py, cli.py; tests/test_dataset_kinds.py.
Write failing fixtures for text, instruction, preference, KTO and prompt records, then run `uv run pytest tests/test_dataset_kinds.py`.
Extend parse_dataset(raw, kind=None) preserving the old triple and register_dataset(..., kind='instruction', source_metadata=None).
Validate each stage in manifest resolution, preserve old instruction records without a kind key, validate S3 provenance without secrets.
Run new and existing registry/runner tests, review failure cases before advancing.

## 2. Engine rules (training agent)

Files: config.py, capabilities.py, training.py, worker.py; tests/test_training_capabilities.py.
First test all pinned stages, strict method options, preference losses, PPO requirements, forbidden combinations.
Implement strict stage/options models and one capability source, translators/descriptors and stage-specific model output/checkpoint verification.
Test full model shards, adapters and value heads, prediction path and conservative unsupported recovery.
Run targeted tests, then root reviews actual loaders/upstream restrictions and integration behavior.

## 3. S3 adapter (storage agent)

Files: storage.py, pyproject.toml, uv.lock; tests/test_storage.py.
First test paginated listing, HEAD, VersionId or ETag frozen download, stream length/limit/hash, error redaction and partial cleanup.
Implement injected boto3 client, strict HTTPS endpoint and explicit credentials, no ambient secret fallback.
Run Stubber/loopback contract tests and verify no credentials in returned provenance.

## 4. Desktop/CLI integration (root)

Files: desktop.py, runner.py, desktop_inspection.py, cli.py; tests/test_desktop_extensions.py.
First test catalog/capabilities API, incompatible plans, explicit S3 import authorization, failed import not registered, cleanup.
Wire S3 listing/head/import to registry and shared capability catalog. Preview accepts dataset kind.
Record training stage in history and reject cross-stage metric deltas. Keep API errors redacted.
Add CLI dataset-kind option and document new configuration examples.

## 5. Windows UI/vault (UI agent)

Files: desktop/src/main.ts, desktop/src-tauri/src/main.rs, desktop/tests/workspace.spec.ts.
First test stage-dependent forms, capability errors, stale-plan invalidation and S3 browse/head/explicit import.
Drive model choices from catalog; expose finetuning/stage fields, dataset kinds and secret-masked S3 settings.
Add only explicit native commands and vault fields. Run npm test/build and Rust allowlist checks.

## 6. Release verification (root)

Run `uv sync --extra dev --extra tracking --locked`, `uv run pytest`, `uv run ruff check src tests`, UI/build/Rust checks.
Build frozen bridge and verify source/worker/lock integrity; build versioned NSIS installer where tools permit.
Review diff and secret staging/history. Record actual evidence and unverified GPU/S3-provider paths in docs/PROGRESS.md.
Commit/push feature branch and open review PR with current scope. Do not alter 0.2.3 release, start paid resources or import actual institutional data.
