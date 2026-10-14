"""Read-only SSH runtime inventory for the approved test Pod."""
import json
import os
import shlex

import paramiko

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController

profile = load_connection('.factory/connection.json')
pod = RunPodController(os.environ['RUNPOD_API_KEY']).status(profile.pod_id)
host, port = pod.get('publicIp'), (pod.get('portMappings') or {}).get('22')
if not host or not port:
    print(json.dumps({'waiting_for_ssh': True, 'status': pod.get('desiredStatus')}))
    raise SystemExit(0)
client = paramiko.SSHClient()
client.load_system_host_keys()
client.set_missing_host_key_policy(paramiko.RejectPolicy())
client.connect(host, port=int(port), username='root', key_filename=str(profile.ssh_key_file),
               timeout=20, allow_agent=False, look_for_keys=False)
code = """import json,sys,glob,subprocess,importlib.metadata as m
print(json.dumps({'python':sys.version,'venv_pythons':glob.glob('/workspace/venvs/*/bin/python'),'uv':subprocess.run(['which','uv'],capture_output=True,text=True).stdout.strip()}))
p=subprocess.run(['nvidia-smi','--query-gpu=name,memory.used,memory.total','--format=csv,noheader'],capture_output=True,text=True);print(p.stdout)
for name in ['torch','llamafactory','transformers','peft','datasets','accelerate','bitsandbytes']:
 try:print(name,m.version(name))
 except m.PackageNotFoundError:print(name,'not-installed-in-system-python')
"""
_, stdout, stderr = client.exec_command(shlex.join(['python3','-c',code]),timeout=30)
print(stdout.read().decode())
error = stderr.read().decode()
if error:
    print(error[:1000])
client.close()
