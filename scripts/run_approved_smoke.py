"""Real tiny SFT + native generation on the explicitly approved isolated Pod runtime."""
import json
import os
import time
from pathlib import Path

from tuning_factory.config import AIConfig, load_config
from tuning_factory.connections import load_connection
from tuning_factory.execution import Policy, RunPodController, SSHExecutor
from tuning_factory.runner import Factory
from tuning_factory.tracking import MLflowTracker

session = json.loads(Path('.factory/gpu-session.json').read_text())
remaining = int(session['deadline'] - time.time()) - 60
if session['authorization'] != 'explicit-user-approved-start-test-stop' or remaining < 120:
    raise ValueError('approved GPU session absent or too close to deadline')
profile = load_connection('.factory/connection.json')
controller = RunPodController(os.environ['RUNPOD_API_KEY'])
pod = controller.status(profile.pod_id)
tracker = MLflowTracker(profile.tracking_uri)
factory = Factory('state/gpu-acceptance', policy=Policy(max_timeout_seconds=1200, max_cost_usd=2), tracker=tracker)
factory.registry.register_dataset('synthetic_smoke', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
factory.registry.register_dataset('synthetic_validation', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
data = load_config('configs/ai/integration_smoke.yaml').model_dump()
data['resource_profile']['timeout_seconds'] = min(900, remaining)
config = AIConfig.model_validate(data)
job = factory.submit(config, request_key='approved-synthetic-smoke-' + str(int(time.time())), simulation=False)
Path('.factory/smoke-job.json').write_text(json.dumps({'job_id': job['id'], 'state': str(factory.root)}, indent=2))
executor = SSHExecutor(pod['publicIp'], int(pod['portMappings']['22']), 'root', profile.ssh_key_file,
    remote_root='/workspace/factory-acceptance/jobs', pod=pod,
    remote_python='/workspace/factory-acceptance/runtime/venv/bin/python',
    worker_lock_file='/workspace/factory-acceptance/runtime/containers/gpu-requirements.lock',
    hf_home='/workspace/factory-acceptance/hf-cache')
result = factory.work(executor=executor, gpu=profile.pod_id, approved_transfer=True, target_job_id=job['id'])
receipt = {'status': result['status'], 'error': result['error'], 'job_id': result['id'], 'run_id': result['run_id'],
           'simulation': False, 'synthetic_data': True, 'real_task_quality_verified': False}
if result['status'] == 'COMPLETED':
    factory.verify_artifacts(result['id'])
    receipt['evaluation'] = json.loads((factory.directory(result['id']) / 'evaluation.json').read_text())
    receipt['runtime'] = json.loads((factory.directory(result['id']) / 'runtime.json').read_text())
(factory.root / 'real-gpu-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
print(json.dumps(receipt, indent=2))
if result['status'] != 'COMPLETED':
    raise SystemExit(1)
