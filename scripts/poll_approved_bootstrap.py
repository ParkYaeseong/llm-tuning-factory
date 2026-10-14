"""Read approved Pod setup progress without changing its runtime."""
import json
import os

import paramiko

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController

profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect(pod['publicIp'], port=int(pod['portMappings']['22']), username='root',
               key_filename=str(profile.ssh_key_file), timeout=20, allow_agent=False, look_for_keys=False)
with client.open_sftp() as sftp:
    ready = False
    try:
        sftp.stat('/workspace/factory-acceptance/runtime/READY')
        ready = True
    except OSError:
        pass
    with sftp.open('/workspace/factory-acceptance/runtime/bootstrap.log') as f:
        log = f.read().decode(errors='replace')
    print(json.dumps({'ready': ready, 'log_tail': log[-2400:]}))
client.close()
