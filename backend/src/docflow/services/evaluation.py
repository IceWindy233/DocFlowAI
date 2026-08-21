from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass


def levenshtein_distance(reference: str, prediction: str) -> int:
    if len(reference) < len(prediction):
        reference, prediction = prediction, reference
    previous = list(range(len(prediction) + 1))
    for row, char_a in enumerate(reference, start=1):
        current = [row]
        for col, char_b in enumerate(prediction, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[col] + 1,
                    previous[col - 1] + (char_a != char_b),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, prediction: str) -> float:
    if not reference:
        return 0.0 if not prediction else 1.0
    return levenshtein_distance(reference, prediction) / len(reference)


@dataclass(frozen=True)
class ClassificationMetrics:
    precision: float
    recall: float
    f1: float
    accuracy: float


def classification_metrics(expected: Iterable[str], actual: Iterable[str]) -> ClassificationMetrics:
    expected_list = list(expected)
    actual_list = list(actual)
    if len(expected_list) != len(actual_list):
        raise ValueError("expected 和 actual 数量必须一致")
    labels = set(expected_list) | set(actual_list)
    true_positive = Counter()
    false_positive = Counter()
    false_negative = Counter()
    correct = 0
    for truth, prediction in zip(expected_list, actual_list, strict=True):
        if truth == prediction:
            correct += 1
            true_positive[truth] += 1
        else:
            false_positive[prediction] += 1
            false_negative[truth] += 1
    tp = sum(true_positive[label] for label in labels)
    fp = sum(false_positive[label] for label in labels)
    fn = sum(false_negative[label] for label in labels)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = correct / len(expected_list) if expected_list else 1.0
    return ClassificationMetrics(precision, recall, f1, accuracy)
