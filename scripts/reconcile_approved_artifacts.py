"""Collect the original completed synthetic worker without retraining it."""
import json
import os
import time
from pathlib import Path

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController, SSHExecutor
from tuning_factory.registry import digest
from tuning_factory.runner import Factory
from tuning_factory.tracking import MLflowTracker

session = json.loads(Path('.factory/gpu-session.json').read_text())
if (session.get('authorization') != 'explicit-user-approved-start-test-stop'
        or session.get('state') == 'STOPPED' or session['deadline'] - time.time() < 120):
    raise ValueError('active approved session with collection margin required')
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
if pod['id'] != session['pod_id'] or pod['desiredStatus'] != 'RUNNING':
    raise ValueError('approved Pod is not running')
tracker = MLflowTracker(profile.tracking_uri)
factory = Factory('state/gpu-acceptance', tracker=tracker)
job_id = '260aa618906147cf9d4da5bc92e29ebd'
before = factory.store.get(job_id)
executor = SSHExecutor(pod['publicIp'], int(pod['portMappings']['22']), 'root', profile.ssh_key_file,
                       remote_root='/workspace/factory-acceptance/jobs', pod=pod)
result = factory.reconcile(job_id, executor)
directory = factory.directory(job_id)
remote_hashes = json.loads((directory / 'remote-artifacts.json').read_text())
for name, expected in remote_hashes.items():
    target = (directory / name).resolve()
    if not target.is_relative_to(directory) or digest(target.read_bytes()) != expected:
        raise ValueError('remote artifact inventory was not fully recovered')
factory.verify_artifacts(job_id)
download = factory.root / 'verified-mlflow-download'
download.mkdir(exist_ok=True)
adapter = tracker.client.download_artifacts(result['run_id'], 'output/adapter_model.safetensors', str(download))
if digest(Path(adapter).read_bytes()) != remote_hashes['output/adapter_model.safetensors']:
    raise ValueError('MLflow adapter round-trip hash mismatch')
run = tracker.client.get_run(result['run_id'])
receipt = {'job_id': job_id, 'previous_status': before['status'], 'status': result['status'],
           'run_id': result['run_id'], 'mlflow_status': run.info.status,
           'remote_inventory_files_verified': len(remote_hashes), 'retrained': False,
           'adapter_mlflow_round_trip_verified': True, 'synthetic_only': True,
           'evaluation': json.loads((directory / 'evaluation.json').read_text())}
(factory.root / 'full-recovery-receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
if result['status'] != 'COMPLETED' or run.info.status != 'FINISHED':
    raise SystemExit(1)
