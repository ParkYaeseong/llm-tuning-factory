"""Apply a reviewed dependency lock only to the session's isolated worker venv."""
import json
import os
import time
from pathlib import Path

import paramiko

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController

session = json.loads(Path('.factory/gpu-session.json').read_text())
if time.time() >= session['deadline']:
    raise ValueError('approved session expired')
p = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(p.pod_id)
c = paramiko.SSHClient()
c.load_system_host_keys()
c.set_missing_host_key_policy(paramiko.RejectPolicy())
c.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
          key_filename=str(p.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
root = '/workspace/factory-acceptance/runtime'
with c.open_sftp() as sftp:
    sftp.put('containers/gpu-requirements.lock', root + '/containers/gpu-requirements.lock')
    sftp.put('dist/llm_tuning_factory-0.1.0-py3-none-any.whl', root + '/llm_tuning_factory-0.1.0-py3-none-any.whl')
command = ('uv pip sync --python ' + root + '/venv/bin/python --require-hashes --torch-backend cu128 '
           + root + '/containers/gpu-requirements.lock')
command += ' && uv pip install --python ' + root + '/venv/bin/python --no-deps ' + root + '/llm_tuning_factory-0.1.0-py3-none-any.whl'
_, out, err = c.exec_command(command, timeout=180)
print(out.read().decode())
print(err.read().decode()[-2000:])
if out.channel.recv_exit_status():
    raise RuntimeError('isolated runtime update failed')
c.close()
