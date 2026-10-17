import hashlib
import importlib
import json
import struct
from pathlib import Path

import pytest
from pydantic import ValidationError


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module('tuning_factory.model_artifacts')
    except ModuleNotFoundError:
        pytest.fail('immutable model artifact and input contracts are missing')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def write_tensor(path, *, name='model.weight', shape=(2, 2), dtype='F32', offsets=(0, 16)):
    header = canonical({name: {'dtype': dtype, 'shape': list(shape), 'data_offsets': list(offsets)}})
    path.write_bytes(struct.pack('<Q', len(header)) + header + bytes(16))


def fixture_model(tmp_path, *, adapter=False, marker='parent'):
    module = api()
    directory = tmp_path / marker
    directory.mkdir()
    config = canonical({'model_type': 'qwen2', 'hidden_size': 2})
    (directory / 'config.json').write_bytes(config)
    (directory / 'tokenizer_config.json').write_text('{}', encoding='utf-8')
    (directory / 'tokenizer.json').write_text('{"version":"1.0"}', encoding='utf-8')
    tokenizer = sha(canonical([
        {'path': 'tokenizer.json', 'size': 17, 'sha256': sha(b'{"version":"1.0"}')},
        {'path': 'tokenizer_config.json', 'size': 2, 'sha256': sha(b'{}')},
    ]))
    root = module.HFRootRef(repo_id='test/tiny', revision='a' * 40,
                            config_sha256=sha(config), tokenizer_fingerprint=tokenizer)
    filename = 'adapter_model.safetensors' if adapter else 'model.safetensors'
    write_tensor(directory / filename)
    metadata = None
    if adapter:
        metadata = module.AdapterMetadata(method='lora', rank=2, alpha=4, target_modules=('q_proj',))
        (directory / 'adapter_config.json').write_bytes(canonical({
            'peft_type': 'LORA', 'r': 2, 'lora_alpha': 4, 'lora_dropout': 0,
            'target_modules': ['q_proj'], 'bias': 'none', 'use_rslora': False,
            'use_dora': False, 'base_model_name_or_path': 'test/tiny',
        }))
    descriptor = module.ModelDescriptor(
        producer_job_id='job-1', producer_attempt_id='attempt-1', producer_manifest_sha256='b' * 64,
        storage_kind='adapter' if adapter else 'full_model', semantic_kind='causal_lm',
        has_value_head=False, root=root, effective_base=root, config_sha256=sha(config),
        tokenizer_fingerprint=tokenizer, adapter=metadata,
        tensors=(module.TensorMetadata(file=filename, name='model.weight', shape=(2, 2), dtype='F32'),),
        synthetic=False, simulation=False,
    )
    return directory, descriptor


def updated(model, **changes):
    return type(model).model_validate({**model.model_dump(), **changes})


def with_value_head(directory, descriptor):
    write_tensor(directory / 'value_head.safetensors', name='v_head.summary.weight')
    tensor = api().TensorMetadata(file='value_head.safetensors', name='v_head.summary.weight',
                                  shape=(2, 2), dtype='F32')
    return updated(descriptor, tensors=(*descriptor.tensors, tensor),
                   semantic_kind='reward_model', has_value_head=True)


def directory_link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        import subprocess
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        f"New-Item -ItemType Junction -Path '{link}' -Target '{target}' | Out-Null"],
                       check=True, capture_output=True)


