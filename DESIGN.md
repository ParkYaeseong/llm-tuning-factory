# Tuning Factory desktop design

Product: a Windows research workbench for reproducible RunPod training and shared MLflow records.
The user requested a substantial visual refinement and useful improvements based on similar open-source tools.

References inspected on 2026-10-14:
- https://getdesign.md/linear.app/design-md and its full DESIGN.md/preview: restrained hierarchy, hairlines,
  compact navigation, a single purposeful accent.
- https://getdesign.md/vercel/design-md and its full DESIGN.md/preview: neutral surfaces and precise alignment.
- https://github.com/unslothai/unsloth and https://unsloth.ai/docs/new/studio/: task-oriented training flows.
- https://github.com/hiyouga/LlamaFactory: training/evaluation separation and native trainer progress.
- https://mlflow.org/docs/latest/ml/tracking/quickstart/: run tables, parameters and metrics.

Use these as references for original product components, not brand/logo copies.
Do not transplant marketing heroes, cinematic gradients or pricing layouts into the application.

## Visual language

Default: light neutral workbench. Canvas #ffffff, sidebar #f8f9fb, subtle surface #f5f6f8,
ink #20232a, secondary #5d6370, borders #e6e8ed, selected surface #eaeefc, accent #5367c8.
Green/amber/red carry actual semantic states only. Never show an unverified connection as online.
Use bundled Pretendard with its OFL license for Korean and Latin; system fallback is Segoe UI/Malgun Gothic.
Base UI 13px, labels 12px, table 12px, page headings 24px, section headings 14px.
Spacing ladder 4/8/12/16/24/32. Controls 34-38px tall, radius 6px; panels 8px, no decorative shadows.
Use simple consistent outline SVG icons. No numbered sidebar, serif monogram, giant slogan or ornamental charts.

## Layout

Sidebar 216px with workspace identity, real command search, navigation groups and account footer.
Top app bar 54px with breadcrumb and neutral local/remote context.
Content maximum width 1240px, 32px gutter. Page title has a short description and one primary action.
Overview prioritizes recent runs, compact counts and a setup checklist. Empty states have an actionable next step.
Forms use short section headings, consistent alignment, and advanced options behind disclosure.
History has search/status filters, sticky table headings, a two-run comparison tray and changed-parameter rows.
Datasets show sample inspection and validation before immutable registration.
Training progress uses native trainer records only; absence of points is an explicit empty state.

## Interaction and integrity

Ctrl+K opens actual navigation search. Keyboard focus is visible, dialog focus is contained, reduced motion supported.
Retain the dry-run and explicit transfer gates. Preserve drafts in memory when changing views.
No automatic paid Pod lifecycle. No fake metric, placeholder trend, fake online indicator or automatic quality claim.
Validation must reject identical train/validation snapshots. Dataset preview never transfers data.
Use the existing engine; do not introduce a second trainer, adopt unmaintained AutoTrain, or replace MLflow.
