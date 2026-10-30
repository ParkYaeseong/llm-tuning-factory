import json

import pytest

from tuning_factory import gpu_compat

SMI = "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n"
SASS = ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"]


def test_parse_gpu_facts_reads_the_fixed_query():
    assert gpu_compat.parse_gpu_facts(SMI) == [{
        "uuid": "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21", "name": "NVIDIA RTX PRO 4000 Blackwell",
        "capability": "12.0", "memory_mib": 24467, "driver": "595.91.07"}]


@pytest.mark.parametrize("text", ["", "GPU-1, NVIDIA RTX A4000, 16376, Disabled\n",
                                  "GPU-1, NVIDIA RTX A4000, x.y, 16376, 570.86.15\n",
                                  ", NVIDIA RTX A4000, 8.6, 16376, 570.86.15\n",
                                  "GPU-1, , 8.6, 16376, 570.86.15\n",
                                  "GPU-1, NVIDIA RTX A4000, [N/A], 16376, 570.86.15\n",
                                  "GPU-1, NVIDIA RTX A4000, 8.6, [N/A], 570.86.15\n"])
def test_parse_gpu_facts_refuses_unexpected_output(text):
    with pytest.raises(ValueError):
        gpu_compat.parse_gpu_facts(text)


def test_parse_gpu_facts_reads_several_gpus_and_skips_blank_lines():
    gpus = gpu_compat.parse_gpu_facts(SMI + "\n" + SMI.replace("GPU-8d31", "GPU-9e42"))
    assert len(gpus) == 2
    assert gpus[0]["uuid"] != gpus[1]["uuid"]


@pytest.mark.parametrize("capability,supported", [
    ("8.0", True), ("8.6", True), ("8.9", True), ("9.0", True), ("10.0", True), ("12.0", True), ("12.1", True),
    ("7.0", False), ("6.1", False), ("11.0", False)])
def test_same_major_machine_code_rule(capability, supported):
    assert gpu_compat.capability_supported(capability, SASS, []) is supported


def test_ptx_covers_newer_devices_only():
    assert gpu_compat.capability_supported("13.0", ["9.0"], ["12.0"])
    assert not gpu_compat.capability_supported("11.0", ["9.0"], ["12.0"])


def test_machine_code_does_not_run_on_a_lower_minor_of_the_same_major():
    assert not gpu_compat.capability_supported("8.0", ["8.6"], [])


def test_driver_versions_compare_numerically():
    assert gpu_compat.driver_at_least("595.91.07", "570.26")
    assert not gpu_compat.driver_at_least("525.60.12", "525.60.13")
    assert gpu_compat.driver_at_least("1000.1", "999.99")
    assert gpu_compat.driver_at_least("570.26", "570.26.0")
    assert not gpu_compat.driver_at_least("570.25.9", "570.26")


QWEN05 = {"id": "Qwen/Qwen2.5-0.5B-Instruct", "parameters_b": 0.5, "hidden_size": 896, "num_layers": 24,
          "gpu_verified": True}
LLAMA70 = {"id": "meta-llama/Llama-3.1-70B-Instruct", "parameters_b": 70, "hidden_size": 8192, "num_layers": 80,
           "gpu_verified": False}


def training(method="lora", **hyper):
    return {"training_method": method, "quantization_bit": 4,
            "hyperparameters": {"precision": "bf16", "per_device_train_batch_size": 1, "cutoff_len": 1024, **hyper},
            "finetuning_options": {"freeze_trainable_layers": 2}}


def test_memory_estimates_cover_lora_qlora_full_and_freeze():
    small = gpu_compat.estimate_memory(QWEN05, training())
    assert 0.9 < small["weights_gib"] < 1.0 and small["total_gib"] < 3
    assert 8.5 < gpu_compat.estimate_memory(QWEN05, training("full"))["total_gib"] < 9.5
    assert 2.9 < gpu_compat.estimate_memory(QWEN05, training("freeze"))["total_gib"] < 3.2
    assert gpu_compat.estimate_memory(LLAMA70, training())["weights_gib"] > 120
    qlora = gpu_compat.estimate_memory(LLAMA70, training("qlora"))
    assert 34 < qlora["weights_gib"] < 37 and 45 < qlora["total_gib"] < 55


