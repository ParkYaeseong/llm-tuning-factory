# Similar tools and applied improvements — 2026-10-14

This review uses the upstream repositories and official documentation, not star counts or third-party rankings.
No upstream application source was copied. Existing LLaMA-Factory and MLflow adapters remain the execution backbone.

| Project | Observed scope | Decision for this application |
|---|---|---|
| [Unsloth Studio/Desktop](https://github.com/unslothai/unsloth), [official Studio docs](https://unsloth.ai/docs/new/studio/) | Desktop and browser UI for model training, local inference, data preparation and export | Closest product reference. Adopt task-oriented setup, inspect data before training, visible actual training progress. Do not introduce a second engine/runtime just for appearance. |
| [LLaMA-Factory / LlamaBoard](https://github.com/hiyouga/LlamaFactory) | Native web UI, training methods, model configuration, evaluation and experiment monitors | Keep the existing tested engine. Read its actual trainer_log.jsonl instead of inventing a parallel progress system. |
| [MLflow](https://github.com/mlflow/mlflow), [tracking guide](https://mlflow.org/docs/latest/ml/tracking/quickstart/) | Experiment tables, parameter/metric/artifact recording and run views | Keep the shared service. Improve loaded-record search, status filtering and changed-parameter comparison in the desktop. |
| [AutoTrain Advanced](https://github.com/huggingface/autotrain-advanced) | No-code task training; upstream README explicitly states it is no longer maintained | Do not adopt as a new runtime dependency. |

Assessment: desktop fine-tuning tools already exist. This application's specific focus is Windows control of assigned
RunPod Pods, institution MLflow authentication, immutable dataset/config provenance and conservative recovery.
The reviewed setup guides do not establish a drop-in replacement for that complete workflow. This is an assessment
of the documented fit, not a claim that other tools cannot be integrated with RunPod or MLflow.
Moving to a different training engine would require a separate compatibility/acceptance project.

## Applied in 0.2.1

- Local dataset inspection: validated format, record count, exact duplicate-record count and first three samples.
  Content is bounded/truncated for display. Preview is limited to 32 MiB and never uploads data.
- Plan and direct submission reject byte-identical train/validation snapshots, including aliases under different IDs.
  This does not detect all partial overlap, near duplicates or semantic leakage.
- Read actual native LLaMA-Factory step/loss/elapsed/remaining records; ignore partial lines and nonfinite losses;
  show at most 200 plotted points. Missing records show an empty state, not a fabricated chart.
- Load-history text/status filters are explicitly scoped to the fetched pages; pagination remains available.
- Show nested changed settings separately from metric deltas. Reuse a full run configuration for a new local plan
  without automatically submitting it.
- Command navigation via Ctrl+K; preserve unsaved form drafts in process memory; never store secrets in localStorage.
- Human-readable connection results and a neutral saved-state display instead of an unverified online indicator.

## Design sources and assets

[getdesign.md Linear](https://getdesign.md/linear.app/design-md) and
[Vercel](https://getdesign.md/vercel/design-md), including their upstream DESIGN.md files, were inspected.
The original application rules are captured in the root DESIGN.md: restrained hierarchy, neutral surfaces,
hairline borders, compact navigation and a single accent. Marketing-page heroes and branded logos were not copied.

The application bundles [Pretendard v1.3.9](https://github.com/orioncactus/pretendard/tree/v1.3.9) and its
OFL license under desktop/public/fonts. Font loading does not contact a CDN at runtime.

## Still separate work

Large-data streaming/chunked registration, full train/validation overlap scans, shared multi-PC GPU scheduling,
new model/engine qualification, gated-model token delivery and actual UI-driven paid GPU acceptance remain separate.
No optimization claim or increased training throughput is made from this UI refinement.
