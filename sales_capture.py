"""Prélecture prudente de notes de vente photographiées.

Les suggestions OCR ne deviennent jamais des ventes avant validation humaine.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image

import datapilot
import invoice_features
import ocr_layout


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
TABLE_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | TABLE_EXTENSIONS | {".pdf", ".txt"}
MAX_PHOTOS = 5
MAX_ROWS = 100


def valid_image(path: Path) -> bool:
    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            return False
        with Image.open(path) as image:
            if image.width * image.height > 25_000_000:
                return False
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def parse_amount(value: str) -> float | None:
    cleaned = re.sub(r"[\s\u00a0]", "", value).replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount <= 0 or amount > 100_000_000:
        return None
    return float(amount)


def parse_sale_lines(text: str, source: str, line_scores: list[float] | None = None) -> list[dict]:
    """N'accepte que des lignes simples avec un montant final non ambigu.

    ``line_scores`` (facultatif) donne la confiance OCR de chaque ligne ; elle est
    reportée sur la vente proposée pour signaler les lignes à vérifier en priorité.
    """
    rows = []
    scores = list(line_scores or [])

    def confidence(*indexes: int | None) -> dict:
        values = [scores[i] for i in indexes if i is not None and i < len(scores)]
        return {"confidence": round(min(values), 3)} if values else {}

    def is_summary(name: str) -> bool:
        lowered = name.lower().strip()
        if re.match(r"^(?:sous[- ]?total|subtotal|total|tva|taxe|solde|balance|net[ -]?[aà]?[ -]?payer)\b", lowered):
            return True
        # En-têtes de page (« Ventes du 23/09 », « Caisse du ») : l'OCR peut lire
        # la date comme un montant (« 23109 »). Ils ne sont jamais proposés.
        return bool(re.match(r"^(?:ventes?|date|journ[ée]e|recettes?|carnet|caisse|sales|page)\b", lowered)
                    or re.search(r"\b(?:du|le|au|of|on)$", lowered))
    pending = ""
    pending_index: int | None = None
    for index, raw in enumerate(text.splitlines()[:150]):
        line = raw.strip()
        if len(line) < 3:
            continue
        amount_only = re.fullmatch(r"\d+(?:[,.]\d{1,2})?", line)
        if amount_only and pending and not is_summary(pending):
            number = parse_amount(line)
            if number is not None:
                rows.append({"date": "", "product": pending, "quantity": "1", "amount": f"{number:.2f}", "source": source,
                             **confidence(pending_index, index)})
            pending = ""
            continue
        match = re.match(r"^(.*?)(?:\s+|\s*[:=]\s*)(\d+(?:[,.]\d{1,2})?)\s*(?:MAD|DH|DHS)?$", line, re.I)
        if not match:
            pending = line if re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ '-]{1,79}", line) else ""
            pending_index = index if pending else None
            continue
        pending = ""
        name = match.group(1).strip(" -:;.")
        if not re.search(r"[A-Za-zÀ-ÿ]", name) or len(name) > 80 or is_summary(name):
            continue
        amount = parse_amount(match.group(2))
        if amount is None:
            continue
        rows.append({"date": "", "product": name, "quantity": "1", "amount": f"{amount:.2f}", "source": source,
                     **confidence(index)})
        if len(rows) >= MAX_ROWS:
            break
    return rows


def detect_page_date(text: str, today: date | None = None) -> str:
    """Repère la date écrite en tête de page (« Ventes du 23/09 ») pour préremplir les lignes.

    Seules les premières lignes sont examinées ; l'année manquante est celle en cours.
    La date reste une proposition : elle est modifiable avant la confirmation.
    """
    today = today or date.today()
    for line in text.splitlines()[:3]:
        match = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?\b", line)
        if not match:
            continue
        day, month = int(match.group(1)), int(match.group(2))
        year = int(match.group(3)) if match.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return ""


def _inspect_image(path: Path) -> dict:
    try:
        lines = ocr_layout.read_lines(path)["lines"]
        text = "\n".join(line["text"] for line in lines)
        rows = parse_sale_lines(text, path.name, [line["score"] for line in lines])
        page_date = detect_page_date(text)
        for row in rows:
            row["date"] = row["date"] or page_date
        if rows:
            return {"status": "extracted", "message": f"{len(rows)} ligne(s) proposées depuis la photo par lecture locale. Vérifiez chaque montant.", "rows": rows}
    except (ImportError, OSError, RuntimeError, ValueError):
        pass
    extracted = invoice_features.extract_invoice_text(path)
    return {"status": extracted["status"], "message": extracted["message"], "rows": parse_sale_lines(extracted.get("text", ""), path.name)}


def _inspect_scanned_pdf(path: Path) -> dict | None:
    try:
        import pymupdf
    except ImportError:
        return None
    try:
        with pymupdf.open(path) as document, tempfile.TemporaryDirectory(prefix="datapilot-pdf-") as folder:
            rows: list[dict] = []
            for page_number in range(min(3, len(document))):
                pixmap = document[page_number].get_pixmap(dpi=150, alpha=False)
                if pixmap.width * pixmap.height > 25_000_000:
                    continue
                image_path = Path(folder) / f"page-{page_number + 1}.png"
                pixmap.save(image_path)
                found = _inspect_image(image_path)["rows"]
                for row in found:
                    row["source"] = path.name
                rows.extend(found)
            if rows:
                return {
                    "status": "extracted",
                    "message": f"{len(rows)} ligne(s) proposées depuis les pages scannées (3 pages maximum). Vérifiez-les.",
                    "rows": rows[:MAX_ROWS],
                    "truncated": len(rows) > MAX_ROWS or len(document) > 3,
                }
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def inspect_document(path: Path) -> dict:
    if path.suffix.lower() == ".txt":
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252")
        rows = parse_sale_lines(text, path.name)
        return {"status": "extracted" if rows else "needs_review", "message": f"{len(rows)} ligne(s) proposées depuis le texte. Vérifiez-les.", "rows": rows}
    if path.suffix.lower() in TABLE_EXTENSIONS:
        try:
            frame = datapilot.read_dataset(path)
        except (ValueError, OSError) as exc:
            return {"status": "unreadable", "message": f"Tableau non lu : {exc}", "rows": []}
        columns = {datapilot.normalize_text(str(name)).replace(" ", "_"): name for name in frame.columns}
        amount = next((columns[key] for key in ("montant", "total_ttc", "total", "ventes", "sales", "revenue", "chiffre_affaires_net", "net_revenue") if key in columns), None)
        product = next((columns[key] for key in ("produit", "product", "article", "libelle", "designation", "categorie", "category") if key in columns), None)
        quantity = next((columns[key] for key in ("quantite", "quantity", "qte") if key in columns), None)
        date = next((columns[key] for key in ("date", "date_vente", "date_facture") if key in columns), None)
        if amount is None:
            return {"status": "needs_mapping", "message": "Colonne de montant non reconnue : ajoutez les lignes manuellement.", "rows": []}
        rows = []
        for _, item in frame.head(MAX_ROWS).iterrows():
            raw_amount = item[amount]
            if datapilot._is_missing(raw_amount):
                continue
            raw_date = item[date] if date is not None else None
            parsed_date = datapilot._parse_local_date(raw_date) if raw_date is not None else None
            raw_quantity = item[quantity] if quantity is not None else 1
            quantity_text = (str(int(raw_quantity)) if isinstance(raw_quantity, (int, float)) and float(raw_quantity).is_integer()
                             else str(raw_quantity).strip())
            rows.append({
                "date": parsed_date.strftime("%Y-%m-%d") if parsed_date is not None and not datapilot._is_missing(parsed_date) else "",
                "product": str(item[product]).strip() if product is not None and not datapilot._is_missing(item[product]) else "",
                "quantity": quantity_text if quantity is not None and not datapilot._is_missing(raw_quantity) else "1",
                "amount": str(raw_amount).strip(),
                "source": path.name,
            })
        return {"status": "extracted", "message": f"{len(rows)} ligne(s) proposées depuis le tableau. Vérifiez-les.", "rows": rows, "truncated": len(frame) > MAX_ROWS}
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        return _inspect_image(path)
    extracted = invoice_features.extract_invoice_text(path)
    parsed_rows = parse_sale_lines(extracted.get("text", ""), path.name)
    if path.suffix.lower() == ".pdf" and not parsed_rows:
        scanned = _inspect_scanned_pdf(path)
        if scanned is not None:
            return scanned
    return {
        "status": extracted["status"],
        "message": extracted["message"],
        "rows": parsed_rows,
    }


def inspect_photo(path: Path) -> dict:
    return inspect_document(path)


def validate_sale(date: str, product: str, quantity: str, amount: str) -> dict:
    product = product.strip()
    if not product or len(product) > 80:
        raise ValueError("Indiquez un article de 80 caractères maximum.")
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("Vérifiez la date de la vente.") from exc
    if not re.fullmatch(r"\d{1,4}", quantity.strip()) or not 1 <= int(quantity) <= 9999:
        raise ValueError("La quantité doit être comprise entre 1 et 9 999.")
    number = parse_amount(amount)
    if number is None:
        raise ValueError("Le montant doit être un nombre positif, par exemple 25,50.")
    return {"date": date or None, "produit": product, "quantite": int(quantity), "montant": number}
