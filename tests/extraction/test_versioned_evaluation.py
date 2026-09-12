from __future__ import annotations

from pathlib import Path

import pytest

from scripts.evaluate_versioned_results import validate_reference
from scripts.run_versioned_evaluation import (
    DICTIONARY_PATH,
    _deterministic_only_config,
    _job_requires_model,
    _v6_jobs,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary


ROOT = Path(__file__).resolve().parents[2]
REFERENCES = (
    ROOT / "evaluation/reference/quote_V5/reference_answers.json",
    ROOT / "evaluation/reference/quote_V6/development/reference_answers.json",
    ROOT / "evaluation/reference/quote_V6/calibration/reference_answers.json",
    ROOT / "evaluation/reference/quote_V6/holdout/reference_answers.json",
)


@pytest.mark.parametrize("reference_path", REFERENCES)
def test_v5_v6_reference_metadata_and_input_hashes_are_valid(
    reference_path: Path,
) -> None:
    reference, dictionary, cases = validate_reference(reference_path)

    assert reference["runtime_access"] in {"DENY", "FORBIDDEN"}
    assert dictionary.version == "1.2.0"
    assert cases
    assert all((ROOT / case.input_path).is_file() for case in cases)


def test_registered_clean_csv_does_not_require_model_configuration() -> None:
    dictionary = QuoteDictionary.load(DICTIONARY_PATH)
    jobs = {job.case_id: job for job in _v6_jobs("holdout")}

    assert _job_requires_model(jobs["V6-HOLD-01"], dictionary) is False
    assert _job_requires_model(jobs["V6-HOLD-02"], dictionary) is True
    assert _job_requires_model(jobs["V6-HOLD-03"], dictionary) is True

    config = _deterministic_only_config()
    assert config.provider == "deterministic"
    assert config.model_id == "no-model-call"