def test_memory_estimate_needs_catalog_sizes():
    assert gpu_compat.estimate_memory(None, training()) is None
    assert gpu_compat.estimate_memory({"parameters_b": 7}, training()) is None


CAPS = {"torch_cuda": "12.8", "sass": SASS, "ptx": [], "bf16_min_capability": "8.0",
        "min_driver": "525.60.13", "recommended_driver": "570.26"}
BLACKWELL = gpu_compat.parse_gpu_facts(SMI)[0]
POD = {"gpu_id": "NVIDIA RTX PRO 4000 Blackwell", "gpu_display_name": None, "gpu_count": 1, "hourly_cost_usd": 0.57}


def job(method="lora", **resource):
    config = training(method)
    config["resource_profile"] = {"gpu_type": "NVIDIA RTX PRO 4000 Blackwell", "gpu_count": 1,
                                  "hourly_cost_usd": 0.6, **resource}
    return config


def observed(gpus=(BLACKWELL,), pod=POD, at=1000.0):
    return {"key": "runpod-borrowed", "observed_at": at, "gpus": list(gpus), "pod": pod}


def codes(findings):
    return {(item["level"], item["code"]) for item in findings}


def test_a_matching_job_on_a_supported_gpu_has_no_findings():
    assert gpu_compat.evaluate(job(), observed(), CAPS, QWEN05, stage="execute") == []


def test_the_queued_a4000_job_is_refused_on_the_blackwell_pod():
    findings = gpu_compat.evaluate(job(gpu_type="NVIDIA RTX A4000", hourly_cost_usd=0.5), observed(), CAPS, QWEN05,
                                   stage="execute")
    assert codes(findings) == {("block", "GPU_TYPE_MISMATCH"), ("block", "PRICE_OVER_BUDGET")}
    mismatch = next(item for item in findings if item["code"] == "GPU_TYPE_MISMATCH")
    assert "NVIDIA RTX A4000" in mismatch["message"] and "NVIDIA RTX PRO 4000 Blackwell" in mismatch["message"]
    assert gpu_compat.refusal(findings).startswith("실행할 수 없습니다.")
    assert gpu_compat.refusal([]) is None


def test_old_architecture_driver_and_bf16_block():
    old = {**BLACKWELL, "name": "Tesla V100-SXM2-32GB", "capability": "7.0", "memory_mib": 32768, "driver": "470.82"}
    findings = gpu_compat.evaluate(job(gpu_type="Tesla V100-SXM2-32GB"), observed([old], pod=None), CAPS, QWEN05,
                                   stage="execute")
    assert codes(findings) == {("block", "GPU_ARCH_UNSUPPORTED"), ("block", "DRIVER_TOO_OLD"),
                               ("block", "BF16_UNSUPPORTED")}


def test_a_driver_below_the_cuda_12_8_release_warns():
    findings = gpu_compat.evaluate(job(), observed([{**BLACKWELL, "driver": "535.104.05"}]), CAPS, QWEN05,
                                   stage="execute")
    assert codes(findings) == {("warn", "DRIVER_OLD")}


def test_memory_blocks_on_weights_and_warns_when_tight():
    h100 = {**BLACKWELL, "name": "NVIDIA H100 80GB HBM3", "capability": "9.0", "memory_mib": 81559}
    pod = {**POD, "gpu_id": "NVIDIA H100 80GB HBM3", "hourly_cost_usd": 2.0}
    lora = gpu_compat.evaluate(job(gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0), observed([h100], pod),
                               CAPS, LLAMA70, stage="execute")
    assert ("block", "MEMORY_WEIGHTS_EXCEED") in codes(lora)
    qlora = gpu_compat.evaluate(job("qlora", gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0),
                                observed([h100], pod), CAPS, LLAMA70, stage="execute")
    assert codes(qlora) == {("warn", "MODEL_UNVERIFIED")}
    qwen7 = {"id": "Qwen/Qwen2.5-7B-Instruct", "parameters_b": 7, "hidden_size": 3584, "num_layers": 28,
             "gpu_verified": False}
    tight = job()
    tight["hyperparameters"].update(cutoff_len=8192, per_device_train_batch_size=4)
    assert codes(gpu_compat.evaluate(tight, observed(), CAPS, qwen7, stage="execute")) == {
        ("warn", "MEMORY_TIGHT"), ("warn", "MODEL_UNVERIFIED")}


