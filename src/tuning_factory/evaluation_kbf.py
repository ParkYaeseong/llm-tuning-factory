"""Pure extraction helpers reused from the existing canonical tuning repository.

Source: D:/workflow/kbl-llm-repo/scripts/eval_extraction_pc.py (names_of, ids_of, f1).
Preserves its partial-credit regex semantics; does not import its GPU/model-generation code.
These heuristic metrics intentionally do not assert full JSON validity or semantic correctness.
"""
import re


def names_of(text):
    return {m.strip().lower() for m in re.findall(r'"name"\s*:\s*"([^"]{1,120})"', text)}


def ids_of(text):
    return set(re.findall(r'"(?:mapped_uo_id|catalog_id)"\s*:\s*"([A-Za-z]+\d+)"', text))


def f1(predicted, gold):
    if not predicted and not gold:
        return 1.0
    if not predicted or not gold:
        return 0.0
    true_positive = len(predicted & gold)
    precision, recall = true_positive / len(predicted), true_positive / len(gold)
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
