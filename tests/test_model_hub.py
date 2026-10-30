import json

import httpx
import pytest

from tuning_factory.model_hub import ModelHub, ModelProfile, imported_model

REVISION = 'a' * 40


def hub(model_type='qwen3_5', extra=None, info=None):
    requests = []
    def respond(request):
        requests.append(request)
        if request.url.path == '/api/models':
            return httpx.Response(200, json=[{'id': 'Qwen/Qwen3.5-0.8B', 'downloads': 10,
                                             'pipeline_tag': 'image-text-to-text', 'lastModified': '2026-03-02'}])
        if '/api/models/' in request.url.path:
            return httpx.Response(200, json={'sha': REVISION, 'gated': False, **(info or {})})
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
    assert result['supported'] and result['supported_stages'] == ['pt', 'sft', 'dpo', 'kto']


@pytest.mark.parametrize('per_layer,experts,template,moe', [(256, None, 'gemma4n', False), (0, 128, 'gemma4', True)])
def test_hub_profile_records_sizes_and_the_gemma4_template(per_layer, experts, template, moe):
    service, _ = hub('gemma4', {'tie_word_embeddings': True, 'text_config': {
        'max_position_embeddings': 131072, 'num_hidden_layers': 35, 'hidden_size': 1536,
        'hidden_size_per_layer_input': per_layer, 'num_experts': experts, 'dtype': 'bfloat16'}},
        info={'safetensors': {'total': 5123178051}})
    profile = service.inspect('google/gemma-4-E2B-it')['profile']
    assert profile['template'] == template
    assert (profile['parameters_total'], profile['hidden_size'], profile['hidden_layers'], profile['moe'],
            profile['tie_word_embeddings']) == (5123178051, 1536, 35, moe, True)
    model = imported_model(profile)
    assert (model['parameters_total'], model['hidden_size'], model['num_layers'], model['parameters_b']) == (
        5123178051, 1536, 35, 5.12)
    assert model['supported_stages'] == ['pt', 'sft', 'dpo', 'kto'] and 'oft' not in model['methods']


@pytest.mark.parametrize('bad', [True, '1536', 0, -1, 10 ** 12, 1.5, [1], {'n': 1}])
def test_one_malformed_hub_fact_never_blocks_the_import(bad):
    service, _ = hub('gemma4', {'tie_word_embeddings': 'yes', 'text_config': {
        'max_position_embeddings': bad, 'num_hidden_layers': bad, 'hidden_size': bad,
        'hidden_size_per_layer_input': bad, 'num_experts': bad, 'tie_word_embeddings': False, 'dtype': 'bfloat16'}},
        info={'safetensors': {'total': 'many'}})
    result = service.inspect('google/gemma-4-E2B-it')
    assert result['supported'] and result['profile'] is not None
    profile = result['profile']
    assert (profile['template'], profile['max_length'], profile['context_length'], profile['hidden_layers'],
            profile['moe'], profile['tie_word_embeddings']) == ('gemma4', 32768, 32768, None, False, False)
    assert 'parameters_total' not in profile and 'hidden_size' not in profile
    assert result['max_length'] == 32768 and result['hidden_layers'] is None and result['hidden_size'] is None


@pytest.mark.parametrize('model_type,top,text,tied', [
    ('qwen3_5', True, False, True), ('qwen3_5', None, False, False), ('qwen3_5', None, True, True),
    ('qwen3_5', 'yes', None, None), ('qwen3_5', None, None, None), ('qwen3', None, None, None),
    ('gemma4', None, None, True), ('gemma4_text', None, None, True), ('gemma4', None, False, False)])
def test_tied_embeddings_fall_back_from_the_top_level_to_text_config_to_the_architecture(model_type, top, text, tied):
    config = {'max_position_embeddings': 32768, 'num_hidden_layers': 24}
    extra = {'text_config': {**config, **({} if text is None else {'tie_word_embeddings': text})},
             **({} if top is None else {'tie_word_embeddings': top})}
    service, _ = hub(model_type, extra)
    profile = service.inspect('someone/model')['profile']
    assert profile.get('tie_word_embeddings') == tied and ('tie_word_embeddings' in profile) == (tied is not None)


def test_an_untied_hub_model_without_a_tie_fact_may_train_its_lm_head():
    from tuning_factory.capabilities import compatibility
    service, _ = hub('qwen3', {'text_config': {'max_position_embeddings': 32768, 'num_hidden_layers': 28}})
    profile = service.inspect('someone/qwen3-untied')['profile']
    result = compatibility({'base_model': profile['repo_id'], 'model_revision': REVISION, 'model_profile': profile,
                            'template': profile['template'], 'training_method': 'freeze',
                            'finetuning_options': {'freeze_extra_modules': 'lm_head'}})
    assert result['valid'], result['errors']


@pytest.mark.parametrize('safetensors',['5123178051', [5123178051], 5123178051, None])
def test_a_safetensors_summary_that_is_not_an_object_is_ignored(safetensors):
    service, _ = hub(info={'safetensors': safetensors})
    profile = service.inspect('Qwen/Qwen3.5-0.8B')['profile']
    assert 'parameters_total' not in profile and profile['hidden_layers'] == 24


@pytest.mark.parametrize('total', [True, '873438784', 0, -5, 1.5, 10 ** 16])
def test_hub_parameter_totals_must_be_positive_integers(total):
    service, _ = hub(info={'safetensors': {'total': total}})
    assert 'parameters_total' not in service.inspect('Qwen/Qwen3.5-0.8B')['profile']


def test_imported_model_lists_sizes_only_with_a_parameter_total():
    profile = ModelProfile(repo_id='someone/model', revision=REVISION, config_sha256='c' * 64, architecture='qwen3_5',
                           template='qwen3_5_nothink', hidden_layers=24, hidden_size=1024, moe=True,
                           tie_word_embeddings=True)
    model = imported_model(profile)
    assert not {'parameters_total', 'parameters_b', 'hidden_size', 'num_layers', 'text_parameters'} & set(model)
    assert (model['moe'], model['tie_word_embeddings']) == (True, True)


def test_profiles_without_size_facts_keep_their_exact_bytes():
    old = {'repo_id': 'Qwen/Qwen3.5-0.8B', 'revision': REVISION, 'config_sha256': 'c' * 64,
           'architecture': 'qwen3_5', 'template': 'qwen3_5_nothink', 'max_length': 32768,
           'context_length': 262144, 'hidden_layers': 24, 'recommended_precision': 'bfloat16', 'gated': False}
    profile = ModelProfile.model_validate(old)
    assert profile.model_dump() == old and profile.model_dump_json() == json.dumps(old, separators=(',', ':'))
    assert 'parameters_total' not in imported_model(profile)


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
    entry = next(item for item in loaded if item['id'] == 'Qwen/Qwen3.5-0.8B')
    assert entry.get('source') != 'huggingface' and entry['template'] == 'qwen3_5_nothink'
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
