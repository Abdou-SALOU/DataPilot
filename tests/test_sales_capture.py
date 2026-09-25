import io
import re
from pathlib import Path

from PIL import Image
import pytest

import app as datapilot_app
import sales_capture


def test_photo_line_parser_is_conservative():
    rows = sales_capture.parse_sale_lines("Pain 25,50\nTotal 100\nLait : 12.00\n", "photo.png")
    assert [row["product"] for row in rows] == ["Pain", "Lait"]
    assert [row["amount"] for row in rows] == ["25.50", "12.00"]
    paired = sales_capture.parse_sale_lines("Pain\n25,50\nLait\n18,00\nTotal\n43,50", "photo.png")
    assert [row["product"] for row in paired] == ["Pain", "Lait"]


def test_mixed_documents_require_confirmation_before_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(datapilot_app, "STORAGE_ROOT", tmp_path / "storage")
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    test_client = datapilot_app.app.test_client()
    test_client.get("/")
    with test_client.session_transaction() as state:
        token = state["_csrf"]
    photo = io.BytesIO()
    Image.new("RGB", (80, 80), "white").save(photo, format="PNG")
    photo.seek(0)
    response = test_client.post("/documents", data={
        "_csrf": token,
        "purpose": "sales",
        "documents": [
            (io.BytesIO(b"produit,montant\nPain,25.5\n"), "ventes.csv"),
            (photo, "carnet.png"),
        ],
    }, content_type="multipart/form-data")
    assert response.status_code == 302
    review_url = response.headers["Location"]
    project_id = re.search(r"/project/([0-9a-f]{32})/", review_url).group(1)
    assert not (tmp_path / "storage" / project_id / "raw.csv").exists()
    page = test_client.get(review_url).get_data(as_text=True)
    assert "Pain" in page
    assert "carnet.png" in page

    confirmed = test_client.post(f"/project/{project_id}/documents/confirm", data={
        "_csrf": token,
        "date[]": ["2026-09-23"],
        "product[]": ["Pain"],
        "quantity[]": ["2"],
        "amount[]": ["25,50"],
        "source[]": [""],
    }, follow_redirects=True)
    assert confirmed.status_code == 200
    assert "Ventes déclarées" in confirmed.get_data(as_text=True)
    assert "25,50" in confirmed.get_data(as_text=True)


def test_mobile_network_requires_configured_access_code(monkeypatch):
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    test_client = datapilot_app.app.test_client()
    monkeypatch.delenv("DATAPILOT_ACCESS_CODE", raising=False)
    blocked = test_client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.8"})
    assert blocked.status_code == 403

    monkeypatch.setenv("DATAPILOT_ACCESS_CODE", "test-mobile-code-123")
    login = test_client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.8"})
    assert login.status_code == 302
    assert "/access" in login.headers["Location"]
    access_page = test_client.get("/access", environ_overrides={"REMOTE_ADDR": "192.168.1.8"})
    assert access_page.status_code == 200
    with test_client.session_transaction() as state:
        token = state["_csrf"]
    unlocked = test_client.post("/access", data={"_csrf": token, "access_code": "test-mobile-code-123"}, environ_overrides={"REMOTE_ADDR": "192.168.1.8"})
    assert unlocked.status_code == 302
    home = test_client.get("/", environ_overrides={"REMOTE_ADDR": "192.168.1.8"})
    assert home.status_code == 200


def test_scanned_pdf_uses_local_ocr_on_demo_document():
    pytest.importorskip("rapidocr")
    pytest.importorskip("pymupdf")
    source = Path(__file__).resolve().parents[1] / "demo" / "essai_boutique_atlas" / "carnet_scan.pdf"
    result = sales_capture.inspect_document(source)
    assert result["status"] == "extracted"
    assert len(result["rows"]) >= 4
    assert sum(row["amount"] == "25.50" for row in result["rows"]) == 1


def test_ocr_layout_keeps_amounts_on_their_row_when_photo_is_tilted():
    import math
    import ocr_layout

    def box(x, y, width, height, angle_deg):
        a = math.radians(angle_deg)
        dx, dy = math.cos(a), math.sin(a)
        corners = [(0, 0), (width, 0), (width, height), (0, height)]
        return [(x + cx * dx - cy * dy, y + cx * dy + cy * dx) for cx, cy in corners]

    angle = -4.0
    boxes, texts = [], []
    for row, (item, amount) in enumerate([("Sandwich", "25,00"), ("Jus orange", "15,00"), ("Eau", "5,00")]):
        y = 100 + row * 60
        boxes.append(box(100, y, 220, 40, angle))
        texts.append(item)
        # Montant écrit à droite : l'inclinaison le fait remonter au-dessus de l'article.
        shift = 600 * math.tan(math.radians(angle))
        boxes.append(box(700, y + shift, 120, 40, angle))
        texts.append(amount)
    order = sorted(range(len(texts)), key=lambda i: sum(p[1] for p in boxes[i]))
    lines = ocr_layout.group_lines([boxes[i] for i in order], [texts[i] for i in order], [0.99] * len(texts))
    assert [line["text"] for line in lines] == ["Sandwich 25,00", "Jus orange 15,00", "Eau 5,00"]


def test_page_headers_are_not_proposed_as_sales():
    rows = sales_capture.parse_sale_lines("Ventes du 23109\nPain 12,50\nCaisse du 2409\nLait 18,00", "carnet.png", [0.9, 0.99, 0.9, 0.97])
    assert [(row["product"], row["amount"]) for row in rows] == [("Pain", "12.50"), ("Lait", "18.00")]
    assert rows[0]["confidence"] == 0.99
