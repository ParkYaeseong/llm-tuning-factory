import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from tuning_factory.execution import SSHExecutor


def test_download_and_cache_verify_without_loading_whole_artifact(tmp_path, monkeypatch):
    payload = b'synthetic model shard' * 10000
    expected = hashlib.sha256(payload).hexdigest()
    class SFTP:
        downloads = 0
        def listdir_attr(self, remote):
            return [SimpleNamespace(filename='model.safetensors', st_mode=stat.S_IFREG)]
        def get(self, source, destination):
            self.downloads += 1
            Path(destination).write_bytes(payload)
    sftp = SFTP()
    monkeypatch.setattr(Path, 'read_bytes', lambda self: pytest.fail('whole model file read'))
    executor = SSHExecutor.__new__(SSHExecutor)
    executor.download(sftp, '/remote', tmp_path, {'model.safetensors': expected})
    executor.download(sftp, '/remote', tmp_path, {'model.safetensors': expected})
    assert sftp.downloads == 1
