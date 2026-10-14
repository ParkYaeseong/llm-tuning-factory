# Operational integration continuation

1. Authenticate with existing Keycloak credentials by reference; never copy secret values into this repository.
2. Use the canonical MLflow host verified from the legacy host's HTTPS redirect. Verify diagnostic run,
   artifact upload/download and failed-run status without training or fabricated quality metrics.
3. Add connection profiles and a doctor command for reproducible connection checks.
4. Normalize live RunPod metadata before resource checks. Prepare bounded dummy GPU acceptance before paid approval.
5. Finish registered validation data, bounded actual adapter generation and detached-attempt reconciliation.
6. Run CPU tests and update actual acceptance receipts. Container startup requires the user/environment to resolve
   the automatic approval rejection; do not bypass it.

Initial live evidence: Keycloak token and authenticated experiment search succeed. The legacy MLflow URL
redirects to https://mlflow.kbiofoundry.kr. KBF Pods are stopped; kbf-fix's API rate is $5.29/hour.
Subsequent explicit user approvals authorized 20 minutes plus an additional 10 minutes on the existing H200,
synthetic examples only, followed by stop and EXITED confirmation. No further paid extension is authorized.
Live production tracking/artifact checks, registered native prediction and detached result reconciliation
are implemented; consult PROGRESS.md for the latest actual acceptance results and remaining limits.

Follow-up: all 40 original GPU artifacts and the MLflow adapter round trip are verified. Native checkpoint-10
to step 12 restoration and preserved history are verified; the restored run is FINISHED. The Pod is EXITED.
That earlier control request arrived late; a subsequent user-authorized 90-minute maximum session used the
GPU-local observer and smaller state transfer. Actual mid-training cancellation, controller restart
reconciliation and checkpoint-10 to step 12 resume now pass. Final artifacts and MLflow round trips verify;
the Pod was stopped after 25.3 minutes and EXITED confirmed. See the latest PROGRESS.md evidence.

Remaining optional acceptance: QLoRA, GPU batch, numerical reproducibility, image build, registry publication,
and abrupt hardware/Pod loss during checkpoint writing. Real task quality needs representative authorized data.
