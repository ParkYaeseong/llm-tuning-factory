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
  No reusable LLaMA-Factory configuration or active MLflow tracking server was established.
- New work: typed configuration, immutable dataset/AI registry, manifests, LLaMA-Factory adapter,
  durable queue, explicit execution policy, SSH Pod transport, MLflow adapter, evaluators, tests and operations docs.
- No existing service, Pod or dataset modified. Real GPU, remote server and image-build checks require external setup.

## Official references checked

- https://github.com/hiyouga/LLaMA-Factory — CLI and training examples; release 0.9.5 from PyPI.
- https://llamafactory.readthedocs.io/en/latest/advanced/arguments.html
- https://docs.runpod.io/api-reference/pods/POST/pods — REST v1, bearer authentication and networkVolumeId.
- https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html — explicit client/run APIs.
- https://mlflow.org/docs/latest/ml/model-registry/workflow/ — model version creation and aliases.

Dependencies are exact pinned; uv.lock captures transitive control-plane dependencies. GPU image resolution is separate.
