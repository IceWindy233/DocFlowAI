import json
from pathlib import Path

from docflow.domain.agents import DraftRequirements
from docflow.services.draft_agent import _missing
from docflow.services.review_agent import deterministic_review

SAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "fixed-agent-samples-public-v1.json"
)


def _samples() -> dict:
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def test_fixed_sample_set_has_expected_distribution_and_unique_ids() -> None:
    data = _samples()
    groups = {
        "qa": data["qa_samples"],
        "review": data["review_samples"],
        "draft": data["draft_samples"],
    }
    assert data["schema_version"] == "1.0"
    assert {name: len(samples) for name, samples in groups.items()} == data[
        "selection_policy"
    ]["distribution"]
    ids = [sample["id"] for samples in groups.values() for sample in samples]
    assert len(ids) == len(set(ids)) == 19
    assert data["synthetic_data"] is True


def test_fixed_qa_evidence_has_stable_source_locator() -> None:
    for sample in _samples()["qa_samples"]:
        expected = sample["expected"]
        if expected["behavior"] == "ABSTAIN":
            assert expected["evidence"] == []
            assert expected["forbidden_patterns"]
            continue
        assert expected["required_facts"]
        assert expected["evidence"]
        for evidence in expected["evidence"]:
            assert len(evidence["source_sha256"]) == 64
            assert evidence["file_name"]
            assert evidence["page_number"] >= 1


def test_fixed_review_samples_match_deterministic_expectations() -> None:
    for sample in _samples()["review_samples"]:
        findings = deterministic_review(
            sample["text"],
            sample["title"],
            sample["scope"],
        )
        categories = [finding["category"] for finding in findings]
        expected = sample["expected"]
        for category in expected.get("required_categories", []):
            assert category in categories, sample["id"]
        for category, minimum in expected.get("minimum_category_counts", {}).items():
            assert categories.count(category) >= minimum, sample["id"]
        if "maximum_rule_finding_count" in expected:
            assert len(findings) <= expected["maximum_rule_finding_count"], sample["id"]
        if "maximum_finding_count" in expected:
            assert len(findings) <= expected["maximum_finding_count"], sample["id"]


def test_fixed_draft_samples_match_requirement_gate() -> None:
    for sample in _samples()["draft_samples"]:
        missing = _missing(DraftRequirements.model_validate(sample["requirements"]))
        expected = sample["expected"]
        for field in expected.get("required_missing_fields", []):
            assert field in missing, sample["id"]
        if expected["should_create"]:
            assert missing == [], sample["id"]
