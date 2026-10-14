"""Read logs of the approved synthetic job; never inspect unrelated workloads."""
import json
import os
from pathlib import Path

import paramiko

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController

local = json.loads(Path('.factory/smoke-job.json').read_text())
directory = Path(local['state']) / 'jobs' / local['job_id']
marker = directory / 'remote-attempt.json'
if not marker.exists():
    print('local preparation or tracking initialization still in progress')
    raise SystemExit(0)
remote = json.loads(marker.read_text())['remote_path']
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
               key_filename=str(profile.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
with client.open_sftp() as sftp:
    result = {'job_id': local['job_id']}
    for name in ['result.json', 'training-health.json', 'worker-error.log', 'train.log', 'predict.log', 'worker.log']:
        try:
            with sftp.open(remote + '/' + name) as f:
                text = f.read().decode(errors='replace')
            result[name] = text[-2400:]
        except OSError:
            pass
    print(json.dumps(result, ensure_ascii=True))
client.close()
