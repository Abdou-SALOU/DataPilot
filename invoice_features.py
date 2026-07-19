# -*- coding: utf-8 -*-
"""Import local et prudent de factures PDF ou image.

Le module est volontairement déterministe : il extrait uniquement des valeurs
présentes dans le texte et conserve, pour chaque valeur, la ligne qui a servi de
preuve. Aucun champ n'est considéré comme validé avant confirmation humaine.

Les dépendances de lecture sont optionnelles afin de ne pas bloquer DataPilot :

* PDF texte : ``pdfplumber`` puis ``pypdf`` en solution de repli ;
* image : ``Pillow`` et ``pytesseract`` (le programme Tesseract doit aussi être
  installé sur la machine).
"""

from __future__ import annotations

import importlib
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}
IMAGE_EXTENSIONS = SUPPORTED_EXTENSIONS - {".pdf"}
MAX_FILE_BYTES = 20 * 1024 * 1024
CONTRACT_VERSION = "1.0"

FIELD_LABELS = {
    "supplier": "Fournisseur",
    "invoice_number": "Numéro de facture",
    "invoice_date": "Date de facture",
    "subtotal_excluding_tax": "Total HT",
    "total_including_tax": "Total TTC",
    "vat_amount": "Montant de TVA",
    "vat_rate": "Taux de TVA",
    "currency": "Devise",
}

REQUIRED_FIELDS = {
    "supplier",
    "invoice_number",
    "invoice_date",
    "total_including_tax",
    "currency",
}

MONTHS = {
    "janvier": 1,
    "january": 1,
    "fevrier": 2,
    "february": 2,
    "mars": 3,
    "march": 3,
    "avril": 4,
    "april": 4,
    "mai": 5,
    "may": 5,
    "juin": 6,
    "june": 6,
    "juillet": 7,
    "july": 7,
    "aout": 8,
    "august": 8,
    "septembre": 9,
    "september": 9,
    "octobre": 10,
    "october": 10,
    "novembre": 11,
    "november": 11,
    "decembre": 12,
    "december": 12,
}


def _plain(value: str) -> str:
    """Minuscule sans accents, uniquement pour comparer des libellés."""

    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text or "").replace("\x00", " ").splitlines():
        line = re.sub(r"[\t \u00a0\u202f]+", " ", raw_line).strip(" |")
        if line:
            lines.append(line[:500])
    return lines


def _field(
    name: str,
    value: Any = None,
    *,
    confidence: float = 0.0,
    evidence: str | None = None,
    raw_value: str | None = None,
) -> dict[str, Any]:
    detected = value is not None
    return {
        "label": FIELD_LABELS[name],
        "value": value,
        "raw_value": raw_value if detected else None,
        "confidence": round(float(confidence), 2) if detected else 0.0,
        "status": "to_confirm" if detected else "missing",
        "evidence": evidence if detected else None,
        "needs_confirmation": detected,
        "review_priority": ("high" if detected and confidence < 0.8 else "normal") if detected else None,
    }


def _extract_supplier(lines: list[str]) -> dict[str, Any]:
    explicit = re.compile(
        r"^(?:fournisseur|prestataire|vendeur|[ée]metteur|supplier|vendor)\s*[:\-]\s*(.+)$",
        re.IGNORECASE,
    )
    forbidden = re.compile(
        r"\b(?:client|facture|invoice|total|tva|vat|date|adresse|address|ice|if|rc)\b",
        re.IGNORECASE,
    )
    for line in lines[:20]:
        match = explicit.match(line)
        if match:
            candidate = match.group(1).strip(" :-")
            if 2 <= len(candidate) <= 120 and not re.fullmatch(r"[\d\W]+", candidate):
                return _field(
                    "supplier", candidate, confidence=0.97, evidence=line, raw_value=candidate
                )

    # Sans libellé explicite, nous ne retenons qu'une raison sociale portant une
    # forme juridique. Cela évite d'inventer un fournisseur à partir d'une adresse
    # ou d'un simple message présent en haut du document.
    legal_form = re.compile(
        r"\b(?:sarl(?:\s+au)?|s\.?a\.?|sas(?:u)?|eurl|llc|ltd\.?|inc\.?|gmbh)\b",
        re.IGNORECASE,
    )
    for line in lines[:10]:
        if (
            2 <= len(line) <= 120
            and legal_form.search(line)
            and not forbidden.search(line)
            and not re.fullmatch(r"[\d\W]+", line)
        ):
            return _field("supplier", line, confidence=0.74, evidence=line, raw_value=line)
    return _field("supplier")


