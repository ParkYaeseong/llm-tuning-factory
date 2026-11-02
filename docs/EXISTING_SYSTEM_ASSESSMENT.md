# Existing system assessment — 2026-10-14

- Workspace D:/llm-tuning-factory was empty and not a Git repository. No existing files overwritten.
- Python 3.14 default, 3.13 and uv-managed 3.12 available. Control plane selects 3.12 for ML ecosystem compatibility.
- Docker CLI 29.6.2-rd available; Docker daemon connection failed. NVIDIA tool unavailable.
- RUNPOD/MLFLOW/CUDA environment-variable names were absent. No credentials inspected.
- D:/GitHub contains labnote_runpod_serverless, runpod_serverless_ops, runpod-portal and scientific RunPod projects.
  The labnote README describes Serverless inference, Docker publication and persistent cache volumes, not Pod SFT.
  Reuse the persistent-storage operational pattern, not its Ollama/Redis serving implementation.
- Authenticated GitHub repository-name search found RunPod repositories, no LLaMA-Factory or MLflow tuning repository
  in the first 40 recent repositories. This is a bounded survey, not proof no other assets exist.
- Broad filename surveys of D:/workflow and D:/GitHub encountered inaccessible temporary directories and were bounded.
  The completed survey subsequently located D:/workflow/kbl-llm-repo as the canonical existing tuning project.
- New work: typed configuration, immutable dataset/AI registry, manifests, LLaMA-Factory adapter,
  durable queue, explicit execution policy, SSH Pod transport, MLflow adapter, evaluators, tests and operations docs.
- No existing service, Pod or dataset modified. Real GPU, remote server and image-build checks require external setup.

## Canonical existing tuning assets — corrected after the bounded survey completed

D:/workflow/kbl-llm-repo/README.md and AGENTS.md establish that this is the source of truth;
D:/workflow/_pod is a staging mirror, not canonical. Existing features include LLaMA-Factory/LlamaBoard,
graph/prose adapters, RunPod Network Volume operation, direct TCP SSH, MLflow Keycloak refresh,
generation extraction evaluation, DVC/NCP publishing, and historical results. Existing documents contain
historical metrics; they were not revalidated and are not reported as new platform performance.

Reused in this implementation:

- ops/dataset_info.json: preserve the existing ShareGPT JSONL messages/role/content mapping in the common backend.
  Registry validates and snapshots these original bytes without creating/rewriting real data.
- scripts/mlflow_util.py: adapt its service-account bearer-token refresh contract to configurable HTTPS OIDC
  environment variables; remove hardcoded credential-file paths. Existing secrets were not loaded or copied.
- scripts/eval_extraction_pc.py: reuse the pure names_of/ids_of/set-F1 semantics as an explicit kbf_extraction evaluator,
  without importing its GPU/model loader. Heuristic partial credit and strict JSON validity remain distinct.
- Existing direct-SSH, persistent-volume and process-cleanup lessons inform the executor/ownership boundaries.

Not copied: serving stack, production pods, custom version-check bypasses, EXAONE template patches,
dataset-generation scripts, server credentials, historical model weights/data, or DVC/NCP secrets.
Existing Llama-3.1/3.2 bases are supported by the conservative template catalog; newer/custom models need a tested profile.
The initial upstream-pinned image has no local production patches and cannot replace the current Pod blindly.

The known existing MLflow URL is https://mlflow-legacy.example.org and its documented realm is
https://sso.example.org/realms/kbf. Unauthenticated read-only /health and /version probes both
returned HTTP 308. This establishes an HTTP response, not authenticated tracking or writable artifact-store readiness.
No production MLflow runs or remote changes were made. See OPERATIONS.md for explicit configuration to reuse this server.

## Official references checked

- https://github.com/hiyouga/LLaMA-Factory — CLI and training examples; release 0.9.5 from PyPI.
- https://llamafactory.readthedocs.io/en/latest/advanced/arguments.html
- https://docs.runpod.io/api-reference/pods/POST/pods — REST v1, bearer authentication and networkVolumeId.
- https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html — explicit client/run APIs.
- https://mlflow.org/docs/latest/ml/model-registry/workflow/ — model version creation and aliases.

Dependencies are exact pinned; uv.lock captures transitive control-plane dependencies. GPU image resolution is separate.
