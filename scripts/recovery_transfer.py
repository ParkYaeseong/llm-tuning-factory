"""Transfer only selected trainer state for the isolated acceptance resume."""
import json

from tuning_factory.execution import SSHExecutor


class RecoveryExecutor(SSHExecutor):
    def upload(self, sftp, local, remote):
        marker = local / 'resume.json'
        if not marker.exists():
            return super().upload(sftp, local, remote)
        # Tokenization uses the exact pinned base revision already present in HF cache.
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
