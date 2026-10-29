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
