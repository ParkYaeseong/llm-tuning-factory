# Windows Desktop Implementation Plan

**Goal:** Deliver a Windows installer that exposes the existing training pipeline through Korean UI.
**Architecture:** Tauri Rust shell with Credential Manager and JSON Python sidecar.
**Tech Stack:** Tauri 2, TypeScript/Vite, Python 3.12, existing locked MLflow/SSH adapters.

1. Bridge contract tests in tests/test_desktop.py: profile persistence without secrets,
   offline preview, real-execution approval gate, snapshot/log safety, immutable datasets.
   Observe failures, then implement src/tuning_factory/desktop.py.
2. Remote runtime adapter in src/tuning_factory/desktop_runtime.py: provider mapping,
   pinned SSH identity, isolated dependency preparation, source/lock preflight, no lifecycle
   mutations. Test fail-closed and command quoting before implementation.
3. Build desktop/src-tauri: narrowly scoped commands, native file pickers, credential vault,
   bundled sidecar request protocol, async jobs and close/reopen recovery.
4. Build desktop/src: connection, data/profile, plan/execute, queue/log/recovery, shared-history
   views; add user-flow tests and compile/type checks.
5. Package sidecar with scripts/build_desktop.py, configure Windows installer and CI.
   Verify standalone sidecar, Rust compile, installer output and offline UI journey.
6. Update README, docs/WINDOWS_APP.md and docs/PROGRESS.md with measured results and limits.
   Run uv run pytest, uv run ruff check src tests, npm tests/build and cargo tests/build.
   Review credentials and changes before committing; no paid GPU or dataset transfer.