def _extract_invoice_number(lines: list[str]) -> dict[str, Any]:
    patterns = [
        (
            re.compile(
                r"\b(?:facture|invoice)\s*(?:num[ée]ro|number|n[°ºo]|no\.?|#)\s*[:#\-]?\s*([a-z0-9][a-z0-9._/\-]{1,})",
                re.IGNORECASE,
            ),
            0.98,
        ),
        (
            re.compile(
                r"\b(?:num[ée]ro|number|n[°ºo]|no\.?)\s*(?:de\s+(?:la\s+)?)?(?:facture|invoice)\s*[:#\-]\s*([a-z0-9][a-z0-9._/\-]{1,})",
                re.IGNORECASE,
            ),
            0.98,
        ),
        (
            re.compile(
                r"\b(?:r[ée]f(?:[ée]rence)?(?:\s+facture)?|invoice\s+id)\s*[:#\-]\s*([a-z0-9][a-z0-9._/\-]{1,})",
                re.IGNORECASE,
            ),
            0.93,
        ),
        (
            re.compile(
                r"^\s*(?:facture|invoice)\s*[:#]\s*([a-z0-9][a-z0-9._/\-]{1,})",
                re.IGNORECASE,
            ),
            0.86,
        ),
    ]
    stopwords = {"date", "numero", "number", "facture", "invoice", "du", "de", "the"}
    for line in lines:
        for pattern, confidence in patterns:
            match = pattern.search(line)
            if not match:
                continue
            candidate = match.group(1).strip(" .,:;#")
            if candidate and _plain(candidate) not in stopwords:
                return _field(
                    "invoice_number",
                    candidate,
                    confidence=confidence,
                    evidence=line,
                    raw_value=candidate,
                )
    return _field("invoice_number")


def _parse_date(raw_value: str) -> tuple[str, float] | None:
    candidate = re.sub(r"\s+", " ", raw_value.strip(" .,:;"))
    numeric_match = re.search(r"\b(\d{1,4})[./\-](\d{1,2})[./\-](\d{1,4})\b", candidate)
    if numeric_match:
        first, second, third = numeric_match.groups()
        try:
            if len(first) == 4:
                parsed = datetime(int(first), int(second), int(third))
                confidence = 0.98
            else:
                year = int(third)
                year += 2000 if year < 70 else 1900 if year < 100 else 0
                parsed = datetime(year, int(second), int(first))
                confidence = 0.9
        except ValueError:
            return None
        return parsed.date().isoformat(), confidence

    plain = _plain(candidate)
    words_match = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", plain)
    if words_match and words_match.group(2) in MONTHS:
        try:
            parsed = datetime(
                int(words_match.group(3)), MONTHS[words_match.group(2)], int(words_match.group(1))
            )
        except ValueError:
            return None
        return parsed.date().isoformat(), 0.95
    return None


def _extract_invoice_date(lines: list[str]) -> dict[str, Any]:
    labels = [
        (r"date\s+(?:de\s+(?:la\s+)?)?facture", 0.98),
        (r"date\s+d['’]?emission", 0.96),
        (r"invoice\s+date", 0.98),
        (r"(?:emise|issued)\s+(?:le|on)", 0.93),
        (r"date", 0.82),
    ]
    for line in lines:
        plain_line = _plain(line)
        # Une date d'échéance n'est pas une date de facture. Elle n'est retenue
        # que si le libellé précise aussi explicitement « facture » ou « émission ».
        if (
            re.search(r"\b(?:echeance|due\s+date|date\s+limite|payment\s+date)\b", plain_line)
            and not re.search(r"\b(?:facture|invoice\s+date|emission)\b", plain_line)
        ):
            continue
        for label, label_confidence in labels:
            match = re.search(label + r"\s*[:\-]?\s*(.+)$", plain_line, re.IGNORECASE)
            if not match:
                continue
            parsed = _parse_date(match.group(1))
            if parsed:
                value, format_confidence = parsed
                raw_match = re.search(
                    r"(?:\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}|\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+\d{4})",
                    line,
                )
                raw_value = raw_match.group(0) if raw_match else match.group(1).strip()
                return _field(
                    "invoice_date",
                    value,
                    confidence=min(label_confidence, format_confidence),
                    evidence=line,
                    raw_value=raw_value,
                )
    return _field("invoice_date")


