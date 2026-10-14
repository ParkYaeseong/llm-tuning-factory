# Development

Use Python 3.12 and `uv sync --extra dev --extra tracking --locked`.
Run `uv run pytest` and `uv run ruff check src tests` before reporting completion.
Keep training and infrastructure adapters separate. Never put credentials in manifests.
Default to dry-run. Do not provision paid resources or transfer datasets without explicit authorization.
Existing user Pods are borrowed: never stop or delete them automatically.
Record actual verification and outstanding GPU work in docs/PROGRESS.md.
