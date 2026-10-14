"""Reconcile a canceled GPU worker after controller restart, then verify native resume."""
import json
import os
import time
from pathlib import Path

import yaml
from recovery_transfer import RecoveryExecutor

from tuning_factory.connections import load_connection
from tuning_factory.execution import Policy, RunPodController
from tuning_factory.runner import Factory
from tuning_factory.tracking import MLflowTracker

session = json.loads(Path('.factory/gpu-session.json').read_text())
if (session.get('authorization') != 'explicit-user-approved-start-test-stop'
        or session.get('state') == 'STOPPED' or session['deadline'] - time.time() < 420):
    raise ValueError('active approved session with recovery margin required')
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
if pod['id'] != session['pod_id'] or pod['desiredStatus'] != 'RUNNING':
    raise ValueError('approved Pod is not running')
marker = json.loads(Path('.factory/recovery-job.json').read_text())
factory = Factory(marker['state'], policy=Policy(max_timeout_seconds=1200, max_cost_usd=2),
                  tracker=MLflowTracker(profile.tracking_uri))
job_id = marker['job_id']


def executor():
    return RecoveryExecutor(pod['publicIp'], int(pod['portMappings']['22']), 'root', profile.ssh_key_file,
        remote_root='/workspace/factory-acceptance/recovery', pod=pod,
        remote_python='/workspace/factory-acceptance/runtime/venv/bin/python',
        worker_lock_file='/workspace/factory-acceptance/runtime/containers/gpu-requirements.lock',
        hf_home='/workspace/factory-acceptance/hf-cache')


factory.store.recover(stale_after_seconds=120)
job = factory.store.get(job_id)
if job['status'] != 'FAILED':
    raise ValueError('expected stale failed job; do not duplicate an active execution')
canceled = factory.reconcile(job_id, executor())
directory = factory.directory(job_id)
first_result = json.loads((directory / 'result.json').read_text())
if canceled['status'] != 'CANCELED' or first_result['status'] != 'CANCELED':
    raise ValueError('the original GPU worker must actually have canceled')
proof = directory / 'attempts' / str(canceled['attempts']) / 'worker-result.json'
proof.parent.mkdir(parents=True, exist_ok=True)
proof.write_text(json.dumps(first_result))
required = ('adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
            'scheduler.pt', 'rng_state.pth', 'trainer_state.json')
checkpoints = sorted((directory / 'output').glob('checkpoint-*'))
checkpoint = next(p for p in checkpoints if all((p / name).is_file() for name in required))
saved = json.loads((checkpoint / 'trainer_state.json').read_text())
target = canceled['manifest']['config']['hyperparameters']['max_steps']
if not 0 < saved['global_step'] < target:
    raise ValueError('checkpoint is not intermediate')
factory.resume(job_id, checkpoint)
resumed = factory.work(executor=executor(), gpu=profile.pod_id, approved_transfer=True, target_job_id=job_id)
if resumed['status'] != 'COMPLETED':
    raise RuntimeError(str(resumed['error']))
state = json.loads((directory / 'output' / 'trainer_state.json').read_text())
plan = yaml.safe_load((directory / 'train.yaml').read_text())
history = saved.get('log_history', [])
if (state['global_step'] != target or state.get('log_history', [])[:len(history)] != history
        or not plan.get('resume_from_checkpoint', '').endswith(checkpoint.name)):
    raise ValueError('native checkpoint continuation evidence does not match')
hashes = factory.verify_artifacts(job_id)
receipt = {'job_id': job_id, 'canceled_run_id': canceled['run_id'], 'resumed_run_id': resumed['run_id'],
           'first_status': canceled['status'], 'resumed_status': resumed['status'],
           'initial_worker_result': first_result, 'worker_cancellation_observed': True,
           'checkpoint': checkpoint.name, 'checkpoint_step': saved['global_step'], 'final_step': state['global_step'],
           'checkpoint_history_preserved': True, 'native_resume_argument': plan['resume_from_checkpoint'],
           'controller_restart_recovery_verified': True, 'artifacts_verified': len(hashes),
           'simulation': False, 'synthetic_data': True}
(factory.root / 'recovery-receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
