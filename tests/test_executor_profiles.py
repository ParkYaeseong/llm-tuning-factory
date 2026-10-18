import pytest

from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts


def test_aliases_lock_whole_pod_and_price_is_not_multiplied():
    a = ExecutorProfile(profile_id="a", revision="1", name="first", pod_id="pod1", credential_scope="local")
    b = a.model_copy(update={"profile_id": "b"})
    assert a.exclusivity_key == b.exclusivity_key == "existing_runpod:pod1"
    facts = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=4,
                          total_hourly_cost_usd=8, observed_at="2026-10-18T00:00:00Z")
    assert facts.cost(1800) == 4
    assert facts.legacy_resources(1800).hourly_cost_usd == 2


@pytest.mark.parametrize("extra", [{"api_key": "secret"}, {"ssh_path": "/key"}])
def test_profiles_cannot_embed_credentials(extra):
    with pytest.raises(ValueError):
        ExecutorProfile(profile_id="a", revision="1", name="first", pod_id="pod1",
                        credential_scope="local", **extra)
