"""Force cancellation after a complete real checkpoint, then resume within the same approved budget."""
import json
import os
import shlex
import threading
import time
from pathlib import Path

import paramiko
import yaml

from tuning_factory.config import AIConfig, load_config
from tuning_factory.connections import load_connection
from tuning_factory.execution import Policy, RunPodController, SSHExecutor
from tuning_factory.runner import Factory
from tuning_factory.tracking import MLflowTracker

session = json.loads(Path('.factory/gpu-session.json').read_text())
remaining = int(session['deadline'] - time.time())
if session.get('state') == 'STOPPED' or remaining < 420:
    raise ValueError('insufficient approved session time for cancellation plus resume; do not extend automatically')
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
factory = Factory('state/gpu-recovery', policy=Policy(max_timeout_seconds=1200, max_cost_usd=2),
                  tracker=MLflowTracker(profile.tracking_uri))
factory.registry.register_dataset('synthetic_smoke', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
factory.registry.register_dataset('synthetic_validation', '1', 'examples/dummy.json', export_allowed=True, synthetic=True)
data = load_config('configs/ai/integration_smoke.yaml').model_dump()
data['ai_id'] = 'integration_recovery'
data['hyperparameters']['max_steps'] = 12
data['hyperparameters']['save_steps'] = 10
data['hyperparameters']['save_total_limit'] = 1
data['evaluation_profile'] = {'kind': 'pipeline'}
data['resource_profile']['timeout_seconds'] = min(360, remaining - 120)
config = AIConfig.model_validate(data)
job = factory.submit(config, request_key='approved-recovery-' + str(time.time_ns()), simulation=False)
Path('.factory/recovery-job.json').write_text(json.dumps({'job_id': job['id'], 'state': str(factory.root)}))
directory = factory.directory(job['id'])
stopped = threading.Event()
monitor_errors = []
monitor_receipts = []


def monitor_checkpoint():
    c = paramiko.SSHClient()
    c.load_system_host_keys()
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        c.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
                  key_filename=str(profile.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
        with c.open_sftp() as sftp:
            observer = '/workspace/factory-acceptance/runtime/checkpoint-observer.py'
            sftp.put('scripts/watch_checkpoint_cancel.py', observer)
            while not stopped.wait(0.3):
                marker = directory / 'remote-attempt.json'
                if not marker.exists():
                    continue
                remote = json.loads(marker.read_text())['remote_path']
                command = ['/workspace/factory-acceptance/runtime/venv/bin/python', observer, remote,
                           '--timeout-seconds', '240', '--target-step', str(data['hyperparameters']['max_steps'])]
                _, out, err = c.exec_command(shlex.join(command), timeout=260)
                observation = out.read().decode()
                if out.channel.recv_exit_status():
                    raise RuntimeError('GPU-local checkpoint observer failed: ' + err.read().decode()[-500:])
                monitor_receipts.append(json.loads(observation))
                factory.store.cancel(job['id'])
                return
    except Exception as exc:
        monitor_errors.append(type(exc).__name__)
    finally:
        c.close()


class RecoveryExecutor(SSHExecutor):
    def upload(self, sftp, local, remote):
        marker = local / 'resume.json'
        if not marker.exists():
            return super().upload(sftp, local, remote)
        # The pinned base tokenizer is loaded from HF cache, not from the trainer checkpoint.
        # Send the selected complete trainer state, avoiding other checkpoints and duplicate tokenizers.
        checkpoint = json.loads(marker.read_text())['checkpoint']
        state_files = {'adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
                       'scheduler.pt', 'rng_state.pth', 'trainer_state.json', 'training_args.bin'}
        root_files = {'manifest.json', 'config.json', 'train.yaml', 'execution.json',
                      'remote-attempt.json', 'resume.json'}
        self.mkdir(sftp, remote)
        for path in local.rglob('*'):
            relative = path.relative_to(local)
            include = (relative.as_posix() in root_files or relative.parts[0] == 'data'
                       or (relative.parent.as_posix() == checkpoint and path.name in state_files))
            if path.is_file() and include:
                if path.is_symlink():
                    raise ValueError('checkpoint symlink rejected')
                target = remote + '/' + relative.as_posix()
                self.mkdir(sftp, target.rsplit('/', 1)[0])
                sftp.put(str(path), target)


def executor():
    return RecoveryExecutor(pod['publicIp'], int(pod['portMappings']['22']), 'root', profile.ssh_key_file,
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
if not monitor_receipts or json.loads((directory / 'result.json').read_text())['status'] != 'CANCELED':
    raise RuntimeError('worker did not actually cancel; a late control-plane request is insufficient')
required = ['adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt', 'scheduler.pt', 'rng_state.pth', 'trainer_state.json']
checkpoints = sorted((directory / 'output').glob('checkpoint-*'), key=lambda p: int(p.name.split('-')[-1]))
checkpoint = next((p for p in checkpoints if all((p / name).is_file() for name in required)), None)
if checkpoint is None:
    raise RuntimeError('no complete checkpoint retrieved from the canceled real worker')
saved_state = json.loads((checkpoint / 'trainer_state.json').read_text())
checkpoint_step = saved_state['global_step']
if not 0 < checkpoint_step < data['hyperparameters']['max_steps']:
    raise RuntimeError('cancellation did not preserve an intermediate resumable step')
factory.resume(job['id'], checkpoint)
resumed = factory.work(executor=executor(), gpu=profile.pod_id, approved_transfer=True, target_job_id=job['id'])
final_state = json.loads((directory / 'output' / 'trainer_state.json').read_text())
resume_plan = yaml.safe_load((directory / 'train.yaml').read_text())
if resumed['status'] == 'COMPLETED':
    if final_state['global_step'] != data['hyperparameters']['max_steps']:
        raise RuntimeError('resumed trainer did not reach the requested final step')
    if not resume_plan.get('resume_from_checkpoint', '').endswith(checkpoint.name):
        raise RuntimeError('native engine did not receive the original checkpoint')
    history = saved_state.get('log_history', [])
    if final_state.get('log_history', [])[:len(history)] != history:
        raise RuntimeError('resumed trainer history does not preserve checkpoint progress')
    factory.verify_artifacts(job['id'])
receipt = {'job_id': job['id'], 'canceled_run_id': canceled['run_id'], 'resumed_run_id': resumed['run_id'],
           'checkpoint': checkpoint.name, 'first_status': canceled['status'], 'resumed_status': resumed['status'],
           'checkpoint_step': checkpoint_step, 'final_step': final_state['global_step'],
           'worker_cancellation_observed': True, 'gpu_local_observer': monitor_receipts[0],
           'checkpoint_history_preserved': final_state.get('log_history', [])[:len(saved_state.get('log_history', []))]
               == saved_state.get('log_history', []),
           'native_resume_argument': resume_plan.get('resume_from_checkpoint'),
           'simulation': False, 'synthetic_data': True, 'error': resumed['error']}
(factory.root / 'recovery-receipt.json').write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
if resumed['status'] != 'COMPLETED':
    raise SystemExit(1)
