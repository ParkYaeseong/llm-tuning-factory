# Desktop refinement implementation plan

Goal: apply the user-authorized visual redesign and useful workflow improvements to the Windows pilot.
Architecture: retain Rust vault/JSON bridge/RunPod/MLflow. Build original UI guided by getdesign.md references.
Chosen approach: light workbench; optional dark-first or Carbon-style dense layouts were considered.

1. Research primary upstream docs and capture references/tradeoffs in DESIGN.md and docs/OPEN_SOURCE_REVIEW.md.
2. Add failing tests for bounded local dataset inspection, identical validation rejection, actual trainer JSONL
   progress parsing and per-run parameter differences. Implement small separate desktop_inspection helpers.
3. Redesign desktop/src using local font assets, SVG icons, flat navigation, actual recent-run overview,
   compact forms and command search. Keep explicit execution approvals and secret masking intact.
4. Add dataset sample inspection, loaded-history filters, changed-setting comparison and reuse-config action.
   Expose native progress/real loss curve with explicit empty state; bound payloads and verify original Pod.
5. Verify Python tests/Ruff, UI workflows including filters/keyboard/preview and Rust allowlist.
6. Build the matching Python sidecar and Windows 0.2.1 installer. Verify packaged source/worker lock parity,
   installed-app offline flow and UI screenshots. Record exact evidence and outstanding paid GPU acceptance.
7. Update existing draft PR and share installer/preview. No paid GPU, institution data transfer or new services.