AMOUNT_PATTERN = re.compile(
    r"(?<![\w])[-+]?\d(?:[\d \u00a0\u202f.,']*\d)?(?![\w])"
)


def _parse_amount(raw_value: str) -> float | None:
    value = re.sub(r"[\s\u00a0\u202f']+", "", raw_value.strip())
    if not value or not re.search(r"\d", value):
        return None
    sign = ""
    if value[0] in "+-":
        sign, value = value[0], value[1:]

    comma_count = value.count(",")
    dot_count = value.count(".")
    if comma_count and dot_count:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "")
        value = value.replace(decimal_separator, ".")
    elif comma_count or dot_count:
        separator = "," if comma_count else "."
        groups = value.split(separator)
        if len(groups) == 2 and len(groups[1]) in {1, 2}:
            value = groups[0] + "." + groups[1]
        elif len(groups) > 2 and len(groups[-1]) in {1, 2}:
            value = "".join(groups[:-1]) + "." + groups[-1]
        else:
            value = "".join(groups)
    try:
        number = Decimal(sign + value)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return float(number)


def _amounts_in_line(line: str) -> list[tuple[str, float]]:
    amounts: list[tuple[str, float]] = []
    for match in AMOUNT_PATTERN.finditer(line):
        # Un pourcentage est un taux, pas un montant monétaire.
        if re.match(r"\s*%", line[match.end() :]):
            continue
        value = _parse_amount(match.group(0))
        if value is not None:
            amounts.append((match.group(0).strip(), value))
    return amounts


def _extract_labeled_amount(
    lines: Iterable[str], field_name: str, label_patterns: Iterable[str], confidence: float
) -> dict[str, Any]:
    for line in lines:
        plain_line = _plain(line)
        if not any(re.search(pattern, plain_line, re.IGNORECASE) for pattern in label_patterns):
            continue
        amounts = _amounts_in_line(line)
        if amounts:
            raw_value, value = amounts[-1]
            return _field(
                field_name,
                value,
                confidence=confidence,
                evidence=line,
                raw_value=raw_value,
            )
    return _field(field_name)


def _extract_total(lines: list[str]) -> dict[str, Any]:
    labels = [
        r"\btotal\s+t\.?t\.?c\.?(?:\b|\s|:)",
        r"\bmontant\s+(?:ttc|total)\b",
        r"\bnet\s+a\s+payer\b",
        r"\bgrand\s+total\b",
        r"\bamount\s+due\b",
        r"\btotal\s+including\s+tax\b",
    ]
    return _extract_labeled_amount(lines, "total_including_tax", labels, 0.97)


def _extract_subtotal(lines: list[str]) -> dict[str, Any]:
    labels = [
        r"\bsous[\s-]*total\s+h\.?t\.?(?:\b|\s|:)",
        r"\btotal\s+h\.?t\.?(?:\b|\s|:)",
        r"\bsubtotal\s+excluding\s+tax\b",
    ]
    return _extract_labeled_amount(lines, "subtotal_excluding_tax", labels, 0.95)


