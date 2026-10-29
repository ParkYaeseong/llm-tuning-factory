import pytest

from tuning_factory import gpu_compat

SMI = "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n"
SASS = ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"]


def test_parse_gpu_facts_reads_the_fixed_query():
    assert gpu_compat.parse_gpu_facts(SMI) == [{
        "uuid": "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21", "name": "NVIDIA RTX PRO 4000 Blackwell",
        "capability": "12.0", "memory_mib": 24467, "driver": "595.91.07"}]


@pytest.mark.parametrize("text", ["", "GPU-1, NVIDIA RTX A4000, 16376, Disabled\n",
                                  "GPU-1, NVIDIA RTX A4000, x.y, 16376, 570.86.15\n"])
def test_parse_gpu_facts_refuses_unexpected_output(text):
    with pytest.raises(ValueError):
        gpu_compat.parse_gpu_facts(text)


@pytest.mark.parametrize("capability,supported", [
    ("8.0", True), ("8.6", True), ("8.9", True), ("9.0", True), ("10.0", True), ("12.0", True), ("12.1", True),
    ("7.0", False), ("6.1", False), ("11.0", False)])
def test_same_major_machine_code_rule(capability, supported):
    assert gpu_compat.capability_supported(capability, SASS, []) is supported


def test_ptx_covers_newer_devices_only():
    assert gpu_compat.capability_supported("13.0", ["9.0"], ["12.0"])
    assert not gpu_compat.capability_supported("11.0", ["9.0"], ["12.0"])


def test_driver_versions_compare_numerically():
    assert gpu_compat.driver_at_least("595.91.07", "570.26")
    assert not gpu_compat.driver_at_least("525.60.12", "525.60.13")
    assert gpu_compat.driver_at_least("1000.1", "999.99")
