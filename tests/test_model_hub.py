import json

import httpx
import pytest

from tuning_factory.model_hub import ModelHub, ModelProfile, imported_model

REVISION = 'a' * 40


def hub(model_type='qwen3_5', extra=None):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path == '/api/models':
            return httpx.Response(200, json=[{'id': 'Qwen/Qwen3.5-0.8B', 'downloads': 10,
                                             'pipeline_tag': 'image-text-to-text', 'lastModified': '2026-03-02'}])
        if '/api/models/' in request.url.path:
            return httpx.Response(200, json={'sha': REVISION, 'gated': False})
        if request.url.path.endswith('tokenizer_config.json'):
            return httpx.Response(200, json={'chat_template': '<|im_start|>{{messages}}'})
        return httpx.Response(200, json={'model_type': model_type, 'text_config': {
            'max_position_embeddings': 262144, 'num_hidden_layers': 24, 'dtype': 'bfloat16'}, **(extra or {})})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    return ModelHub(client=client), requests


def test_search_and_inspect_modern_model_without_weight_download():
    service, requests = hub()
    assert service.search('Qwen3.5')['models'][0]['id'] == 'Qwen/Qwen3.5-0.8B'
    result = service.inspect('Qwen/Qwen3.5-0.8B')
    assert result['supported'] and result['architecture'] == 'qwen3_5'
    profile = ModelProfile.model_validate(result['profile'])
    assert profile.template == 'qwen3_5_nothink' and profile.revision == REVISION
    assert len(profile.config_sha256) == 64
    model = imported_model(profile)
    assert model['id'] == 'Qwen/Qwen3.5-0.8B' and not model['gpu_verified']
    assert not any('safetensors' in str(x.url) or '.bin' in str(x.url) for x in requests)


@pytest.mark.parametrize('model_type,extra,reason', [
    ('unknown_next_architecture', {}, 'architecture'),
    ('qwen3', {'auto_map': {'AutoModel': 'custom.py'}}, 'remote code'),
    ('qwen3', {'quantization_config': {'quant_method': 'gptq'}}, 'quantized'),
])
def test_searchable_does_not_mean_executable(model_type, extra, reason):
    service, _ = hub(model_type, extra)
    result = service.inspect('publisher/new-model')
    assert not result['supported'] and reason in ' '.join(result['reasons'])


def test_repository_reference_cannot_change_host_or_path():
    service, requests = hub()
    for model in ['https://elsewhere/model', '../secret', 'org/model/resolve/main', 'org/model?token=secret']:
        with pytest.raises(ValueError):
            service.inspect(model)
    assert requests == []


def test_imported_modern_profile_binds_model_revision_and_stage():
    from tuning_factory.capabilities import compatibility
    from tuning_factory.config import AIConfig, load_config
    service, _ = hub()
    profile = service.inspect('Qwen/Qwen3.5-0.8B')['profile']
    value = load_config('configs/ai/paper_extraction.yaml').model_dump()
    value.update(base_model=profile['repo_id'], model_revision=REVISION, tokenizer_revision=REVISION,
                 template=profile['template'], model_profile=profile)
    assert AIConfig.model_validate(value).model_profile.architecture == 'qwen3_5'
    result = compatibility({**value, 'training_stage': 'ppo'})
    assert not result['valid'] and 'ppo' not in result['supported_stages']
    with pytest.raises(ValueError, match='revision'):
        AIConfig.model_validate({**value, 'model_revision': 'b' * 40, 'tokenizer_revision': 'b' * 40})


def test_current_qwen36_uses_its_upstream_template():
    service, _ = hub('qwen3_5_moe')
    result = service.inspect('Qwen/Qwen3.6-35B-A3B')
    assert result['supported'] and result['profile']['template'] == 'qwen3_6'


def test_gemma4_is_discoverable_with_text_training_scope():
    service, _ = hub('gemma4')
    result = service.inspect('google/gemma-4-E2B-it')
    assert result['supported'] and result['supported_stages'] == ['sft']


def test_legacy_ppo_runtime_does_not_offer_a_missing_qwen3_template():
    service, _ = hub('qwen3')
    assert 'ppo' not in service.inspect('Qwen/Qwen3-0.6B')['supported_stages']


def test_phi_uses_the_engine_template_name():
    service, _ = hub('phi3')
    assert service.inspect('microsoft/Phi-3-mini-4k-instruct')['profile']['template'] == 'phi'


def test_import_registration_persists_only_checked_metadata(tmp_path, monkeypatch):
    from tuning_factory.desktop import DesktopService
    app = DesktopService(tmp_path)
    service, _ = hub()
    monkeypatch.setattr(app, 'hub', lambda: service, raising=False)
    inspected = app.dispatch('model_inspect', {'model': 'Qwen/Qwen3.5-0.8B'})
    registered = app.dispatch('model_import', {'model': inspected['repo_id'], 'revision': inspected['revision'],
                                              'config_sha256': inspected['profile']['config_sha256']})
    assert registered['model']['id'] == 'Qwen/Qwen3.5-0.8B'
    loaded = DesktopService(tmp_path).dispatch('catalog', {})['models']
    assert any(item['id'] == 'Qwen/Qwen3.5-0.8B' for item in loaded)
    assert 'secret' not in json.dumps(list((tmp_path / 'model-profiles').glob('*.json')), default=str)


def test_gpu_preflight_checks_actual_pinned_config_bytes(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from tuning_factory.config import AIConfig, load_config
    from tuning_factory.worker import runtime_preflight
    service, _ = hub()
    profile = service.inspect('Qwen/Qwen3.5-0.8B')['profile']
    value = load_config('configs/ai/paper_extraction.yaml').model_dump()
    value.update(base_model=profile['repo_id'], model_revision=REVISION, tokenizer_revision=REVISION,
                 template=profile['template'], model_profile=profile)
    source = tmp_path / 'config.json'
    source.write_text('{"model_type":"forged"}')
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, device_count=lambda: 1, is_bf16_supported=lambda: True)))
    observed = SimpleNamespace(model_type='qwen3_5', text_config=SimpleNamespace(num_hidden_layers=24))
    monkeypatch.setitem(sys.modules, 'transformers', SimpleNamespace(AutoConfig=SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: observed)))
    monkeypatch.setitem(sys.modules, 'huggingface_hub', SimpleNamespace(hf_hub_download=lambda **kwargs: str(source)))
    with pytest.raises(ValueError, match='metadata hash'):
        runtime_preflight(AIConfig.model_validate(value))