def _extract_vat(lines: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    vat_lines = [
        line
        for line in lines
        if re.search(r"\b(?:tva|vat|taxe\s+sur\s+(?:la\s+)?valeur\s+ajoutee)\b", _plain(line))
    ]
    amount = _extract_labeled_amount(vat_lines, "vat_amount", [r"."], 0.91)

    for line in vat_lines:
        match = re.search(r"\b(?:TVA|VAT)\b[^\n]{0,40}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%", line, re.IGNORECASE)
        if match:
            rate = _parse_amount(match.group(1))
            if rate is not None and 0 <= rate <= 100:
                return amount, _field(
                    "vat_rate",
                    rate,
                    confidence=0.96,
                    evidence=line,
                    raw_value=match.group(1) + "%",
                )
    return amount, _field("vat_rate")


def _extract_currency(lines: list[str], preferred_evidence: str | None = None) -> dict[str, Any]:
    currency_patterns = [
        (r"\bMAD\b", "MAD", 0.99),
        (r"\b(?:DHS?|DIRHAMS?)\b", "MAD", 0.92),
        (r"\bEUR\b|€", "EUR", 0.99),
        (r"\bUSD\b", "USD", 0.99),
        (r"\bCAD\b", "CAD", 0.99),
        (r"\bAUD\b", "AUD", 0.99),
        (r"\bGBP\b|£", "GBP", 0.99),
        # Le symbole $ seul ne permet pas de décider entre USD, CAD et AUD.
        (r"\$", "$", 0.72),
    ]
    candidates = ([preferred_evidence] if preferred_evidence else []) + lines
    seen: set[str] = set()
    for line in candidates:
        if not line or line in seen:
            continue
        seen.add(line)
        for pattern, code, confidence in currency_patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                return _field(
                    "currency",
                    code,
                    confidence=confidence,
                    evidence=line,
                    raw_value=match.group(0),
                )
    return _field("currency")


def parse_invoice_text(text: str, *, source_name: str | None = None) -> dict[str, Any]:
    """Analyse un texte de facture sans appel réseau ni valeur supposée.

    Le résultat est directement sérialisable en JSON. Une valeur détectée est
    toujours accompagnée de sa preuve et marquée ``to_confirm``.
    """

    lines = _clean_lines(text)
    fields: dict[str, dict[str, Any]] = {
        "supplier": _extract_supplier(lines),
        "invoice_number": _extract_invoice_number(lines),
        "invoice_date": _extract_invoice_date(lines),
        "subtotal_excluding_tax": _extract_subtotal(lines),
        "total_including_tax": _extract_total(lines),
    }
    fields["vat_amount"], fields["vat_rate"] = _extract_vat(lines)
    fields["currency"] = _extract_currency(
        lines, preferred_evidence=fields["total_including_tax"].get("evidence")
    )

    detected = [name for name, field in fields.items() if field["value"] is not None]
    missing = [name for name, field in fields.items() if field["value"] is None]
    missing_required = [name for name in FIELD_LABELS if name in REQUIRED_FIELDS and name in missing]
    confidences = [fields[name]["confidence"] for name in detected]
    average_confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    required_detected = len(REQUIRED_FIELDS) - len(missing_required)

    if not lines:
        status = "no_text"
        message = "Aucun texte exploitable n'a été trouvé dans ce document."
    elif not detected:
        status = "needs_review"
        message = "Le texte a été lu, mais aucun champ de facture certain n'a été détecté."
    else:
        status = "needs_review"
        message = (
            f"{len(detected)} champ{'s' if len(detected) > 1 else ''} détecté"
            f"{'s' if len(detected) > 1 else ''}. Vérifiez les valeurs avant de continuer."
        )

    return {
        "contract_version": CONTRACT_VERSION,
        "kind": "invoice_extraction",
        "status": status,
        "message": message,
        "source_name": Path(source_name).name if source_name else None,
        "fields": fields,
        "review": {
            "required": bool(detected),
            "fields_to_confirm": detected,
            "missing_fields": missing,
            "missing_required_fields": missing_required,
            "average_confidence": average_confidence,
            "required_fields_detected": required_detected,
            "required_fields_total": len(REQUIRED_FIELDS),
            "score_explanation": (
                "La confiance moyenne est la moyenne des scores des règles ayant trouvé une preuve. "
                "Elle ne remplace pas la confirmation humaine."
            ),
        },
        "privacy": {
            "processing": "local",
            "external_transmission": False,
        },
        "text_summary": {
            "characters": len(str(text or "")),
            "non_empty_lines": len(lines),
        },
    }


def _base_extraction(path: Path) -> dict[str, Any]:
    return {
        "status": "pending",
        "message": "Lecture du document en cours.",
        "filename": path.name,
        "extension": path.suffix.lower(),
        "method": None,
        "pages": None,
        "text": "",
    }


def _extract_pdf(path: Path) -> dict[str, Any]:
    result = _base_extraction(path)
    available = False
    errors: list[str] = []

    try:
        pdfplumber = importlib.import_module("pdfplumber")
        available = True
        try:
            with pdfplumber.open(str(path)) as document:
                chunks = [(page.extract_text() or "") for page in document.pages]
            text = "\n".join(chunks).strip()
            if text:
                result.update(
                    status="extracted",
                    message="Le texte du PDF a été lu localement.",
                    method="pdfplumber",
                    pages=len(chunks),
                    text=text,
                )
                return result
            result["pages"] = len(chunks)
        except Exception as exc:  # Une seconde bibliothèque peut encore réussir.
            errors.append(f"pdfplumber: {type(exc).__name__}")
    except (ImportError, ModuleNotFoundError):
        pass

    try:
        pypdf = importlib.import_module("pypdf")
        available = True
        try:
            reader = pypdf.PdfReader(str(path))
            chunks = [(page.extract_text() or "") for page in reader.pages]
            text = "\n".join(chunks).strip()
            if text:
                result.update(
                    status="extracted",
                    message="Le texte du PDF a été lu localement.",
                    method="pypdf",
                    pages=len(chunks),
                    text=text,
                )
                return result
            result["pages"] = len(chunks)
        except Exception as exc:
            errors.append(f"pypdf: {type(exc).__name__}")
    except (ImportError, ModuleNotFoundError):
        pass

    if not available:
        result.update(
            status="dependency_missing",
            message="La lecture PDF n'est pas installée. Ajoutez pdfplumber ou pypdf pour activer cette fonction.",
        )
    elif errors:
        result.update(
            status="extraction_error",
            message="Ce PDF n'a pas pu être lu. Vérifiez qu'il n'est pas protégé ou endommagé.",
        )
    else:
        result.update(
            status="no_text",
            message=(
                "Ce PDF ne contient pas de texte sélectionnable. S'il s'agit d'un scan, "
                "importez une image de la facture pour utiliser l'OCR."
            ),
        )
    return result


def _extract_image(path: Path) -> dict[str, Any]:
    result = _base_extraction(path)
    try:
        image_module = importlib.import_module("PIL.Image")
        pytesseract = importlib.import_module("pytesseract")
    except (ImportError, ModuleNotFoundError):
        result.update(
            status="dependency_missing",
            message=(
                "La lecture des images n'est pas installée. Ajoutez Pillow, pytesseract "
                "et Tesseract OCR pour l'activer."
            ),
        )
        return result

    try:
        with image_module.open(path) as image:
            try:
                text = pytesseract.image_to_string(image, lang="fra+eng")
            except Exception:
                # Certaines installations n'ont que la langue par défaut.
                text = pytesseract.image_to_string(image)
    except Exception as exc:
        if "tesseract" in _plain(str(exc)) or "tesseract" in _plain(type(exc).__name__):
            result.update(
                status="dependency_missing",
                message=(
                    "Tesseract OCR n'est pas disponible sur cet ordinateur. Le fichier est conservé, "
                    "mais son texte ne peut pas encore être lu automatiquement."
                ),
            )
        else:
            result.update(
                status="extraction_error",
                message="Cette image n'a pas pu être lue. Vérifiez son format ou essayez une autre photo.",
            )
        return result

    text = str(text or "").strip()
    if not text:
        result.update(
            status="no_text",
            message="Aucun texte lisible n'a été trouvé. Essayez une photo plus nette et bien cadrée.",
            method="tesseract",
        )
        return result
    result.update(
        status="extracted",
        message="Le texte de l'image a été lu localement.",
        method="tesseract",
        pages=1,
        text=text,
    )
    return result


def extract_invoice_text(path: str | Path) -> dict[str, Any]:
    """Extrait localement le texte d'un PDF ou d'une image, sans exception UI."""

    file_path = Path(path)
    result = _base_extraction(file_path)
    if not file_path.is_file():
        result.update(status="file_not_found", message="Le fichier sélectionné est introuvable.")
        return result
    extension = file_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        result.update(
            status="unsupported_format",
            message="Format non pris en charge. Utilisez PDF, PNG, JPG, TIFF ou WEBP.",
        )
        return result
    try:
        size = file_path.stat().st_size
    except OSError:
        result.update(status="file_not_found", message="Le fichier sélectionné est inaccessible.")
        return result
    if size > MAX_FILE_BYTES:
        result.update(
            status="file_too_large",
            message="Ce fichier dépasse 20 Mo. Réduisez sa taille avant de réessayer.",
        )
        return result
    return _extract_pdf(file_path) if extension == ".pdf" else _extract_image(file_path)


def process_invoice(path: str | Path) -> dict[str, Any]:
    """Lit et analyse une facture avec un contrat JSON stable pour l'interface."""

    extraction = extract_invoice_text(path)
    parsed = parse_invoice_text(extraction.get("text", ""), source_name=extraction.get("filename"))
    public_extraction = {
        key: value for key, value in extraction.items() if key != "text"
    }
    public_extraction["text_characters"] = len(extraction.get("text", ""))
    parsed["extraction"] = public_extraction

    if extraction["status"] != "extracted":
        parsed["status"] = extraction["status"]
        parsed["message"] = extraction["message"]
    return parsed


__all__ = [
    "CONTRACT_VERSION",
    "MAX_FILE_BYTES",
    "SUPPORTED_EXTENSIONS",
    "extract_invoice_text",
    "parse_invoice_text",
    "process_invoice",
]
