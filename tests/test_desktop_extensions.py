import json
from pathlib import Path

import pytest

from tuning_factory.desktop import DesktopService, compare_runs


def test_catalog_and_capabilities_share_available_stages(tmp_path):
    app = DesktopService(tmp_path)
    catalog = app.dispatch('catalog', {})
    assert {x['id'] for x in catalog['training_stages']} == {'pt', 'sft', 'rm', 'ppo', 'dpo', 'kto'}
    config = catalog['templates'][0]['config']
    assert app.dispatch('capabilities', {'config': config})['valid']
    config['training_stage'] = 'ppo'
    result = app.dispatch('capabilities', {'config': config})
    assert not result['valid'] and any('reward_model' in x for x in result['errors'])


def test_plan_rejects_stage_data_mismatch(tmp_path):
    app = DesktopService(tmp_path)
    source = tmp_path / 'instruction.json'
    source.write_text('[{"instruction":"q","output":"a"}]')
    app.dispatch('dataset', {'dataset_id': 'dummy', 'version': '1', 'path': str(source)})
    config = app.dispatch('catalog', {})['templates'][0]['config']
    config['training_stage'] = 'dpo'
    assert not app.dispatch('capabilities', {'config': config})['valid']
    with pytest.raises(ValueError, match='dataset kind'):
        app.dispatch('plan', {'config': config})


def test_cross_stage_training_losses_never_get_delta():
    base = {'ai_id': 'ai', 'validation_sha256': 'hash', 'evaluation_kind': 'json',
            'status': 'FINISHED', 'simulation': False, 'metrics': {'train_loss': 1},
            'config': {'training_stage': 'sft'}}
    other = {**base, 'config': {'training_stage': 'dpo'}}
    assert not compare_runs(base, other)['comparable']


def test_s3_import_requires_approval_and_preserves_immutable_provenance(tmp_path, monkeypatch):
    app = DesktopService(tmp_path)
    class Storage:
        calls = 0
        def download(self, bucket, key, destination_directory, expected, maximum_bytes):
            self.calls += 1
            assert maximum_bytes == 32 * 1024 * 1024
            target = Path(destination_directory) / 'random.json'
            target.write_text('[{"instruction":"q","output":"a"}]')
            return {'path': str(target), 'source': {'kind': 's3', 'bucket': bucket, 'key': key,
                                                  'version_id': 'fixed', 'etag': 'tag'}}
    storage = Storage()
    monkeypatch.setattr(app, 'storage', lambda: storage, raising=False)
    data = {'bucket': 'test', 'key': 'data.json', 'dataset_id': 'froms3', 'version': '1',
            'expected': {'version_id': 'fixed', 'etag': 'tag', 'size': 34}}
    with pytest.raises(ValueError, match='approval'):
        app.dispatch('s3_import', data)
    assert storage.calls == 0
    record = app.dispatch('s3_import', {**data, 'approved': True})
    assert record['source']['version_id'] == 'fixed' and not record['export_allowed']
    assert not list((tmp_path / 's3-imports').glob('*'))
    assert app.factory.registry.dataset_path(record).is_file()


def test_stage_preview_and_registry_support_text_dataset(tmp_path):
    app = DesktopService(tmp_path)
    source = tmp_path / 'text.jsonl'
    source.write_text(json.dumps({'text': 'a document'}) + '\n')
    preview = app.dispatch('dataset_preview', {'path': str(source), 'kind': 'text'})
    assert preview['rows'] == 1 and preview['kind'] == 'text'
    result = app.dispatch('dataset', {'path': str(source), 'dataset_id': 'corpus', 'version': '1', 'kind': 'text'})
    assert result['kind'] == 'text'


def test_model_revision_rejects_architecture_mismatch(monkeypatch, tmp_path):
    import httpx
    def response(url, **kwargs):
        payload = {'sha': 'a' * 40} if '/api/models/' in url else {'model_type': 'unknown'}
        return httpx.Response(200, json=payload, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', response)
    with pytest.raises(ValueError, match='architecture'):
        DesktopService(tmp_path).dispatch('model_revision', {'model': 'Qwen/Qwen2.5-0.5B-Instruct'})


def test_reward_metrics_require_matching_validation_provenance(tmp_path):
    from tuning_factory.config import AIConfig
    from tuning_factory.runner import Factory
    config = DesktopService(tmp_path / 'control').dispatch('catalog', {})['templates'][0]['config']
    config.update(training_stage='rm', evaluation_profile={'kind': 'reward', 'dataset_id': 'heldout',
                                                          'dataset_version': '1'})
    config = AIConfig.model_validate(config)
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'reward_metrics.json').write_text(json.dumps({'validation_sha256': 'expected',
                                                           'metrics': {'eval_accuracy': 0.75}}))
    metrics, quality = Factory.evaluate(tmp_path, config, False, 'expected')
    assert metrics['eval_accuracy'] == 0.75 and quality['quality_status'] == 'measured'
    with pytest.raises(ValueError, match='validation'):
        Factory.evaluate(tmp_path, config, False, 'different')