def test_unknown_models_and_unreadable_gpu_facts_warn():
    findings = gpu_compat.evaluate(job(), observed(gpus=[]), CAPS, None, stage="execute")
    assert codes(findings) == {("warn", "MEMORY_UNKNOWN"), ("warn", "MODEL_UNVERIFIED"),
                               ("warn", "GPU_FACTS_UNKNOWN")}


def test_plan_reports_missing_and_stale_observations_only():
    assert codes(gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="plan")) == {("warn", "OBSERVATION_MISSING")}
    stale = gpu_compat.evaluate(job(), observed(at=0.0), CAPS, QWEN05, stage="plan",
                                now=gpu_compat.STALE_SECONDS + 1)
    assert codes(stale) == {("warn", "OBSERVATION_STALE")}
    assert gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="execute") == []
    with pytest.raises(ValueError, match="stage"):
        gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="later")


def test_observation_store_round_trips_and_refuses_unsafe_keys(tmp_path):
    pod = {"gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1}, "costPerHr": "0.57"}
    observation = gpu_compat.make_observation("runpod-borrowed", [BLACKWELL], pod, now=5.0)
    assert observation == {"key": "runpod-borrowed", "observed_at": 5.0, "gpus": [BLACKWELL], "pod": POD}
    gpu_compat.save_observation(tmp_path, observation)
    assert gpu_compat.load_observation(tmp_path, "runpod-borrowed") == observation
    assert gpu_compat.load_observation(tmp_path, "runpod-other") is None
    with pytest.raises(ValueError, match="key"):
        gpu_compat.load_observation(tmp_path, "../escape")
    assert gpu_compat.make_observation("server-0123456789abcdef", [BLACKWELL], None, now=1.0)["pod"] is None


def test_a_gpu_count_mismatch_blocks_without_a_price_finding():
    findings = gpu_compat.evaluate(job(), observed(pod={**POD, "gpu_count": 2}), CAPS, QWEN05, stage="execute")
    assert ("block", "GPU_COUNT_MISMATCH") in codes(findings)
    assert "PRICE_UNKNOWN" not in {item["code"] for item in findings}


def test_a_price_equal_to_the_limit_passes():
    findings = gpu_compat.evaluate(job(), observed(pod={**POD, "hourly_cost_usd": 0.6}), CAPS, QWEN05,
                                   stage="execute")
    assert "PRICE_OVER_BUDGET" not in {item["code"] for item in findings}


def test_incomplete_pod_facts_block():
    for pod in ({**POD, "gpu_id": None, "gpu_display_name": None}, {**POD, "gpu_count": None}):
        findings = gpu_compat.evaluate(job(), observed(pod=pod), CAPS, QWEN05, stage="execute")
        assert codes(findings) == {("block", "POD_FACTS_UNKNOWN")}
    for price in (None, 0, float("nan")):
        findings = gpu_compat.evaluate(job(), observed(pod={**POD, "hourly_cost_usd": price}), CAPS, QWEN05,
                                       stage="execute")
        assert codes(findings) == {("block", "PRICE_UNKNOWN")}


def test_unreadable_or_foreign_observation_files_load_as_none(tmp_path):
    observation = gpu_compat.make_observation("runpod-borrowed", [BLACKWELL], None, now=5.0)
    path = tmp_path / "gpu-observations" / "runpod-borrowed.json"
    gpu_compat.save_observation(tmp_path, observation)
    broken = {k: v for k, v in BLACKWELL.items() if k != "memory_mib"}
    for text in ("{not json", "[]", json.dumps({**observation, "key": "runpod-other"}),
                 json.dumps({**observation, "gpus": [broken]})):
        path.write_text(text, encoding="utf-8")
        assert gpu_compat.load_observation(tmp_path, "runpod-borrowed") is None


def test_saving_twice_overwrites_and_leaves_no_temp_files(tmp_path):
    first = gpu_compat.make_observation("runpod-borrowed", [BLACKWELL], None, now=1.0)
    second = gpu_compat.make_observation("runpod-borrowed", [BLACKWELL], None, now=2.0)
    gpu_compat.save_observation(tmp_path, first)
    gpu_compat.save_observation(tmp_path, second)
    assert gpu_compat.load_observation(tmp_path, "runpod-borrowed") == second
    assert list((tmp_path / "gpu-observations").glob("*.tmp")) == []


def test_a_verified_model_warns_on_a_generation_it_was_not_verified_on():
    h100 = {**BLACKWELL, "name": "NVIDIA H100 80GB HBM3", "capability": "9.0", "memory_mib": 81559}
    pod = {**POD, "gpu_id": "NVIDIA H100 80GB HBM3", "hourly_cost_usd": 2.0}
    verified = {**QWEN05, "verified_capabilities": ["9.0"]}
    on_blackwell = gpu_compat.evaluate(job(), observed(), CAPS, verified, stage="execute")
    assert codes(on_blackwell) == {("warn", "MODEL_UNVERIFIED")}
    message = next(item for item in on_blackwell if item["code"] == "MODEL_UNVERIFIED")["message"]
    assert message == "이 모델은 이 GPU 세대(compute capability 12.0)에서 아직 검증되지 않았습니다."
    on_h100 = gpu_compat.evaluate(job(gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0), observed([h100], pod),
                                  CAPS, verified, stage="execute")
    assert not any(code == "MODEL_UNVERIFIED" for _, code in codes(on_h100))
    assert gpu_compat.evaluate(job(), observed(), CAPS, QWEN05, stage="execute") == []


def test_memory_messages_name_their_thresholds():
    h100 = {**BLACKWELL, "name": "NVIDIA H100 80GB HBM3", "capability": "9.0", "memory_mib": 81559}
    pod = {**POD, "gpu_id": "NVIDIA H100 80GB HBM3", "hourly_cost_usd": 2.0}
    lora = gpu_compat.evaluate(job(gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0), observed([h100], pod),
                               CAPS, LLAMA70, stage="execute")
    assert "의 95%를 넘습니다" in next(i for i in lora if i["code"] == "MEMORY_WEIGHTS_EXCEED")["message"]
    qwen7 = {"id": "Qwen/Qwen2.5-7B-Instruct", "parameters_b": 7, "hidden_size": 3584, "num_layers": 28,
             "gpu_verified": False}
    tight = job()
    tight["hyperparameters"].update(cutoff_len=8192, per_device_train_batch_size=4)
    findings = gpu_compat.evaluate(tight, observed(), CAPS, qwen7, stage="execute")
    assert "의 90%를 넘어 부족할 수 있습니다" in next(i for i in findings if i["code"] == "MEMORY_TIGHT")["message"]


@pytest.mark.parametrize("price,code", [(0.6, None), (0, None), (0.61, "PRICE_OVER_BUDGET"), (None, "PRICE_UNKNOWN"),
                                        (float("nan"), "PRICE_UNKNOWN"), (-1, "PRICE_UNKNOWN"), (True, "PRICE_UNKNOWN")])
def test_a_server_price_is_judged_like_a_pod_price_but_zero_is_allowed(price, code):
    for observation in (observed(pod=None), None):   # judged before the missing-observation return
        findings = gpu_compat.evaluate(job(), observation, CAPS, QWEN05, stage="execute",
                                       server={"hourly_cost_usd": price})
        assert {item["code"] for item in findings if item["level"] == "block"} == ({code} if code else set())


def test_an_unknown_server_price_points_to_the_connection_settings():
    findings = gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="plan", server={"hourly_cost_usd": None})
    assert gpu_compat.SERVER_PRICE_UNKNOWN in gpu_compat.refusal(findings)