def test_freeze_copies_parent_and_retries_do_not_change_frozen_bytes(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    frozen = store.path(ref)
    original = (frozen / 'model.safetensors').read_bytes()
    (directory / 'model.safetensors').write_bytes(b'resumed training output')
    assert (frozen / 'model.safetensors').read_bytes() == original
    assert store.verify(ref) == descriptor
    assert store.get(ref) == descriptor
    assert ref.model_dump() == {
        'artifact_id': sha(canonical(descriptor.model_dump(mode='json'))),
        'inventory_sha256': ref.inventory_sha256,
    }


def test_inventory_is_sorted_and_streamed_without_read_bytes(tmp_path, monkeypatch):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    (directory / 'large.bin').write_bytes(b'x' * 2_000_000)
    expected = [{'path': file.name, 'size': file.stat().st_size, 'sha256': sha(file.read_bytes())}
                for file in sorted(directory.iterdir())]
    monkeypatch.setattr(Path, 'read_bytes', lambda self: pytest.fail('unbounded file read'))
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    assert ref.inventory_sha256 == sha(canonical(expected))
    assert store.verify(ref) == descriptor
    assert store.freeze(directory, descriptor) == ref


def test_changed_weights_have_distinct_identity_even_with_same_descriptor(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    parent = store.freeze(directory, descriptor)
    with (directory / 'model.safetensors').open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'\x01')
    child = store.freeze(directory, descriptor)
    assert parent.artifact_id == child.artifact_id
    assert parent.inventory_sha256 != child.inventory_sha256
    assert store.path(parent) != store.path(child)
    assert store.verify(parent) == store.verify(child)


@pytest.mark.parametrize('field,value', [
    ('token', 'secret'), ('model_path', '/tmp/output'), ('artifact_id', 'a' * 64),
    ('credentials', {'api_key': 'secret'}),
])
def test_descriptor_rejects_credentials_paths_and_unknown_fields(tmp_path, field, value):
    _, descriptor = fixture_model(tmp_path)
    with pytest.raises(ValidationError):
        updated(descriptor, **{field: value})


@pytest.mark.parametrize('repo,revision', [('/tmp/model', 'a' * 40), ('test/tiny', 'main'),
                                          ('https://token@host/model', 'a' * 40)])
def test_hf_root_requires_pinned_metadata_without_authorizing_download(repo, revision):
    module = api()
    with pytest.raises(ValidationError):
        module.HFRootRef(repo_id=repo, revision=revision, config_sha256='b' * 64,
                         tokenizer_fingerprint='c' * 64)
    with pytest.raises(ValidationError):
        module.ModelArtifactRef(artifact_id='a' * 64, inventory_sha256='b' * 64, path='/tmp/a')


@pytest.mark.parametrize('filename', ['../escape.safetensors', '/absolute', 'C:/model',
                                     'nested/../../escape', 'nested\\escape', 'file:stream'])
def test_tensor_inventory_rejects_unsafe_relative_paths(filename):
    module = api()
    with pytest.raises(ValidationError):
        module.TensorMetadata(file=filename, name='weight', shape=(2, 2), dtype='F32')


def test_freeze_rejects_source_and_nested_symlinks(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    link = tmp_path / 'link'
    directory_link(link, directory)
    store = module.ModelArtifactStore(tmp_path / 'store')
    with pytest.raises(ValueError, match='link|reparse'):
        store.freeze(link, descriptor)
    directory_link(directory / 'escape', tmp_path)
    with pytest.raises(ValueError, match='link|reparse'):
        store.freeze(directory, descriptor)


@pytest.mark.parametrize('mutation', ['weights', 'extra', 'missing', 'descriptor', 'inventory'])
def test_every_read_rejects_tampering(tmp_path, mutation):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    payload = store.path(ref)
    if mutation == 'weights':
        (payload / 'model.safetensors').write_bytes(b'changed')
    elif mutation == 'extra':
        (payload / 'extra').write_text('changed')
    elif mutation == 'missing':
        (payload / 'model.safetensors').unlink()
    else:
        (payload.parent / f'{mutation}.json').write_text('{}')
    for read in (store.get, store.verify, store.path):
        with pytest.raises(ValueError):
            read(ref)


@pytest.mark.parametrize('shard', ['missing.safetensors', '../outside.safetensors'])
def test_shard_index_rejects_missing_or_escaping_shards(tmp_path, shard):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    (directory / 'model.safetensors.index.json').write_bytes(canonical({
        'metadata': {'total_size': 16}, 'weight_map': {'model.weight': shard},
    }))
    with pytest.raises(ValueError, match='shard|path'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


@pytest.mark.parametrize('shape,dtype,offsets', [((4, 4), 'F32', (0, 16)),
                                                ((2, 2), 'F16', (0, 16)),
                                                ((2, 2), 'F32', (8, 24))])
def test_safetensor_header_geometry_matches_descriptor_and_file(tmp_path, shape, dtype, offsets):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    write_tensor(directory / 'model.safetensors', shape=shape, dtype=dtype, offsets=offsets)
    with pytest.raises(ValueError, match='tensor|geometry|offset'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


def test_effective_parent_is_distinct_from_root_and_adapter_binds_parent(tmp_path):
    module = api()
    parent_dir, parent_desc = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    parent = store.freeze(parent_dir, parent_desc)
    adapter_dir, adapter_desc = fixture_model(tmp_path, adapter=True, marker='adapter')
    adapter_desc = updated(adapter_desc, effective_base=parent, parents=(parent,))
    adapter = store.freeze(adapter_dir, adapter_desc)
    correct = module.ModelInput(policy=module.ModelLoadRef(base=parent, adapter=adapter),
                                initialization='continue_adapter')
    assert store.validate_input(correct) == correct
    wrong = updated(correct, policy=module.ModelLoadRef(base=adapter_desc.root, adapter=adapter))
    with pytest.raises(ValueError, match='effective base'):
        store.validate_input(wrong)
    with pytest.raises(ValueError, match='full model'):
        store.validate_input(updated(correct, policy=module.ModelLoadRef(base=adapter, adapter=adapter)))
    assert store.get(adapter).effective_base == parent
    assert store.get(adapter).root == parent_desc.root


@pytest.mark.parametrize('storage,semantic,head', [('adapter', 'causal_lm', False),
                                                  ('adapter', 'reward_model', True),
                                                  ('full_model', 'reward_model', True)])
def test_storage_form_is_independent_of_semantic_kind(tmp_path, storage, semantic, head):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=storage == 'adapter')
    if head:
        descriptor = with_value_head(directory, descriptor)
    else:
        descriptor = updated(descriptor, semantic_kind=semantic, has_value_head=head)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    assert store.get(ref).storage_kind == storage
    assert store.get(ref).semantic_kind == semantic
    if semantic == 'reward_model':
        with pytest.raises(ValueError, match='causal_lm'):
            store.validate_input(module.ModelInput(policy=module.ModelLoadRef(
                base=descriptor.root if storage == 'adapter' else ref,
                adapter=ref if storage == 'adapter' else None), initialization=(
                    'continue_adapter' if storage == 'adapter' else 'full_weights')))


@pytest.mark.parametrize('initialization,adapter', [('continue_adapter', False), ('new_adapter', True),
                                                   ('full_weights', True)])
def test_initialization_requires_explicit_compatible_loader(initialization, adapter):
    module = api()
    ref = module.ModelArtifactRef(artifact_id='a' * 64, inventory_sha256='b' * 64)
    with pytest.raises(ValidationError):
        module.ModelInput(policy=module.ModelLoadRef(base=ref, adapter=ref if adapter else None),
                          initialization=initialization)


@pytest.mark.parametrize('flag', ['synthetic', 'simulation'])
def test_synthetic_or_simulated_outputs_cannot_be_real_candidates(tmp_path, flag):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, updated(descriptor, **{flag: True}))
    assert not store.get(ref).eligible_for_real_use
    with pytest.raises(ValueError, match='synthetic|simulation'):
        store.validate_input(module.ModelInput(policy=module.ModelLoadRef(base=ref),
                                               initialization='full_weights'), require_real=True)


def test_frozen_reference_and_reward_bindings_are_verified(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    policy = store.freeze(directory, descriptor)
    load = module.ModelLoadRef(base=policy)
    model_input = module.ModelInput(policy=load, initialization='full_weights',
                                    frozen_reference=load, reward_model=load)
    with pytest.raises(ValueError, match='reward_model'):
        store.validate_input(model_input)
    reward = store.freeze(directory, with_value_head(directory, descriptor))
    assert store.validate_input(updated(model_input, reward_model=module.ModelLoadRef(base=reward)))


def test_snapshot_requires_matching_config_tokenizer_and_adapter_metadata(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    (directory / 'adapter_config.json').write_text('{"peft_type":"LORA","r":99}')
    with pytest.raises(ValueError, match='adapter'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)
    full, full_desc = fixture_model(tmp_path, marker='full')
    (full / 'tokenizer.json').unlink()
    with pytest.raises(ValueError, match='tokenizer'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(full, full_desc)
    (full / 'tokenizer.json').write_text('{"version":"1.0"}')
    (full / 'config.json').write_text('{"model_type":"other"}')
    with pytest.raises(ValueError, match='config'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(full, full_desc)


def test_sharded_weights_require_complete_index(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    old = directory / 'model.safetensors'
    filename = 'model-00001-of-00002.safetensors'
    old.rename(directory / filename)
    tensor = updated(descriptor.tensors[0], file=filename)
    with pytest.raises(ValueError, match='shard|weight'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, updated(descriptor, tensors=(tensor,)))


def test_adapter_weight_file_cannot_masquerade_as_full_model(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    (directory / 'model.safetensors').rename(directory / 'adapter_model.safetensors')
    tensor = updated(descriptor.tensors[0], file='adapter_model.safetensors')
    with pytest.raises(ValueError, match='weight|storage'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, updated(descriptor, tensors=(tensor,)))


def test_frozen_reference_cannot_silently_revert_to_root(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    parent = store.freeze(directory, descriptor)
    model_input = module.ModelInput(policy=module.ModelLoadRef(base=parent), initialization='new_adapter',
                                    frozen_reference=module.ModelLoadRef(base=descriptor.root))
    with pytest.raises(ValueError, match='reference.*policy|exact.*parent'):
        store.validate_input(model_input)


def test_actual_value_head_must_match_descriptor(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    with pytest.raises(ValueError, match='value head'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(
            directory, updated(descriptor, semantic_kind='reward_model', has_value_head=True))


@pytest.mark.parametrize('metadata', [
    {'target_modules': [None]}, {'peft_type': 'LORA', 'r': 2, 'lora_alpha': 4, 'lora_dropout': False,
                               'target_modules': ['q_proj']},
])
def test_malformed_adapter_settings_fail_closed(tmp_path, metadata):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    (directory / 'adapter_config.json').write_bytes(canonical(metadata))
    with pytest.raises(ValueError, match='adapter'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


@pytest.mark.parametrize('filename', ['COM2.bin', 'LPT9.weights', 'nested/COM3'])
def test_windows_device_names_cannot_be_model_paths(filename):
    module = api()
    with pytest.raises(ValueError):
        module.TensorMetadata(file=filename, name='weight', shape=(1,), dtype='F32')


def test_effective_adapter_settings_cannot_omit_pissa_variant(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    config = json.loads((directory / 'adapter_config.json').read_text())
    config['init_lora_weights'] = 'pissa_niter_16'
    (directory / 'adapter_config.json').write_bytes(canonical(config))
    with pytest.raises(ValueError, match='adapter'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


def test_public_reads_reject_store_reparse_redirection(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    payload = store.path(ref)
    held = tmp_path / 'held'
    payload.rename(held)
    directory_link(payload, held)
    with pytest.raises(ValueError, match='link|reparse'):
        store.verify(ref)


def test_duplicate_header_keys_and_unlisted_tensors_are_rejected(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    header = (b'{"model.weight":{"dtype":"F32","shape":[2,2],"data_offsets":[0,16]},'
              b'"model.weight":{"dtype":"F32","shape":[2,2],"data_offsets":[0,16]}}')
    (directory / 'model.safetensors').write_bytes(struct.pack('<Q', len(header)) + header + bytes(16))
    with pytest.raises(ValueError, match='duplicate'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


def test_synthetic_parent_provenance_cannot_be_removed(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    parent = store.freeze(directory, updated(descriptor, synthetic=True))
    child_directory, child_descriptor = fixture_model(tmp_path, adapter=True, marker='child')
    with pytest.raises(ValueError, match='synthetic'):
        store.freeze(child_directory, updated(child_descriptor, effective_base=parent, parents=(parent,)))
