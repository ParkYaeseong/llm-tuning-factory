"""Metrics require caller-supplied predictions and ground truth; no synthetic quality scores."""
import json
import math
import unicodedata
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .registry import canonical, digest


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    direction: str
    category: str
    description: str


def metric_schema(kind):
    """Describe metrics without picking a default selection objective."""
    definitions = {
        "text": (("exact_match", "text_answer_agreement", "NFC and whitespace-normalized text answer match"),
                 ("character_f1", "text_answer_agreement", "Unicode character multiset F1 excluding whitespace")),
        "classification": (("accuracy", "label_agreement", "Exact label accuracy"),
                           ("macro_f1", "label_agreement", "Unweighted label F1")),
        "extraction": (("accuracy", "field_agreement", "Exact field agreement"),
                       ("f1", "field_agreement", "Micro field F1")),
        "json": (("json_validity", "format_validity", "JSON syntax validity"),),
        "kbf_extraction": (("mean_name_f1", "field_agreement", "Mean extracted-name F1"),
                           ("mean_id_f1", "field_agreement", "Mean extracted-ID F1"),
                           ("json_validity", "format_validity", "JSON syntax validity")),
        "pipeline": (),
    }
    if kind not in definitions:
        raise ValueError("unsupported evaluation kind")
    return tuple(MetricDefinition(name, "max", category, description)
                 for name, category, description in definitions[kind])


def evaluator_source_sha256(kind="text"):
    """Hash scoring source, including delegated helpers, without importing model runtimes."""
    metric_schema(kind)
    paths = [Path(__file__)]
    if kind == "kbf_extraction":
        paths.append(Path(__file__).with_name("evaluation_kbf.py"))
    return digest(canonical({path.name: digest(path.read_bytes()) for path in paths}))


def _text_metrics(predictions, references):
    if not all(isinstance(value, str) for value in [*predictions, *references]):
        raise ValueError("text inputs must be strings")
    exact = total_f1 = 0
    for prediction, reference in zip(predictions, references):
        prediction = " ".join(unicodedata.normalize("NFC", prediction).split())
        reference = " ".join(unicodedata.normalize("NFC", reference).split())
        exact += prediction == reference
        predicted = Counter(char for char in prediction if not char.isspace())
        expected = Counter(char for char in reference if not char.isspace())
        size = sum(predicted.values()) + sum(expected.values())
        total_f1 += 2 * sum((predicted & expected).values()) / size if size else 1.0
    return {"exact_match": exact / len(predictions), "character_f1": total_f1 / len(predictions)}


class Evaluator(ABC):
    @abstractmethod
    def validate_output(self, output): ...

    @abstractmethod
    def evaluate(self, predictions, references, kind): ...

    @abstractmethod
    def summarize_metrics(self, metrics): ...

    @abstractmethod
    def compare_with_baseline(self, metrics, baseline): ...


class CommonEvaluator(Evaluator):
    def validate_output(self, output):
        if isinstance(output, (dict, list)):
            return True
        try:
            json.loads(output, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            return True
        except (ValueError, TypeError):
            return False

    def evaluate(self, predictions, references, kind):
        if kind == "pipeline":
            return {}
        if not predictions:
            raise ValueError("nonempty predictions required")
        if kind == "json":
            return {"json_validity": sum(map(self.validate_output, predictions)) / len(predictions)}
        if references is None:
            raise ValueError("ground truth required for this evaluator")
        if len(predictions) != len(references):
            raise ValueError("prediction/reference counts differ")
        if kind == "text":
            return _text_metrics(predictions, references)
        if kind == "kbf_extraction":
            from .evaluation_kbf import f1, ids_of, names_of
            if not all(isinstance(x, str) for x in [*predictions, *references]):
                raise ValueError("KBF extraction inputs must be generation/reference text")
            return {"mean_name_f1": sum(f1(names_of(p), names_of(r)) for p, r in zip(predictions, references)) / len(predictions),
                    "mean_id_f1": sum(f1(ids_of(p), ids_of(r)) for p, r in zip(predictions, references)) / len(predictions),
                    "json_validity": sum(map(self.validate_output, predictions)) / len(predictions)}
        if kind == "classification":
            if not all(isinstance(x, str) for x in [*predictions, *references]):
                raise ValueError("classification labels must be strings")
            scores = []
            for label in set(predictions) | set(references):
                tp = sum(p == r == label for p, r in zip(predictions, references))
                fp = sum(p == label and r != label for p, r in zip(predictions, references))
                fn = sum(p != label and r == label for p, r in zip(predictions, references))
                scores.append(2 * tp / (2 * tp + fp + fn))
            return {"accuracy": sum(p == r for p, r in zip(predictions, references)) / len(predictions),
                    "macro_f1": sum(scores) / len(scores)}
        if kind == "extraction":
            tp = fp = fn = exact = 0
            for prediction, reference in zip(predictions, references):
                if isinstance(reference, str):
                    reference = json.loads(reference)
                invalid = False
                if isinstance(prediction, str):
                    try:
                        prediction = json.loads(prediction)
                    except ValueError:
                        prediction, invalid = {}, True
                if not isinstance(prediction, dict) or not isinstance(reference, dict):
                    raise ValueError("extraction inputs must be JSON objects")
                p = {(k, json.dumps(v, sort_keys=True)) for k, v in prediction.items()}
                r = {(k, json.dumps(v, sort_keys=True)) for k, v in reference.items()}
                tp += 0 if invalid else len(p & r)
                fp += 1 if invalid else len(p - r)
                fn += len(r - p)
                exact += p == r and not invalid
            return {"accuracy": exact / len(predictions), "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 1.0}
        raise ValueError("unsupported evaluation kind")

    def summarize_metrics(self, metrics):
        if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in metrics.values()):
            raise ValueError("metrics must be finite numbers")
        return dict(metrics)

    def compare_with_baseline(self, metrics, baseline):
        return {k: value - baseline[k] for k, value in metrics.items() if k in baseline}
