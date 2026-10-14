"""Force cancellation after a complete real checkpoint, then resume within the same approved budget."""
import json
import os
import threading
import time
from pathlib import Path

import paramiko

from tuning_factory.config import AIConfig, load_config
from tuning_factory.connections import load_connection
from tuning_factory.execution import Policy, RunPodController, SSHExecutor
from tuning_factory.runner import Factory
from tuning_factory.tracking import MLflowTracker

session = json.loads(Path('.factory/gpu-session.json').read_text())
remaining = int(session['deadline'] - time.time())
if remaining < 210:
    raise ValueError('insufficient approved session time for cancellation plus resume; do not extend automatically')
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
factory = Factory('state/gpu-recovery', policy=Policy(max_timeout_seconds=1200, max_cost_usd=2),
                  tracker=MLflowTracker(profile.tracking_uri))
factory.registry.register_dataset('synthetic_smoke', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
factory.registry.register_dataset('synthetic_validation', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
data = load_config('configs/ai/integration_smoke.yaml').model_dump()
data['ai_id'] = 'integration_recovery'
data['hyperparameters']['max_steps'] = 40
data['hyperparameters']['save_total_limit'] = 2
data['evaluation_profile'] = {'kind': 'pipeline'}
data['resource_profile']['timeout_seconds'] = min(240, remaining - 60)
config = AIConfig.model_validate(data)
job = factory.submit(config, simulation=False)
directory = factory.directory(job['id'])
stopped = threading.Event()
monitor_errors = []


def monitor_checkpoint():
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        c.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
                  key_filename=str(profile.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
        with c.open_sftp() as sftp:
            while not stopped.wait(0.3):
                marker = directory / 'remote-attempt.json'
                if not marker.exists():
                    continue
                output_path = json.loads(marker.read_text())['remote_path'] + '/output/'
                try:
                    names = sftp.listdir(output_path)
                    candidates = sorted([name for name in names if name.startswith('checkpoint-')],
                                        key=lambda name: int(name.split('-')[-1]), reverse=True)
                    if not candidates:
                        continue
                    remote = output_path + candidates[-1] + '/'
                    for name in ['adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
                                 'scheduler.pt', 'rng_state.pth', 'trainer_state.json']:
                        sftp.stat(remote + name)
                except OSError:
                    continue
                factory.store.cancel(job['id'])
                return
    except Exception as exc:
        monitor_errors.append(type(exc).__name__)
    finally:
        c.close()


def executor():
    return SSHExecutor(pod['publicIp'], int(pod['portMappings']['22']), 'root', profile.ssh_key_file,
        remote_root='/workspace/factory-acceptance/recovery', pod=pod,
        remote_python='/workspace/factory-acceptance/runtime/venv/bin/python',
        worker_lock_file='/workspace/factory-acceptance/runtime/containers/gpu-requirements.lock',
        hf_home='/workspace/factory-acceptance/hf-cache')


thread = threading.Thread(target=monitor_checkpoint, daemon=True)
thread.start()
try:
    canceled = factory.work(executor=executor(), gpu=profile.pod_id, approved_transfer=True, target_job_id=job['id'])
finally:
    stopped.set()
    thread.join(timeout=25)
if canceled['status'] != 'CANCELED':
    raise RuntimeError('expected actual training cancellation; status=' + canceled['status'] + '; monitor=' + str(monitor_errors))
required = ['adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt', 'scheduler.pt', 'rng_state.pth', 'trainer_state.json']
checkpoints = sorted((directory / 'output').glob('checkpoint-*'), key=lambda p: int(p.name.split('-')[-1]))
checkpoint = next((p for p in checkpoints if all((p / name).is_file() for name in required)), None)
if checkpoint is None:
    raise RuntimeError('no complete checkpoint retrieved from the canceled real worker')
factory.resume(job['id'], checkpoint)
resumed = factory.work(executor=executor(), gpu=profile.pod_id, approved_transfer=True, target_job_id=job['id'])
receipt = {'job_id': job['id'], 'canceled_run_id': canceled['run_id'], 'resumed_run_id': resumed['run_id'],
           'checkpoint': checkpoint.name, 'first_status': canceled['status'], 'resumed_status': resumed['status'],
           'simulation': False, 'synthetic_data': True, 'error': resumed['error']}
(factory.root / 'recovery-receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
if resumed['status'] != 'COMPLETED':
    raise SystemExit(1)
