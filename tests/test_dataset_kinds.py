import json

import pytest

from tuning_factory.registry import Registry, dataset_descriptor, parse_dataset


def test_existing_instruction_registration_stays_compatible(tmp_path):
    path = tmp_path / 'data.json'
    path.write_text('[{"instruction":"q","output":"a"}]')
    registry = Registry(tmp_path / 'registry')
    record = registry.register_dataset('old', '1', path)
    assert 'kind' not in record
    assert registry.get_dataset('old', '1') == record


@pytest.mark.parametrize(('kind', 'row'), [
    ('text', {'text': 'continued pretraining document'}),
    ('prompt', {'instruction': 'write an answer'}),
    ('preference', {'instruction': 'q', 'chosen': 'good', 'rejected': 'bad'}),
    ('kto', {'instruction': 'q', 'output': 'answer', 'kto_tag': True}),
])
def test_register_stage_data_is_immutable(tmp_path, kind, row):
    path = tmp_path / 'data.jsonl'
    path.write_text(json.dumps(row) + '\n')
    registry = Registry(tmp_path / 'registry')
    record = registry.register_dataset('sample', '1', path, kind=kind)
    assert record['kind'] == kind
    assert registry.get_dataset('sample', '1') == record
    assert parse_dataset(path.read_bytes(), kind)[0] == [row]
    path.write_text(json.dumps({**row, 'changed': True}))
    with pytest.raises(ValueError, match='immutable'):
        registry.register_dataset('sample', '1', path, kind=kind)


@pytest.mark.parametrize(('kind', 'row'), [
    ('text', {'instruction': 'q', 'output': 'a'}),
    ('preference', {'instruction': 'q', 'chosen': 'good'}),
    ('preference', {'instruction': 'q', 'chosen': 'same', 'rejected': 'same'}),
    ('kto', {'instruction': 'q', 'output': 'a', 'kto_tag': 'true'}),
    ('prompt', {'messages': [{'role': 'assistant', 'content': 'no question'}]}),
])
def test_wrong_kind_rejects_before_registration(kind, row):
    with pytest.raises(ValueError):
        parse_dataset(json.dumps([row]).encode(), kind)


def test_preference_sharegpt_descriptor_preserves_real_columns():
    raw = json.dumps([{'conversations': [{'from': 'human', 'value': 'q'}],
                       'chosen': {'from': 'gpt', 'value': 'yes'},
                       'rejected': {'from': 'gpt', 'value': 'no'}}]).encode()
    descriptor = dataset_descriptor(raw, 'preference', 'dataset.json')
    assert descriptor['ranking'] is True
    assert descriptor['columns']['messages'] == 'conversations'
    assert descriptor['tags']['user_tag'] == 'human'
    assert descriptor['columns']['chosen'] == 'chosen'


def test_prompt_descriptor_never_references_absent_answers_or_queries():
    descriptor = dataset_descriptor(b'[{"instruction":"a prompt"}]', 'prompt', 'dataset.json')
    assert descriptor['columns'] == {'prompt': 'instruction'}


def test_text_metadata_does_not_select_conversation_converter():
    raw = b'[{"text":"the document","messages":["metadata only"]}]'
    assert dataset_descriptor(raw, 'text', 'data.json')['formatting'] == 'alpaca'


@pytest.mark.parametrize('rows,kind', [
    ([{'messages': [{'role': 'user', 'content': 'q'}, {'role': 'user', 'content': 'again'},
                     {'role': 'assistant', 'content': 'a'}]}], 'instruction'),
    ([{'messages': [{'role': 'user', 'content': 'q'}]}], 'prompt'),
    ([{'instruction': 'q', 'output': 'a', 'input': 'context'}, {'instruction': 'q2', 'output': 'a2'}], 'instruction'),
])
def test_engine_incompatible_record_shapes_reject_before_submission(rows, kind):
    with pytest.raises(ValueError):
        parse_dataset(json.dumps(rows).encode(), kind)


def test_artifact_digest_streams_large_file(tmp_path, monkeypatch):
    from tuning_factory.registry import file_digest
    path = tmp_path / 'model.safetensors'
    path.write_bytes(b'synthetic shard' * 10000)
    import hashlib
    expected = hashlib.sha256(b'synthetic shard' * 10000).hexdigest()
    monkeypatch.setattr(type(path), 'read_bytes', lambda self: pytest.fail('whole artifact read'))
    assert file_digest(path) == expected


def test_s3_source_provenance_is_safe_and_identity_is_immutable(tmp_path):
    path = tmp_path / 'data.json'
    path.write_text('[{"instruction":"q","output":"a"}]')
    registry = Registry(tmp_path / 'registry')
    source = {'kind': 's3', 'bucket': 'sample', 'key': 'data.json', 'version_id': 'v1',
              'etag': 'opaque', 'endpoint_url': 'https://s3.example.org', 'region': 'region'}
    record = registry.register_dataset('froms3', '1', path, source_metadata=source)
    assert record['source'] == source
    with pytest.raises(ValueError, match='source'):
        registry.register_dataset('bad', '1', path, source_metadata={**source, 'secret_access_key': 'secret'})
    with pytest.raises(ValueError, match='source'):
        registry.register_dataset('bad', '1', path, source_metadata={**source, 'endpoint_url': 'https://user:secret@s3.org'})
