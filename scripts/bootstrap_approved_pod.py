"""Install only into the approved session's isolated directory; do not modify existing venvs."""
import json
import os
import shlex
import time
from pathlib import Path

import paramiko

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController, SSHExecutor

session = json.loads(Path('.factory/gpu-session.json').read_text())
if session['authorization'] != 'explicit-user-approved-start-test-stop' or time.time() >= session['deadline']:
    raise ValueError('approved session missing or expired')
profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
               key_filename=str(profile.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
root = '/workspace/factory-acceptance/runtime'
with client.open_sftp() as sftp:
    executor = SSHExecutor('unused', 22, 'root', 'unused', client=client)
    executor.mkdir(sftp, root + '/containers')
    sftp.put('containers/gpu-requirements.lock', root + '/containers/gpu-requirements.lock')
    sftp.put('dist/llm_tuning_factory-0.1.0-py3-none-any.whl', root + '/factory.whl')
script = '''#!/bin/bash
set -eu
export UV_CACHE_DIR=/workspace/factory-acceptance/uv-cache
uv venv --python python3 /workspace/factory-acceptance/runtime/venv
uv pip sync --python /workspace/factory-acceptance/runtime/venv/bin/python --require-hashes /workspace/factory-acceptance/runtime/containers/gpu-requirements.lock
cp /workspace/factory-acceptance/runtime/factory.whl /workspace/factory-acceptance/runtime/llm_tuning_factory-0.1.0-py3-none-any.whl
uv pip install --python /workspace/factory-acceptance/runtime/venv/bin/python --no-deps /workspace/factory-acceptance/runtime/llm_tuning_factory-0.1.0-py3-none-any.whl
touch /workspace/factory-acceptance/runtime/READY
'''
with client.open_sftp() as sftp:
    with sftp.open(root + '/bootstrap.sh', 'w') as f:
        f.write(script)
command = 'nohup bash ' + shlex.quote(root + '/bootstrap.sh') + ' > ' + shlex.quote(root + '/bootstrap.log') + ' 2>&1 < /dev/null &'
_, stdout, _ = client.exec_command(command, timeout=20)
print(json.dumps({'bootstrap_launched': stdout.channel.recv_exit_status() == 0, 'root': root,
                  'remaining_seconds': int(session['deadline'] - time.time())}))
client.close()
