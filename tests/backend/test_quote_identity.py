from pathlib import Path

import pytest

from supplier_comparison.backend.quote_identity import identify_supplier_id


ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "data/generated/demos/full_flow_demo4/quotes"


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("great_wall", "SUP-029"),
        ("redwood", "SUP-022"),
        ("schwarzwald", "SUP-023"),
        ("sterling", "SUP-024"),
    ],
)
def test_identifies_explicit_supplier_id_from_registered_csv_field(stem: str, expected: str) -> None:
    result = identify_supplier_id(
        (DEMO / f"csv/{stem}_quote.csv").read_bytes(),
        filename=f"{stem}_quote.csv",
        media_type="text/csv",
    )
    assert result == {
        "status": "FOUND",
        "supplier_id": expected,
        "candidates": [expected],
        "source": "CSV_FIELD",
    }


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("great_wall", "SUP-029"),
        ("redwood", "SUP-022"),
        ("schwarzwald", "SUP-023"),
        ("sterling", "SUP-024"),
    ],
)
def test_identifies_explicit_supplier_id_from_pdf_text(stem: str, expected: str) -> None:
    result = identify_supplier_id(
        (DEMO / f"pdf/{stem}_quote.pdf").read_bytes(),
        filename=f"{stem}_quote.pdf",
        media_type="application/pdf",
    )
    assert result["status"] == "FOUND"
    assert result["supplier_id"] == expected
    assert result["source"] == "PDF_TEXT"


def test_does_not_guess_an_id_and_reports_multiple_ids() -> None:
    missing = identify_supplier_id(
        b"supplier_name,price\nExample,10\n",
        filename="quote.csv",
        media_type="text/csv",
    )
    assert missing["status"] == "NOT_FOUND"
    assert missing["supplier_id"] is None

    ambiguous = identify_supplier_id(
        b"supplier_id,price\nSUP-001,10\nSUP-002,11\n",
        filename="quote.csv",
        media_type="text/csv",
    )
    assert ambiguous["status"] == "AMBIGUOUS"
    assert ambiguous["supplier_id"] is None
    assert ambiguous["candidates"] == ["SUP-001", "SUP-002"]
