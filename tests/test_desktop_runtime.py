import base64
import hashlib

import paramiko
import pytest

from tuning_factory.desktop_runtime import PinnedHostKey, validate_pod


def test_pin_mismatch_never_accepts_a_host():
    key = paramiko.RSAKey.generate(1024)
    policy = PinnedHostKey("SHA256:wrong")
    with pytest.raises(paramiko.SSHException):
        policy.missing_host_key(None, "example", key)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    PinnedHostKey(fingerprint).missing_host_key(None, "example", key)


def test_runtime_requires_running_network_volume_pod():
    pod = {"desiredStatus": "EXITED"}
    with pytest.raises(ValueError, match="running"):
        validate_pod(pod)
    pod.update(desiredStatus="RUNNING", publicIp="1.2.3.4", portMappings={"22": 123},
               networkVolume={"id": "disk"}, volumeMountPath="/workspace")
    validate_pod(pod)
    pod["volumeMountPath"] = "/other"
    with pytest.raises(ValueError, match="volume"):
        validate_pod(pod)
