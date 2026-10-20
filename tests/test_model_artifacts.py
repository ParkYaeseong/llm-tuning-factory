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


@pytest.mark.parametrize('field,value', [('fan_in_fan_out', True), ('inference_mode', True),
                                        ('task_type', 'CAUSAL_LM')])
def test_loader_option_requires_matching_effective_metadata(tmp_path, field, value):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    store = module.ModelArtifactStore(tmp_path / 'store')
    before = store.freeze(directory, descriptor)
    config = json.loads((directory / 'adapter_config.json').read_text())
    config[field] = value
    (directory / 'adapter_config.json').write_bytes(canonical(config))
    with pytest.raises(ValueError, match='adapter'):
        store.freeze(directory, descriptor)
    changed = updated(descriptor, adapter=updated(descriptor.adapter, **{field: value}))
    after = store.freeze(directory, changed)
    assert before.artifact_id != after.artifact_id
    assert store.get(after).adapter.model_dump()[field] == value
    assert store.verify(before) == descriptor


@pytest.mark.parametrize('field,value', [
    ('unknown_loader_option', True), ('unknown_loader_option', None), ('lora_bias', True),
    ('exclude_modules', ['v_proj']), ('layers_to_transform', [1]), ('layers_pattern', 'layers'),
    ('megatron_config', {'tensor_model_parallel_size': 2}), ('megatron_core', 'custom.core'),
    ('trainable_token_indices', [1]), ('loftq_config', {'loftq_bits': 4}),
    ('eva_config', {'rho': 2}), ('corda_config', {'corda_method': 'ipm'}),
    ('alora_invocation_tokens', [1]), ('use_qalora', True), ('qalora_group_size', 32),
    ('arrow_config', {'top_k': 1}), ('ensure_weight_tying', True),
    ('auto_mapping', {'base_model_class': 'UnreviewedModel'}),
    ('runtime_config', {'ephemeral_gpu_offload': True}), ('fan_in_fan_out', 0),
])
def test_unrepresented_loader_variants_are_rejected(tmp_path, field, value):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    config = json.loads((directory / 'adapter_config.json').read_text())
    config[field] = value
    (directory / 'adapter_config.json').write_bytes(canonical(config))
    with pytest.raises(ValueError, match='adapter'):
        module.ModelArtifactStore(tmp_path / 'store').freeze(directory, descriptor)


