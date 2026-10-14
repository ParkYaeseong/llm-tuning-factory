import json
import runpy

import pytest


def test_checkpoint_observer_waits_for_complete_intermediate_state(tmp_path):
    watch = runpy.run_path('scripts/watch_checkpoint_cancel.py')['watch']
    checkpoint = tmp_path / 'output' / 'checkpoint-2'
    checkpoint.mkdir(parents=True)
    for name in ('adapter_config.json', 'adapter_model.safetensors', 'scheduler.pt', 'rng_state.pth'):
        (checkpoint / name).write_text('test-only fixture')
    (checkpoint / 'trainer_state.json').write_text(json.dumps({'global_step': 2}))
    with pytest.raises(TimeoutError):
        watch(tmp_path, 0.05, 12)
    assert not (tmp_path / 'cancel').exists()
    (checkpoint / 'optimizer.pt').write_text('test-only optimizer fixture')
    with pytest.raises(TimeoutError):
        watch(tmp_path, 0.05, 2)
    receipt = watch(tmp_path, 1, 12)
    assert receipt == {'checkpoint': 'checkpoint-2', 'step': 2, 'cancel_requested': True}
    assert (tmp_path / 'cancel').exists()
