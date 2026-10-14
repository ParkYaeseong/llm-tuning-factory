# Windows desktop design

Approved by the user on 2026-10-14: Windows installer, Rust/Tauri shell, Korean UI,
existing Python control plane, LLaMA-Factory on individually assigned RunPod Pods,
shared MLflow history. No central scheduler in this release.

The shell stores secrets in Windows Credential Manager and invokes a bundled Python
sidecar over stdin/stdout JSON. No localhost listener or arbitrary shell command API.
Each installation has a private application-data queue and immutable datasets.
Profiles select the Pod, SSH key, independently verified SSH fingerprint, and MLflow.
Runtime preparation is explicit and confined to /workspace/factory-desktop/runtime.
Existing Pods are never automatically started, stopped, deleted or reconfigured globally.

Screens: connection wizard, task/model/training profiles, dataset registration and
validation selection, dry-run plan and explicit transfer approval, queue/logs/cancel/
reconcile/resume, MLflow paginated history and comparable-metric deltas.
Remote work survives window closure; collection requires reconnection and credentials.
Cross-PC GPU arbitration and provider-side cost caps remain out of scope.

Distribute a bundled Python executable plus matching wheel, source files and dependency
locks. Verify bridge contracts, authorization boundaries, UI flows, Rust build and Windows
installer. Actual GPU verification of the new app requires separately authorized compute;
historical CLI acceptance must not be described as desktop acceptance.
