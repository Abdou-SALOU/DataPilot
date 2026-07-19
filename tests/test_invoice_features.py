import importlib
import json
from pathlib import Path

import invoice_features


SAMPLE_INVOICE = """
ACME Maroc SARL
FACTURE N° FA-2026-001
Date de facture : 17/07/2026
Sous-total HT : 1 000,00 MAD
TVA 20 % : 200,00 MAD
TOTAL TTC : 1 200,00 MAD
Merci pour votre confiance
"""


def test_parse_invoice_text_extracts_supported_fields_without_guessing():
    result = invoice_features.parse_invoice_text(SAMPLE_INVOICE, source_name="facture.pdf")
    fields = result["fields"]

    assert fields["supplier"]["value"] == "ACME Maroc SARL"
    assert fields["invoice_number"]["value"] == "FA-2026-001"
    assert fields["invoice_date"]["value"] == "2026-07-17"
    assert fields["subtotal_excluding_tax"]["value"] == 1000.0
    assert fields["total_including_tax"]["value"] == 1200.0
    assert fields["vat_amount"]["value"] == 200.0
    assert fields["vat_rate"]["value"] == 20.0
    assert fields["currency"]["value"] == "MAD"
    assert result["source_name"] == "facture.pdf"


def test_every_detected_value_requires_human_confirmation_and_has_evidence():
    result = invoice_features.parse_invoice_text(SAMPLE_INVOICE)
    detected = result["review"]["fields_to_confirm"]

    assert result["status"] == "needs_review"
    assert result["review"]["required"] is True
    assert detected
    for name in detected:
        assert result["fields"][name]["status"] == "to_confirm"
        assert result["fields"][name]["needs_confirmation"] is True
        assert result["fields"][name]["evidence"]


def test_explicit_supplier_and_english_invoice_are_supported():
    text = """
Supplier: Northwind Traders Ltd
Invoice Number: INV/734-A
Invoice date: 2026-07-05
VAT 15%: EUR 30.00
Amount due: EUR 230.00
"""
    fields = invoice_features.parse_invoice_text(text)["fields"]

    assert fields["supplier"]["value"] == "Northwind Traders Ltd"
    assert fields["invoice_number"]["value"] == "INV/734-A"
    assert fields["invoice_date"]["value"] == "2026-07-05"
    assert fields["total_including_tax"]["value"] == 230.0
    assert fields["vat_amount"]["value"] == 30.0
    assert fields["currency"]["value"] == "EUR"


def test_sparse_text_does_not_invent_invoice_values():
    result = invoice_features.parse_invoice_text("Merci pour votre confiance.\nÀ bientôt !")

    assert all(field["value"] is None for field in result["fields"].values())
    assert result["review"]["fields_to_confirm"] == []
    assert set(result["review"]["missing_required_fields"]) == {
        "supplier",
        "invoice_number",
        "invoice_date",
        "total_including_tax",
        "currency",
    }


def test_invalid_calendar_date_is_not_returned():
    result = invoice_features.parse_invoice_text(
        "Fournisseur : Atlas Services SARL\nFacture N° A-42\nDate facture : 31/02/2026"
    )

    assert result["fields"]["invoice_date"]["value"] is None
    assert result["fields"]["invoice_date"]["evidence"] is None


def test_date_label_is_not_mistaken_for_an_invoice_number():
    result = invoice_features.parse_invoice_text(
        "Date facture : 17 juillet 2026\nNet à payer : 2 880,00 DH"
    )

    assert result["fields"]["invoice_number"]["value"] is None
    assert result["fields"]["invoice_date"]["value"] == "2026-07-17"
    assert result["fields"]["total_including_tax"]["value"] == 2880.0


def test_due_date_is_not_mistaken_for_invoice_date():
    result = invoice_features.parse_invoice_text(
        "Facture N° FAC-7\nDate d'échéance : 30/08/2026\nTotal TTC : 500 MAD"
    )

    assert result["fields"]["invoice_date"]["value"] is None


def test_dollar_symbol_is_not_silently_converted_to_usd():
    result = invoice_features.parse_invoice_text("Grand total: $ 120.00")

    assert result["fields"]["total_including_tax"]["value"] == 120.0
    assert result["fields"]["currency"]["value"] == "$"
    assert result["fields"]["currency"]["review_priority"] == "high"


def test_contract_is_json_serializable():
    encoded = json.dumps(invoice_features.parse_invoice_text(SAMPLE_INVOICE), ensure_ascii=False)

    assert '"contract_version": "1.0"' in encoded
    assert '"external_transmission": false' in encoded


def test_unsupported_file_returns_a_simple_status(tmp_path: Path):
    path = tmp_path / "facture.docx"
    path.write_bytes(b"not an invoice")

    extraction = invoice_features.extract_invoice_text(path)

    assert extraction["status"] == "unsupported_format"
    assert "PDF" in extraction["message"]


def test_missing_image_ocr_is_reported_without_crashing(tmp_path: Path, monkeypatch):
    path = tmp_path / "facture.png"
    path.write_bytes(b"fake image")
    original_import = importlib.import_module

    def import_without_ocr(name, package=None):
        if name in {"PIL.Image", "pytesseract"}:
            raise ModuleNotFoundError(name)
        return original_import(name, package)

    monkeypatch.setattr(invoice_features.importlib, "import_module", import_without_ocr)
    extraction = invoice_features.extract_invoice_text(path)

    assert extraction["status"] == "dependency_missing"
    assert "install" in extraction["message"].lower()


def test_process_invoice_does_not_expose_raw_text_when_extraction_is_unavailable(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "facture.jpg"
    path.write_bytes(b"fake image")

    monkeypatch.setattr(
        invoice_features,
        "extract_invoice_text",
        lambda unused: {
            "status": "dependency_missing",
            "message": "OCR indisponible.",
            "filename": "facture.jpg",
            "extension": ".jpg",
            "method": None,
            "pages": None,
            "text": "",
        },
    )
    result = invoice_features.process_invoice(path)

    assert result["status"] == "dependency_missing"
    assert result["message"] == "OCR indisponible."
    assert "text" not in result["extraction"]
    assert result["extraction"]["text_characters"] == 0
