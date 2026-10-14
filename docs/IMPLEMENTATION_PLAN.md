# Implementation plan

The supplied master specification is the design brief. Continue without redundant design approvals as requested.

1. Configuration and provenance: strict Pydantic contracts, dataset validation and content-addressed copies,
   supported model/template pairs, immutable resolved manifests, AI revision history; tests for bad inputs and tampering.
2. Training boundary: TrainingBackend, LlamaFactoryBackend YAML translation and CLI invocation, checkpoint validation,
   artifact checksums. Test emitted configuration and real subprocess failure/timeout with injected executable.
3. Durable orchestration: transactional SQLite submission, idempotency, state transitions, global concurrency and
   independent GPU locks; retry bounds, cancellation and restart recovery. Test concurrent claims and failure cases.
4. Execution: local mock and RunPod REST/SSH adapters, policy enforcement, known-host verification, bounded execution,
   artifact retrieval. New Pod creation separately approved; borrowed Pods never destroyed.
5. Tracking and evaluation: MLflow client with stable run identifiers, failure artifacts, candidate metadata;
   JSON validity, classification macro F1, extraction F1 and baseline deltas. No invented model metrics.
6. Operator surface: validate/plan/run/batch/work/status/logs/cancel/resume/compare commands; three example AI configs,
   dummy fixtures, container recipe, CI and guides. Run full CPU suite and CLI acceptance flows.

SQLite is the supported single-host metadata backend. PostgreSQL and a multi-user API are documented future work.
Real GPU acceptance remains pending until authorized infrastructure is available.