def test_default_export_fields_and_provenance_do_not_change_effective_metadata(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    config = json.loads((directory / 'adapter_config.json').read_text())
    config.update(fan_in_fan_out=False, task_type=None, inference_mode=False,
                  peft_version='0.18.1', revision='a' * 40, base_model_name_or_path='/old/output',
                  auto_mapping=None, exclude_modules=None, layers_to_transform=None, layers_pattern=None,
                  megatron_config=None, megatron_core='megatron.core', trainable_token_indices=None,
                  loftq_config={}, eva_config=None, corda_config=None, alora_invocation_tokens=None,
                  use_qalora=False, qalora_group_size=16, lora_bias=False, arrow_config=None,
                  ensure_weight_tying=False)
    (directory / 'adapter_config.json').write_bytes(canonical(config))
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    assert store.get(ref).adapter == descriptor.adapter
    assert store.get(ref).effective_base == descriptor.root


def make_shared_lineage(tmp_path, count=6):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    refs = []
    for index in range(count):
        parents = tuple(refs[-2:])
        ref = store.freeze(directory, updated(descriptor, producer_job_id=f'job-{index}',
                                              parents=parents, effective_base=parents[-1] if parents
                                              else descriptor.root))
        refs.append(ref)
    return store, directory, descriptor, refs


@pytest.mark.parametrize('operation', ['verify', 'get', 'path', 'freeze', 'validate_input'])
def test_shared_ancestors_are_hashed_once_per_public_operation(tmp_path, monkeypatch, operation):
    module = api()
    store, directory, descriptor, refs = make_shared_lineage(tmp_path)
    original_inventory = module._inventory
    scans = []

    def inventory_spy(path):
        scans.append(path)
        return original_inventory(path)

    monkeypatch.setattr(module, '_inventory', inventory_spy)
    if operation == 'freeze':
        store.freeze(directory, updated(descriptor, producer_job_id='child',
                                         parents=tuple(refs[-2:]), effective_base=refs[-1]))
        # One new snapshot plus each distinct parent: no path is hashed twice.
        assert len(scans) == len(refs) + 1
    elif operation == 'validate_input':
        load = module.ModelLoadRef(base=refs[-1])
        store.validate_input(module.ModelInput(policy=load, initialization='full_weights', frozen_reference=load))
        assert len(scans) == len(refs)
    else:
        getattr(store, operation)(refs[-1])
        assert len(scans) == len(refs)
    assert len(set(scans)) == len(scans)


def test_completed_verification_cache_does_not_survive_public_calls(tmp_path):
    store, _, _, refs = make_shared_lineage(tmp_path)
    root_payload = store.path(refs[0])
    store.verify(refs[-1])
    (root_payload / 'model.safetensors').write_bytes(b'tampered after earlier successful verification')
    for read in (store.verify, store.get, store.path):
        with pytest.raises(ValueError, match='hash|inventory'):
            read(refs[-1])


def test_shared_verification_cache_never_skips_active_cycle_check(tmp_path):
    store, _, _, refs = make_shared_lineage(tmp_path, count=2)
    identity = (refs[0].artifact_id, refs[0].inventory_sha256)
    verifier = store.verifier()
    verifier.verify(refs[0])
    assert identity in verifier._completed
    # A completed node cannot legitimize a reference back into the active path.
    with pytest.raises(ValueError, match='cyclic'):
        verifier._verify(refs[0], frozenset({identity}))


def test_entry_only_verification_cache_cannot_reach_a_lineage_verifier(tmp_path):
    module = api()
    store, _, _, _ = make_shared_lineage(tmp_path, count=2)
    # Each verifier owns its completed-cache; nobody can hand an entry-only cache to a lineage verifier.
    with pytest.raises(TypeError):
        module.ArtifactVerifier(store.root, completed={})
    # lineage=False is the worker's entry-only mode; the control-plane store never offers it.
    with pytest.raises(TypeError):
        store.verifier(lineage=False)
    assert store.verifier().lineage is True
    assert not hasattr(store, '_verify')
    worker, control = module.ArtifactVerifier(store.root, lineage=False), store.verifier()
    assert worker._completed is not control._completed


def test_directory_inventory_sha256_is_the_frozen_inventory_identity(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    ref = store.freeze(directory, descriptor)
    assert module.directory_inventory_sha256(directory) == ref.inventory_sha256
    assert module.directory_inventory_sha256(store.path(ref)) == ref.inventory_sha256


def test_historical_descriptor_identity_survives_added_effective_option_defaults(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    store = module.ModelArtifactStore(tmp_path / 'store')
    current = store.freeze(directory, descriptor)
    location = store.path(current).parent
    historical = descriptor.model_dump(mode='json')
    for field in ('fan_in_fan_out', 'task_type', 'inference_mode'):
        historical['adapter'].pop(field)
    body = canonical(historical)
    prior = module.ModelArtifactRef(artifact_id=sha(body), inventory_sha256=current.inventory_sha256)
    (location / 'descriptor.json').write_bytes(body)
    historical_location = store.root / prior.artifact_id / prior.inventory_sha256
    historical_location.parent.mkdir()
    location.rename(historical_location)
    assert store.get(prior) == descriptor
    assert store.path(prior).parent == historical_location
    assert store.get(prior).adapter.fan_in_fan_out is False


def test_historical_descriptor_defaults_do_not_accept_unrecorded_loader_changes(tmp_path):
    module = api()
    directory, descriptor = fixture_model(tmp_path, adapter=True)
    config = json.loads((directory / 'adapter_config.json').read_text())
    config['fan_in_fan_out'] = True
    (directory / 'adapter_config.json').write_bytes(canonical(config))
    # Construct the exact legacy serialized artifact directly: the prior release
    # accepted this unrepresented option. A valid historical hash is insufficient.
    historical = descriptor.model_dump(mode='json')
    for field in ('fan_in_fan_out', 'task_type', 'inference_mode'):
        historical['adapter'].pop(field)
    inventory = [{'path': file.name, 'size': file.stat().st_size, 'sha256': sha(file.read_bytes())}
                 for file in sorted(directory.iterdir())]
    ref = module.ModelArtifactRef(artifact_id=sha(canonical(historical)),
                                   inventory_sha256=sha(canonical(inventory)))
    store = module.ModelArtifactStore(tmp_path / 'store')
    location = store.root / ref.artifact_id / ref.inventory_sha256
    location.mkdir(parents=True)
    directory.rename(location / 'files')
    (location / 'descriptor.json').write_bytes(canonical(historical))
    (location / 'inventory.json').write_bytes(canonical(inventory))
    with pytest.raises(ValueError, match='adapter.*fan_in_fan_out'):
        store.verify(ref)


def test_verification_cache_distinguishes_both_parts_of_artifact_identity(tmp_path, monkeypatch):
    module = api()
    directory, descriptor = fixture_model(tmp_path)
    store = module.ModelArtifactStore(tmp_path / 'store')
    first = store.freeze(directory, descriptor)
    with (directory / 'model.safetensors').open('r+b') as stream:
        stream.seek(-1, 2)
        stream.write(b'\x01')
    second = store.freeze(directory, descriptor)
    assert first.artifact_id == second.artifact_id
    assert first.inventory_sha256 != second.inventory_sha256
    child = store.freeze(directory, updated(descriptor, producer_job_id='child',
                                             parents=(first, second), effective_base=second))
    scans = []
    original_inventory = module._inventory

    def inventory_spy(path):
        scans.append(path)
        return original_inventory(path)

    monkeypatch.setattr(module, '_inventory', inventory_spy)
    store.verify(child)
    assert len(scans) == 3
    assert len(set(scans)) == 3


def test_operation_outputs_uses_only_public_helpers():
    import ast

    from tuning_factory import dataset_splits, operation_outputs
    module = api()
    tree = ast.parse(Path(operation_outputs.__file__).read_text(encoding='utf-8'))
    private = sorted(f'{node.module}.{alias.name}' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                     and (node.module or '').rsplit('.', 1)[-1] in {'model_artifacts', 'dataset_splits'}
                     for alias in node.names if alias.name.startswith('_'))
    assert private == []
    for public, alias in (('list_files', '_files'), ('read_json', '_read_json'), ('tensor_header', '_tensor_header')):
        assert getattr(module, alias) is getattr(module, public)   # the private names are aliases, never copies
    assert dataset_splits._dataset is dataset_splits.load_dataset
