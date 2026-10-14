"""GPU-local observer: request cancellation immediately after a complete checkpoint."""
import argparse
import json
import time
from pathlib import Path


def watch(directory, timeout_seconds, target_step):
    directory = Path(directory)
    deadline = time.monotonic() + timeout_seconds
    required = ('adapter_config.json', 'adapter_model.safetensors', 'optimizer.pt',
                'scheduler.pt', 'rng_state.pth', 'trainer_state.json')
    while time.monotonic() < deadline:
        for checkpoint in sorted((directory / 'output').glob('checkpoint-*')):
            try:
                if not all((checkpoint / name).is_file() and (checkpoint / name).stat().st_size > 0
                           for name in required):
                    continue
                state = json.loads((checkpoint / 'trainer_state.json').read_text())
                step = int(state['global_step'])
                if not 0 < step < target_step or checkpoint.name != f'checkpoint-{step}':
                    continue
                (directory / 'cancel').touch(exist_ok=True)
                return {'checkpoint': checkpoint.name, 'step': step, 'cancel_requested': True}
            except (OSError, ValueError, KeyError):
                continue
        time.sleep(0.02)
    raise TimeoutError('no complete intermediate checkpoint before observer deadline')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--timeout-seconds', type=int, required=True)
    parser.add_argument('--target-step', type=int, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not directory.is_relative_to('/workspace/factory-acceptance/recovery'):
        raise ValueError('observer may only target the isolated recovery namespace')
    print(json.dumps(watch(directory, args.timeout_seconds, args.target_step)))
