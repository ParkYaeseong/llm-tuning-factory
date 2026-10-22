import json

from tuning_factory import desktop_runtime


def test_ppo_runtime_isolated_from_primary_environment():
    assert desktop_runtime.runtime_path('ppo') != desktop_runtime.runtime_path('sft')
    assert desktop_runtime.runtime_path() == desktop_runtime.runtime_path('sft')


def test_ppo_preflight_uses_exact_legacy_lock(monkeypatch):
    import ast

    from tuning_factory.desktop import assets_root
    from tuning_factory.manifest import source_hash
    from tuning_factory.registry import digest

    expected = {'source': source_hash(), 'engine': '0.9.3', 'cuda': True,
                'lock': digest((assets_root() / 'containers/gpu-ppo-requirements.lock').read_bytes())}
    calls = []
    def run(client, argv, **kw):
        calls.append(argv)
        return '' if argv[0] == 'sh' else json.dumps(expected)
    monkeypatch.setattr(desktop_runtime, 'command', run)
    assert desktop_runtime.check(None, 'ppo') == expected
    assert 'ppo' in calls[-1][0]
    ast.parse(calls[-1][2])


def test_full_ppo_reward_model_requires_identical_tokenizer(monkeypatch):
    import sys
    from types import SimpleNamespace

    from tuning_factory.config import AIConfig, load_config
    from tuning_factory.worker import runtime_preflight
    value = load_config('configs/ai/paper_extraction.yaml').model_dump()
    value.update(training_stage='ppo', stage_options={'reward_model': '/workspace/reward',
                  'reward_model_type': 'full', 'reward_model_sha256': 'a' * 64})
    fake_cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, is_bf16_supported=lambda: True)
    class AutoConfig:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return SimpleNamespace(model_type='qwen2', num_hidden_layers=24)
    class AutoTokenizer:
        @staticmethod
        def from_pretrained(model, **kwargs):
            return SimpleNamespace(get_vocab=lambda: {'token': 2 if model == '/workspace/reward' else 1})
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=fake_cuda))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoConfig=AutoConfig, AutoTokenizer=AutoTokenizer))
    import pytest
    with pytest.raises(ValueError, match='tokenizer'):
        runtime_preflight(AIConfig.model_validate(value))


def test_operation_profile_pins_model_operations_to_the_primary_runtime():
    import pytest

    from tuning_factory.runtime_profiles import MODEL_OPS_PINS, engine_profile, operation_profile
    assert operation_profile('train', 'primary') == engine_profile('sft')
    assert operation_profile('train', 'ppo') == engine_profile('ppo')
    assert operation_profile('evaluate', 'primary') == engine_profile('sft')
    for kind in ('merge', 'materialize'):
        profile = operation_profile(kind, 'primary')
        assert {key: profile[key] for key in engine_profile('sft')} == engine_profile('sft')
        assert profile['model_ops_pins'] == dict(MODEL_OPS_PINS)
        with pytest.raises(ValueError, match='^model operation requires the primary runtime with pinned PEFT 0.18.1$'):
            operation_profile(kind, 'ppo')
    with pytest.raises(ValueError, match='local selection never dispatches to a GPU worker'):
        operation_profile('select', 'primary')
    with pytest.raises(ValueError, match='unsupported operation kind'):
        operation_profile('shell', 'primary')
    with pytest.raises(ValueError, match='unsupported runtime profile'):
        operation_profile('train', 'cuda')


def test_model_ops_pin_drift_is_reported_without_importing_the_runtime(monkeypatch):
    import sys
    from importlib import metadata

    from tuning_factory.runtime_profiles import MODEL_OPS_PINS, model_ops_pin_drift
    for name in ('torch', 'peft', 'transformers', 'safetensors'):
        monkeypatch.setitem(sys.modules, name, None)
    installed = {**MODEL_OPS_PINS, 'torch': '2.7.1+cu126'}

    def version(name):
        if name not in installed:
            raise metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(metadata, 'version', version)
    assert model_ops_pin_drift() == ()
    installed['peft'] = '0.18.0'
    del installed['safetensors']
    assert model_ops_pin_drift() == ('peft', 'safetensors')
