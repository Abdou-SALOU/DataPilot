# -*- coding: utf-8 -*-
"""Application web locale DataPilot."""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import tempfile
import unicodedata
import uuid
import csv
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from threading import RLock

import pandas as pd
from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.utils import secure_filename

import datapilot
import advanced_features
import groq_analysis
import invoice_features
import name_translation
import rename_features
import task_queue


BASE_DIR = Path(__file__).resolve().parent
STORAGE_ROOT = Path(os.environ.get("DATAPILOT_STORAGE", BASE_DIR / "storage"))
PROJECT_RE = re.compile(r"^[0-9a-f]{32}$")
CHART_ID_RE = re.compile(r"^[0-9a-f]{12}$")
INVOICE_FIELD_COLUMNS = {
    "supplier": "fournisseur",
    "invoice_number": "numero_facture",
    "invoice_date": "date_facture",
    "subtotal_excluding_tax": "total_ht",
    "vat_amount": "tva",
    "total_including_tax": "total_ttc",
    "currency": "devise",
}

app = Flask(__name__)
app.secret_key = os.environ.get("DATAPILOT_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=20 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


# Les mêmes indicateurs étaient reconstruits à chaque interaction, y compris
# lorsqu'aucune donnée n'avait changé. Ce petit cache conserve uniquement les
# deux dernières analyses en mémoire et est invalidé à chaque écriture liée à
# un projet. Les fichiers restent la source de vérité.
PROJECT_VIEW_CACHE_LIMIT = 2
_project_view_cache: OrderedDict[tuple[str, tuple], dict] = OrderedDict()
_project_view_cache_lock = RLock()


def file_signature(path: Path) -> tuple[str, int, int] | None:
    """Identifie une ressource sans lire tout son contenu."""
    try:
        details = path.stat()
    except OSError:
        return None
    return (path.name, details.st_mtime_ns, details.st_size)


def invalidate_project_view(project_id: str) -> None:
    """Écarte les vues dérivées devenues obsolètes pour un projet."""
    with _project_view_cache_lock:
        stale_keys = [key for key in _project_view_cache if key[0] == project_id]
        for key in stale_keys:
            _project_view_cache.pop(key, None)


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def counted_fr(count: int, singular: str, plural: str | None = None) -> str:
    """Formate un compte avec un libellé français correctement accordé."""
    amount = int(count)
    label = singular if amount == 1 else (plural or f"{singular}s")
    return f"{amount} {label}"


def wants_json_response() -> bool:
    """Détecte les interactions asynchrones sans modifier les routes HTML."""
    return (
        request.headers.get("X-Requested-With") == "DataPilot"
        or request.accept_mimetypes.best == "application/json"
    )


def json_response(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def normalize_answer(answer: dict, *, question: str, mode: str) -> dict:
    """Stabilise le contrat consommé par l'interface, quelle que soit la source."""
    return {
        "ok": bool(answer.get("ok")),
        "answer": str(answer.get("answer", "Réponse indisponible.")).strip(),
        "question": question,
        "mode": mode,
        "source": str(answer.get("source", "local")),
        "insights": [str(item) for item in answer.get("insights", []) if str(item).strip()][:5],
        "cautions": [str(item) for item in answer.get("cautions", []) if str(item).strip()][:3],
        "evidence": [str(item) for item in answer.get("evidence", []) if str(item).strip()][:3],
        "suggested_questions": [
            str(item) for item in answer.get("suggested_questions", []) if str(item).strip()
        ][:3],
    }


def excel_export_value(value: object) -> object | None:
    """Convertit une cellule Pandas en valeur stable pour un fichier Excel."""
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except ValueError:
            pass
    return value


def make_excel_export(frame: pd.DataFrame):
    """Construit un XLSX sur disque temporaire, sans saturer la mémoire."""
    import xlsxwriter

    payload = tempfile.TemporaryFile(mode="w+b", suffix=".xlsx")
    try:
        workbook = xlsxwriter.Workbook(payload, {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        })
        worksheet = workbook.add_worksheet("Donnees_nettoyees")
        header_format = workbook.add_format({
            "bold": True,
            "bg_color": "#EEF3FF",
            "font_color": "#101114",
        })
        worksheet.write_row(0, 0, [str(column) for column in frame.columns], header_format)
        for row_index, row in enumerate(frame.itertuples(index=False, name=None), start=1):
            worksheet.write_row(row_index, 0, [excel_export_value(value) for value in row])
        worksheet.freeze_panes(1, 0)
        workbook.close()
        payload.seek(0)
        return payload
    except Exception:
        payload.close()
        raise


def safe_export_text(value: str) -> str:
    """Évite qu'un tableur interprète une valeur texte comme une formule."""
    return datapilot.safe_spreadsheet_text(value)


def export_label_maps(entries: list[dict]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    column_labels = {
        str(entry.get("column", "")): display_column_name(entry.get("column", ""), entries)
        for entry in entries
    }
    value_labels = {
        str(entry.get("column", "")): display_value_labels(entry)
        for entry in entries
        if display_value_labels(entry)
    }
    return column_labels, value_labels


def iter_fast_csv_export(project_id: str, entries: list[dict]):
    """Envoie le CSV par morceaux : le navigateur commence aussitôt à télécharger."""
    column_labels, value_labels = export_label_maps(entries)
    output = io.StringIO(newline="")
    writer = csv.writer(output)

    def flush() -> str:
        content = output.getvalue()
        output.seek(0)
        output.truncate(0)
        return content

    with data_path(project_id).open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        headers = next(reader, None)
        if not headers:
            raise datapilot.DataPilotError("Le fichier ne contient aucune colonne à exporter.")
        writer.writerow([safe_export_text(column_labels.get(name, name)) for name in headers])
        yield "\ufeff" + flush()
        for row in reader:
            prepared = []
            for index, value in enumerate(row):
                column = headers[index] if index < len(headers) else ""
                renamed = value_labels.get(column, {}).get(value, value)
                prepared.append(safe_export_text(renamed))
            writer.writerow(prepared)
            yield flush()


def fast_csv_download(project_id: str, entries: list[dict], download_name: str):
    response = Response(
        stream_with_context(iter_fast_csv_export(project_id, entries)),
        mimetype="text/csv",
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def make_fast_archive(project_id: str, entries: list[dict], base_name: str):
    """Prépare un ZIP compact ligne par ligne, adapté aux fichiers volumineux."""
    payload = tempfile.TemporaryFile(mode="w+b", suffix=".zip")
    source_path = data_path(project_id)
    column_labels, value_labels = export_label_maps(entries)
    csv_name = f"{base_name}_nettoye.csv"
    try:
        with zipfile.ZipFile(payload, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            with source_path.open("r", encoding="utf-8-sig", newline="") as source:
                reader = csv.reader(source)
                headers = next(reader, None)
                if not headers:
                    raise datapilot.DataPilotError("Le fichier ne contient aucune colonne à exporter.")
                with archive.open(csv_name, "w") as binary:
                    with io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as target:
                        writer = csv.writer(target)
                        writer.writerow([safe_export_text(column_labels.get(name, name)) for name in headers])
                        for row in reader:
                            prepared = []
                            for index, value in enumerate(row):
                                column = headers[index] if index < len(headers) else ""
                                renamed = value_labels.get(column, {}).get(value, value)
                                prepared.append(safe_export_text(renamed))
                            writer.writerow(prepared)
            archive.writestr(
                "README.txt",
                "Export DataPilot\n\nOuvrez le fichier CSV inclus avec Excel, LibreOffice ou Google Sheets.\n",
            )
        payload.seek(0)
        return payload
    except Exception:
        payload.close()
        raise


def download_response(payload, *, mimetype: str, download_name: str):
    """Uniformise les en-têtes de téléchargement et libère le fichier ensuite."""
    response = send_file(
        payload,
        mimetype=mimetype,
        as_attachment=True,
        download_name=download_name,
        conditional=False,
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    close = getattr(payload, "close", None)
    if callable(close):
        response.call_on_close(close)
    return response


@app.before_request
def protect_posts():
    if request.method != "POST":
        return None
    received = request.form.get("_csrf", "") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not received or not expected or not secrets.compare_digest(received, expected):
        if wants_json_response():
            return json_response({
                "ok": False,
                "error": {
                    "code": "csrf_invalid",
                    "message": "Votre session a expiré. Rechargez la page puis réessayez.",
                },
                "message": "Votre session a expiré. Rechargez la page puis réessayez.",
            }, 400)
        abort(400, "Jeton de sécurité invalide. Rechargez la page.")
    return None


@app.context_processor
def inject_helpers():
    return {"csrf_token": csrf_token, "counted_fr": counted_fr}


def project_dir(project_id: str) -> Path:
    if not PROJECT_RE.fullmatch(project_id):
        abort(404)
    root = STORAGE_ROOT.resolve()
    path = (root / project_id).resolve()
    if root not in path.parents:
        abort(404)
    return path


def metadata_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def write_json_atomic(path: Path, payload: object) -> None:
    """Évite qu’un worker et le serveur ne laissent un fichier JSON partiel."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_metadata(project_id: str) -> dict:
    path = metadata_path(project_id)
    if not path.exists():
        abort(404)
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        abort(404)
    if not isinstance(metadata, dict):
        abort(404)
    return metadata


def write_metadata(project_id: str, metadata: dict) -> None:
    write_json_atomic(metadata_path(project_id), metadata)
    invalidate_project_view(project_id)


def data_path(project_id: str, prefer_clean: bool = True) -> Path:
    directory = project_dir(project_id)
    clean = directory / "clean.csv"
    raw = directory / "raw.csv"
    if prefer_clean and clean.exists():
        return clean
    if not raw.exists():
        abort(404)
    return raw


def load_frame(project_id: str, prefer_clean: bool = True) -> pd.DataFrame:
    path = data_path(project_id, prefer_clean)
    frame = pd.read_csv(path, encoding="utf-8")
    return datapilot.restore_semantic_types(frame) if path.name == "clean.csv" else frame


def chart_specs_path(project_id: str) -> Path:
    return project_dir(project_id) / "charts.json"


def read_chart_specs(project_id: str) -> list[dict]:
    path = chart_specs_path(project_id)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def write_chart_specs(project_id: str, specs: list[dict]) -> None:
    write_json_atomic(chart_specs_path(project_id), specs[-12:])
    invalidate_project_view(project_id)


def read_project_items(project_id: str, filename: str) -> list[dict]:
    path = project_dir(project_id) / filename
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def write_project_items(project_id: str, filename: str, items: list[dict], limit: int = 20) -> None:
    write_json_atomic(project_dir(project_id) / filename, items[-limit:])
    invalidate_project_view(project_id)


def read_dictionary_history(project_id: str) -> list[dict]:
    return read_project_items(project_id, "dictionary_history.json")


def write_dictionary_history(project_id: str, items: list[dict]) -> None:
    write_project_items(project_id, "dictionary_history.json", items, limit=20)


def save_dictionary_snapshot(project_id: str, entries: list[dict], label: str) -> None:
    """Conserve l'état précédent des noms sans toucher au fichier de données."""
    snapshot = json.loads(json.dumps(entries, ensure_ascii=False))
    history = read_dictionary_history(project_id)
    history.append({
        "id": uuid.uuid4().hex[:12],
        "label": label[:120] or "Modification des noms",
        "snapshot": snapshot,
        "created_at": datapilot.utc_now(),
    })
    write_dictionary_history(project_id, history)


def dictionary_entry(entries: list[dict], column: str) -> dict | None:
    return next((item for item in entries if str(item.get("column", "")) == column), None)


def display_column_name(column: object, entries: list[dict]) -> str:
    original = str(column)
    entry = dictionary_entry(entries, original)
    if not entry:
        return original
    return str(
        entry.get("display_name")
        or entry.get("label_fr")
        or entry.get("label_ar")
        or original
    ).strip() or original


def suggested_display_name(column: object, entries: list[dict] | None = None) -> str:
    """Propose un titre français lisible sans service externe."""
    source = str(column or "").strip()
    existing = dictionary_entry(entries or [], source)
    if existing:
        current = clean_display_label(
            existing.get("display_name") or existing.get("label_fr") or existing.get("label_ar")
        )
        if current:
            return current

    normalized = re.sub(r"[_\-\s]+", "_", source.casefold()).strip("_")
    common = {
        "id_vente": "Identifiant de vente",
        "date_vente": "Date de vente",
        "canal_vente": "Canal de vente",
        "type_client": "Type de client",
        "code_client": "Code client",
        "categorie_produit": "Catégorie du produit",
        "prix_unitaire": "Prix unitaire",
        "mode_paiement": "Mode de paiement",
        "statut_commande": "Statut de la commande",
        "frais_livraison_factures": "Frais de livraison facturés",
        "cout_livraison": "Coût de livraison",
        "chiffre_affaires_net": "Chiffre d’affaires net",
        "marge_brute": "Marge brute",
        "delai_livraison": "Délai de livraison",
        "note_satisfaction": "Note de satisfaction",
    }
    unit_labels = {
        "mad": "MAD",
        "eur": "EUR",
        "usd": "USD",
        "jours": "jours",
        "jour": "jour",
        "pct": "%",
        "pourcent": "%",
    }
    parts = normalized.split("_") if normalized else []
    unit = unit_labels.get(parts[-1]) if len(parts) > 1 else None
    base_key = "_".join(parts[:-1]) if unit else normalized
    if base_key in common:
        base = common[base_key]
    else:
        acronyms = {"id": "ID", "sku": "SKU", "tva": "TVA", "ht": "HT", "ttc": "TTC"}
        words = [acronyms.get(part, part) for part in (parts[:-1] if unit else parts)]
        base = " ".join(words).strip()
        base = base[:1].upper() + base[1:] if base else source
    return f"{base} ({unit})" if unit else base


def display_value_labels(entry: dict | None) -> dict[str, str]:
    if not entry:
        return {}
    labels: dict[str, str] = {}
    for item in entry.get("value_labels", []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", ""))
        target = str(item.get("display", "")).strip()
        if source and target:
            labels[source] = target
    return labels


def language_details(language_name: str, sample: str = "") -> tuple[str, str]:
    normalized = unicodedata.normalize("NFKC", language_name).strip().casefold()
    known = {
        "français": "fr", "francais": "fr", "french": "fr", "fr": "fr",
        "العربية": "ar", "arabe": "ar", "arabe standard": "ar", "arabic": "ar", "ar": "ar",
        "darija": "ary", "darija marocaine": "ary", "arabe marocain": "ary",
        "الدارجة": "ary", "الدارجة المغربية": "ary", "ary": "ary",
        "english": "en", "anglais": "en", "en": "en",
        "español": "es", "espagnol": "es", "spanish": "es", "es": "es",
        "deutsch": "de", "allemand": "de", "german": "de", "de": "de",
        "italiano": "it", "italien": "it", "italian": "it", "it": "it",
        "português": "pt", "portugais": "pt", "portuguese": "pt", "pt": "pt",
        "hébreu": "he", "hebrew": "he", "עברית": "he", "he": "he",
        "فارسی": "fa", "persan": "fa", "persian": "fa", "fa": "fa",
        "اردو": "ur", "ourdou": "ur", "urdu": "ur", "ur": "ur",
    }
    language_code = known.get(normalized, normalized if re.fullmatch(r"[a-z]{2,3}", normalized) else "und")
    if "darija" in normalized and "latin" in normalized:
        language_code = "ary-Latn"
    rtl = language_code in {"ar", "ary", "fa", "he", "ur"} or any(
        unicodedata.bidirectional(char) in {"R", "AL", "AN"} for char in sample
    )
    return language_code, "rtl" if rtl else ("ltr" if language_code != "und" else "auto")


def translation_column_option(
    frame: pd.DataFrame,
    entries: list[dict],
    column: str,
    *,
    unique: int | None = None,
    kind: str | None = None,
) -> dict | None:
    if column not in frame.columns:
        return None
    series = frame[column]
    unique = int(series.nunique(dropna=True)) if unique is None else int(unique)
    kind = kind or datapilot.column_kind(series)
    can_translate_values = kind == "texte" and 2 <= unique <= 50
    if can_translate_values:
        reason = f"{counted_fr(unique, 'valeur distincte', 'valeurs distinctes')} peuvent être adaptées."
    elif kind != "texte":
        reason = "Cette colonne contient surtout des nombres ou des dates ; seul son titre peut être renommé."
    elif unique > 50:
        reason = "Cette colonne contient plus de 50 valeurs différentes ; seul son titre peut être renommé."
    else:
        reason = "Cette colonne n'a pas assez de catégories différentes à renommer."
    return {
        "name": column,
        "label": display_column_name(column, entries),
        "suggested_name": suggested_display_name(column, entries),
        "can_translate_values": can_translate_values,
        "reason": reason,
    }


def translatable_columns(
    frame: pd.DataFrame,
    entries: list[dict],
    profile: dict | None = None,
) -> list[dict]:
    details = list((profile or {}).get("columns", []))
    options: list[dict] = []
    for index, column in enumerate(frame.columns):
        detail = details[index] if index < len(details) else {}
        option = translation_column_option(
            frame,
            entries,
            str(column),
            unique=detail.get("unique"),
            kind=detail.get("kind"),
        )
        if option:
            options.append(option)
    return options


def category_rows(frame: pd.DataFrame, column: str, entry: dict | None = None) -> list[dict]:
    if column not in frame.columns:
        return []
    existing = display_value_labels(entry)
    counts = frame[column].dropna().value_counts().head(50)
    return [
        {
            "source": str(value),
            "display": existing.get(str(value), str(value)),
            "count": int(count),
        }
        for value, count in counts.items()
    ]


def clean_display_label(value: object, limit: int = 100) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()[:limit]


def make_translation_draft(
    frame: pd.DataFrame,
    entries: list[dict],
    *,
    column: str,
    display_name: str,
    language_name: str,
    include_values: bool,
    submitted_labels: dict[str, str] | None = None,
) -> dict:
    option = translation_column_option(frame, entries, column)
    if not option:
        raise datapilot.DataPilotError("Choisissez une colonne existante.")
    entry = dictionary_entry(entries, column)
    include_values = bool(include_values and option["can_translate_values"])
    rows = category_rows(frame, column, entry) if include_values else []
    if submitted_labels is not None:
        for row in rows:
            if row["source"] in submitted_labels:
                row["display"] = clean_display_label(submitted_labels[row["source"]], 160) or row["source"]
    shown_name = clean_display_label(display_name) or display_column_name(column, entries)
    language = clean_display_label(language_name, 60) or "Langue non précisée"
    language_code, direction = language_details(language, " ".join([shown_name, *[row["display"] for row in rows]]))
    displayed_values = [row["display"].casefold() for row in rows]
    has_collision = len(displayed_values) != len(set(displayed_values))
    return {
        "column": column,
        "current_name": column,
        "display_name": shown_name,
        "language_name": language,
        "language_code": language_code,
        "direction": direction,
        "include_values": include_values,
        "can_translate_values": option["can_translate_values"],
        "reason": option["reason"],
        "values": rows,
        "affected_rows": int(sum(row["count"] for row in rows)),
        "has_collision": has_collision,
    }


def build_display_frame(frame: pd.DataFrame, entries: list[dict]) -> pd.DataFrame:
    """Crée une vue traduite ; le DataFrame source reste strictement inchangé."""
    if not entries:
        return frame
    displayed = frame.copy(deep=False)
    changed_values = False
    for entry in entries:
        column = str(entry.get("column", ""))
        replacements = display_value_labels(entry)
        if column not in frame.columns or not replacements:
            continue
        if not changed_values:
            displayed = frame.copy(deep=False)
            changed_values = True
        displayed[column] = frame[column].map(
            lambda value, labels=replacements: labels.get(str(value), value) if pd.notna(value) else value
        )
    renames = {
        str(column): display_column_name(column, entries)
        for column in frame.columns
        if display_column_name(column, entries) != str(column)
    }
    if renames:
        displayed = displayed.rename(columns=renames, copy=False)
    return displayed


def localize_chart_options(options: dict, entries: list[dict]) -> dict:
    localized = {"dimensions": [], "measures": []}
    kind_labels = {"date": "date", "number": "nombre", "category": "catégorie"}
    for group in localized:
        for option in options.get(group, []):
            item = dict(option)
            shown = display_column_name(item.get("name", ""), entries)
            item["label"] = (
                f"{shown} · {kind_labels.get(item.get('kind', ''), item.get('kind', ''))}"
                if group == "dimensions"
                else shown
            )
            localized[group].append(item)
    return localized


def localize_answer_payload(answer: dict, entries: list[dict]) -> dict:
    localized = dict(answer)
    replacements = sorted(
        (
            (str(entry.get("column", "")), display_column_name(entry.get("column", ""), entries))
            for entry in entries
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def replace_text(value: object) -> str:
        text = str(value)
        for source, target in replacements:
            if source and source != target:
                text = re.sub(rf"(?<!\w){re.escape(source)}(?!\w)", target, text)
        return text

    localized["answer"] = replace_text(localized.get("answer", ""))
    if "chart_message" in localized:
        localized["chart_message"] = replace_text(localized.get("chart_message", ""))
    for key in ("insights", "cautions", "evidence", "suggested_questions"):
        localized[key] = [replace_text(item) for item in localized.get(key, [])]
    return localized


def recipes_path() -> Path:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return STORAGE_ROOT / "cleaning_recipes.json"


def read_recipes() -> list[dict]:
    path = recipes_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def write_recipes(items: list[dict]) -> None:
    recipes_path().write_text(
        json.dumps(items[-20:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def explain_chart_values(chart: dict) -> dict | None:
    """Explique le point le plus élevé sans lui attribuer une cause inventée."""
    labels = [str(item) for item in chart.get("labels", [])]
    raw_values = chart.get("values", [])
    if len(labels) < 2 or len(raw_values) != len(labels):
        return None
    try:
        values = [float(item) for item in raw_values]
    except (TypeError, ValueError):
        return None
    peak_index = max(range(len(values)), key=values.__getitem__)
    peak_value = values[peak_index]
    average = sum(values) / len(values)
    points = [f"Le point le plus élevé est « {labels[peak_index]} » avec {peak_value:,.2f}."]
    if average:
        difference = (peak_value - average) / abs(average) * 100
        points.append(f"Il se situe à {abs(difference):.1f} % {'au-dessus' if difference >= 0 else 'en dessous'} de la moyenne affichée.")
    if chart.get("type") == "line" and peak_index > 0:
        previous = values[peak_index - 1]
        delta = peak_value - previous
        points.append(f"Par rapport au point précédent, la différence est de {delta:+,.2f}.")
    elif sum(value for value in values if value > 0) > 0:
        positive_total = sum(value for value in values if value > 0)
        points.append(f"Ce point représente {peak_value / positive_total * 100:.1f} % du total positif affiché.")
    return {
        "title": "Comprendre le point le plus élevé",
        "points": points,
        "caution": "Ce calcul montre une différence dans le fichier ; il ne prouve pas sa cause.",
    }


def recent_projects() -> list[dict]:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    items = []
    for path in STORAGE_ROOT.glob("*/project.json"):
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(metadata, dict):
                continue
            if not (path.parent / "raw.csv").exists():
                continue
            if metadata.get("processing_status") not in {None, "ready"}:
                continue
            metadata["id"] = path.parent.name
            try:
                timestamp = datetime.fromisoformat(
                    str(metadata.get("updated_at", "")).replace("Z", "+00:00")
                )
                metadata["date_label"] = timestamp.strftime("%d/%m/%Y · %H:%M")
            except (TypeError, ValueError):
                metadata["date_label"] = "Date inconnue"
            items.append(metadata)
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return sorted(items, key=lambda item: item.get("updated_at", ""), reverse=True)[:8]


def apply_cleaning_batch(project_id: str, action_ids: list[str], label: str) -> int:
    """Applique un lot réversible et garde l'état précédent sur l'ordinateur."""
    metadata = read_metadata(project_id)
    directory = project_dir(project_id)
    clean_path = directory / "clean.csv"
    source_was_clean = clean_path.exists()
    current = load_frame(project_id)
    cleaned, log = datapilot.apply_cleaning(current, action_ids)
    if not log:
        return 0

    batch_id = uuid.uuid4().hex[:12]
    versions = directory / "versions"
    versions.mkdir(exist_ok=True)
    snapshot_name = f"{batch_id}.csv"
    current.to_csv(versions / snapshot_name, index=False, encoding="utf-8")
    cleaned.to_csv(clean_path, index=False, encoding="utf-8")

    batches = list(metadata.get("cleaning_batches", []))
    batches.append({
        "id": batch_id,
        "label": label[:80] or "Corrections validées",
        "actions": [item["action"] for item in log],
        "log": log,
        "snapshot": snapshot_name,
        "source_was_clean": source_was_clean,
        "applied_at": datapilot.utc_now(),
    })
    transformations = list(metadata.get("transformations", [])) + log
    metadata.update({
        "updated_at": datapilot.utc_now(),
        "cleaned": True,
        "transformations": transformations,
        "cleaning_batches": batches[-20:],
        "quality_after": datapilot.profile_dataframe(cleaned)["quality_score"],
    })
    write_metadata(project_id, metadata)
    return len(log)


def dictionary_aliases(entries: list[dict]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for entry in entries:
        column = str(entry.get("column", "")).strip()
        terms = [
            str(entry.get("display_name", "")).strip(),
            str(entry.get("label_fr", "")).strip(),
            str(entry.get("label_ar", "")).strip(),
            *[
                item.strip()
                for item in str(entry.get("synonyms", "")).replace(";", ",").split(",")
            ],
        ]
        if column:
            aliases[column] = [term for term in terms if term]
    return aliases


def apply_business_words(question: str, entries: list[dict]) -> str:
    """Remplace seulement les mots enregistrés par leur vraie colonne interne."""
    resolved = question
    terms: list[tuple[str, str]] = []
    for column, aliases in dictionary_aliases(entries).items():
        terms.extend((alias, column) for alias in aliases)
    for alias, column in sorted(terms, key=lambda item: len(item[0]), reverse=True):
        resolved = re.sub(
            rf"(?<!\w){re.escape(alias)}(?!\w)",
            lambda _match, replacement=column: replacement,
            resolved,
            flags=re.IGNORECASE,
        )
    value_terms: list[tuple[str, str]] = []
    for entry in entries:
        for item in entry.get("value_labels", []):
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).strip()
            displayed = str(item.get("display", "")).strip()
            if source and displayed and source != displayed:
                value_terms.append((displayed, source))
    for displayed, source in sorted(value_terms, key=lambda item: len(item[0]), reverse=True):
        resolved = re.sub(
            rf"(?<!\w){re.escape(displayed)}(?!\w)",
            lambda _match, replacement=source: replacement,
            resolved,
            flags=re.IGNORECASE,
        )
    return resolved


def calculate_saved_kpis(
    frame: pd.DataFrame,
    specs: list[dict],
    entries: list[dict] | None = None,
) -> list[dict]:
    calculated: list[dict] = []
    entries = entries or []
    for spec in specs:
        original_measure = spec.get("measure") or None
        measure = display_column_name(original_measure, entries) if original_measure else None
        result = advanced_features.calculate_kpi(
            frame,
            measure=measure,
            aggregation=str(spec.get("aggregation", "sum")),
            target=spec.get("target"),
            favorable_direction=str(spec.get("direction", "higher")),
        )
        if not result.get("ok"):
            continue
        item = dict(result["kpi"])
        item.update({
            "id": spec.get("id", ""),
            "name": spec.get("name") or item["measure_label"],
            "message": result["message"],
        })
        progress = item.get("achievement_percent")
        item["progress"] = max(0, min(float(progress), 100)) if progress is not None else None
        calculated.append(item)
    return calculated


def parse_optional_number(value: str) -> float | None:
    text = str(value or "").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    return float(text)


def build_project_custom_charts(
    project_id: str,
    frame: pd.DataFrame,
    dictionary_entries: list[dict],
) -> list[dict]:
    """Construit uniquement les graphiques que la personne a enregistrés."""
    display_frame = build_display_frame(frame, dictionary_entries)
    custom_charts: list[dict] = []
    for spec in read_chart_specs(project_id):
        try:
            chart = datapilot.build_custom_chart(
                display_frame,
                dimension=display_column_name(spec.get("dimension", ""), dictionary_entries),
                measure=display_column_name(spec.get("measure", ""), dictionary_entries),
                aggregation=str(spec.get("aggregation", "count")),
                chart_type=str(spec.get("chart_type", "auto")),
            )
        except datapilot.DataPilotError:
            continue
        chart.update({"id": spec.get("id", ""), "created_at": spec.get("created_at", "")})
        chart["explanation"] = explain_chart_values(chart)
        custom_charts.append(chart)
    return custom_charts


def render_chart_studio_fragment(
    project_id: str,
    *,
    frame: pd.DataFrame | None = None,
    dictionary_entries: list[dict] | None = None,
    notice: dict | None = None,
) -> str:
    frame = frame if frame is not None else load_frame(project_id)
    dictionary_entries = (
        dictionary_entries
        if dictionary_entries is not None
        else read_project_items(project_id, "dictionary.json")
    )
    return render_template(
        "_chart_studio.html",
        project_id=project_id,
        chart_options=localize_chart_options(
            datapilot.chart_builder_options(frame), dictionary_entries
        ),
        custom_charts=build_project_custom_charts(project_id, frame, dictionary_entries),
        chart_notice=notice,
    )


def chart_fragment_response(
    project_id: str,
    *,
    frame: pd.DataFrame,
    dictionary_entries: list[dict],
    message: str,
    level: str = "info",
    ok: bool = True,
    status: int = 200,
):
    return json_response({
        "ok": ok,
        "message": message,
        "fragment": {
            "selector": "#chart-studio",
            "html": render_chart_studio_fragment(
                project_id,
                frame=frame,
                dictionary_entries=dictionary_entries,
                notice={"text": message, "level": level},
            ),
            "focus": "#chart-studio",
        },
        "canonical_url": url_for("project", project_id=project_id, section="charts"),
    }, status)


def project_view_signature(project_id: str) -> tuple:
    """Versionne les éléments qui influencent la vue sans ralentir la page."""
    directory = project_dir(project_id)
    return (
        file_signature(data_path(project_id)),
        file_signature(directory / "dictionary.json"),
        file_signature(directory / "charts.json"),
    )


def build_project_view(project_id: str) -> dict:
    """Prépare la partie coûteuse de l'écran projet, réutilisable telle quelle."""
    cache_key = (project_id, project_view_signature(project_id))
    with _project_view_cache_lock:
        cached = _project_view_cache.get(cache_key)
        if cached is not None:
            _project_view_cache.move_to_end(cache_key)
            return cached

    frame = load_frame(project_id)
    dictionary_entries = read_project_items(project_id, "dictionary.json")
    display_frame = build_display_frame(frame, dictionary_entries)
    profile = datapilot.profile_dataframe(display_frame)
    dashboard = datapilot.build_dashboard(display_frame, profile=profile)
    for chart in dashboard.get("charts", []):
        chart["explanation"] = explain_chart_values(chart)
    semantic = datapilot.semantic_manifest(frame)
    semantic["business_dictionary"] = dictionary_entries
    (project_dir(project_id) / "semantic.json").write_text(
        json.dumps(semantic, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    view = {
        "frame": frame,
        "display_frame": display_frame,
        "dictionary_entries": dictionary_entries,
        "profile": profile,
        "suggestions": datapilot.suggest_cleaning(frame),
        "dashboard": dashboard,
        "chart_options": localize_chart_options(datapilot.chart_builder_options(frame), dictionary_entries),
        "custom_charts": build_project_custom_charts(project_id, frame, dictionary_entries),
    }
    with _project_view_cache_lock:
        _project_view_cache[cache_key] = view
        _project_view_cache.move_to_end(cache_key)
        while len(_project_view_cache) > PROJECT_VIEW_CACHE_LIMIT:
            _project_view_cache.popitem(last=False)
    return view


def render_project(
    project_id: str,
    answer: dict | None = None,
    translation_draft: dict | None = None,
    active_section: str = "overview",
):
    if active_section not in PROJECT_SECTIONS:
        abort(404)
    metadata = read_metadata(project_id)
    view = build_project_view(project_id)
    frame = view["frame"]
    display_frame = view["display_frame"]
    dictionary_entries = view["dictionary_entries"]
    return render_template(
        "project.html",
        project_id=project_id,
        metadata=metadata,
        frame=frame,
        profile=view["profile"],
        suggestions=view["suggestions"],
        preview=datapilot.dataframe_preview(display_frame),
        display_columns=[str(column) for column in display_frame.columns],
        dashboard=view["dashboard"],
        chart_options=view["chart_options"],
        custom_charts=view["custom_charts"],
        saved_kpis=calculate_saved_kpis(
            display_frame,
            read_project_items(project_id, "kpis.json"),
            dictionary_entries,
        ),
        dictionary_entries=dictionary_entries,
        dictionary_history=read_dictionary_history(project_id),
        translation_columns=translatable_columns(frame, dictionary_entries, view["profile"]),
        translation_draft=translation_draft,
        invoice_rows=datapilot.dataframe_preview(frame, limit=20) if metadata.get("source_kind") == "invoices" else [],
        recipes=read_recipes(),
        answer=localize_answer_payload(answer, dictionary_entries) if answer else None,
        groq=groq_analysis.status(),
        active_section=active_section,
        project_sections=PROJECT_SECTIONS,
    )


PROJECT_SECTIONS = {
    "overview": {
        "number": "1",
        "label": "Comprendre",
        "description": "Voir les chiffres importants",
        "guidance": "Regardez les quatre chiffres ci-dessous. Ils résument la qualité et la taille de votre fichier.",
        "next": "cleaning",
        "next_label": "Vérifier les corrections",
    },
    "cleaning": {
        "number": "2",
        "label": "Corriger",
        "description": "Réparer les erreurs proposées",
        "guidance": "DataPilot prépare les corrections. Vous choisissez celles que vous voulez appliquer.",
        "next": "names",
        "next_label": "Simplifier les noms",
    },
    "names": {
        "number": "3",
        "label": "Clarifier",
        "description": "Changer les noms compliqués",
        "guidance": "Choisissez un nom difficile à lire. DataPilot vous propose aussitôt une version plus simple.",
        "next": "charts",
        "next_label": "Créer un graphique",
    },
    "charts": {
        "number": "4",
        "label": "Visualiser",
        "description": "Voir les données en images",
        "guidance": "Choisissez ce que vous voulez comparer. DataPilot prépare le calcul et le graphique.",
        "next": None,
        "next_label": "Télécharger le résultat",
    },
}


DICTIONARY_DEPENDENT_TARGETS = [
    "#overview",
    "#automatic-dashboard",
    "#chart-studio",
]


def dictionary_form_state() -> dict:
    """Conserve les choix saisis lorsqu'une validation asynchrone échoue."""
    return {
        "title_mode": request.form.get("title_mode", "manual").strip(),
        "column": request.form.get("column", "").strip(),
        "display_name": request.form.get("display_name", ""),
        "language_name": request.form.get("language_name", ""),
        "include_values": request.form.get("include_values") == "1",
        "suggestion_consent": request.form.get("suggestion_consent") == "1",
    }


def dictionary_fragment_response(
    project_id: str,
    *,
    frame: pd.DataFrame,
    entries: list[dict],
    message: str,
    level: str = "info",
    ok: bool = True,
    status: int = 200,
    changed: bool = False,
    translation_draft: dict | None = None,
    translation_form: dict | None = None,
    focus: str = "#business-words-title",
    error_code: str | None = None,
    refresh_views: bool = False,
):
    """Répond avec le seul bloc de renommage, prêt à être remplacé dans le DOM."""
    html = render_template(
        "_business_words.html",
        project_id=project_id,
        translation_columns=translatable_columns(frame, entries),
        translation_draft=translation_draft,
        translation_form=translation_form or {},
        translation_notice={"text": message, "level": level},
        dictionary_entries=entries,
        dictionary_history=read_dictionary_history(project_id),
    )
    payload = {
        "ok": ok,
        "changed": changed,
        "message": message,
        "level": level,
        "fragment": {
            "selector": "#business-words",
            "html": html,
            "focus": focus,
        },
        "canonical_url": url_for("project", project_id=project_id, section="names"),
    }
    if error_code:
        payload["error"] = {"code": error_code, "message": message}
    if refresh_views:
        payload.update({
            "refresh_url": url_for("project", project_id=project_id, section="overview"),
            "refresh_targets": DICTIONARY_DEPENDENT_TARGETS,
        })
    return json_response(payload, status)


@app.route("/")
def index():
    return render_template("index.html", projects=recent_projects())


@app.route("/demo", methods=["POST"])
def demo():
    """Crée un espace de démonstration à partir du fichier PME fourni."""
    source = BASE_DIR / "demo" / "ventes_pme.csv"
    if not source.exists():
        flash("Le fichier de démonstration est indisponible.", "warn")
        return redirect(url_for("index"))

    project_id = uuid.uuid4().hex
    directory = project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=False)
    try:
        original_path = directory / "source.csv"
        shutil.copy2(source, original_path)
        frame = datapilot.read_dataset(original_path)
        frame.to_csv(directory / "raw.csv", index=False, encoding="utf-8")
        profile = datapilot.profile_dataframe(frame)
        metadata = {
            "name": "Démonstration ventes PME",
            "original_filename": "ventes_pme.csv",
            "extension": ".csv",
            "created_at": datapilot.utc_now(),
            "updated_at": datapilot.utc_now(),
            "cleaned": False,
            "quality_before": profile["quality_score"],
            "transformations": [],
            "privacy": "Fichier brut traité localement",
        }
        write_metadata(project_id, metadata)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        flash("La démonstration n’a pas pu être préparée.", "warn")
        return redirect(url_for("index"))

    flash("Démonstration prête : explorez les résultats ci-dessous.", "ok")
    return redirect(url_for("project", project_id=project_id))


def process_project_import(project_id: str, safe_name: str, extension: str) -> dict:
    """Analyse l’import enregistré ; peut être exécuté localement ou par Celery."""
    directory = project_dir(project_id)
    metadata = read_metadata(project_id)
    if metadata.get("processing_status") == "ready" and (directory / "raw.csv").exists():
        return {"ok": True, "project_id": project_id, "already_ready": True}
    metadata.update({
        "processing_status": "processing",
        "processing_progress": 15,
        "processing_message": "Lecture et vérification du fichier…",
        "updated_at": datapilot.utc_now(),
    })
    write_metadata(project_id, metadata)
    try:
        if extension not in datapilot.ALLOWED_EXTENSIONS:
            raise datapilot.DataPilotError("Format d’import non autorisé.")
        if not safe_name or secure_filename(safe_name) != safe_name:
            raise datapilot.DataPilotError("Nom de fichier d’import invalide.")
        original_path = directory / f"source{extension}"
        if not original_path.exists():
            raise datapilot.DataPilotError("Le fichier source de cet import est introuvable.")
        frame = datapilot.read_dataset(original_path)
        metadata.update({
            "processing_progress": 65,
            "processing_message": "Préparation de l’analyse…",
        })
        write_metadata(project_id, metadata)
        temporary_raw = directory / f".raw.{uuid.uuid4().hex}.tmp"
        try:
            frame.to_csv(temporary_raw, index=False, encoding="utf-8")
            os.replace(temporary_raw, directory / "raw.csv")
        finally:
            temporary_raw.unlink(missing_ok=True)
        profile = datapilot.profile_dataframe(frame)
    except Exception as exc:
        message = (
            str(exc)
            if isinstance(exc, datapilot.DataPilotError)
            else "Le fichier n’a pas pu être traité."
        )
        metadata.update({
            "processing_status": "failed",
            "processing_progress": 100,
            "processing_message": message,
            "processing_error": message,
            "updated_at": datapilot.utc_now(),
        })
        write_metadata(project_id, metadata)
        raise

    metadata.update({
        "name": Path(safe_name).stem or "Fichier",
        "original_filename": safe_name,
        "extension": extension,
        "updated_at": datapilot.utc_now(),
        "cleaned": False,
        "quality_before": profile["quality_score"],
        "transformations": [],
        "privacy": "Fichier brut traité localement",
        "processing_status": "ready",
        "processing_progress": 100,
        "processing_message": "Analyse prête.",
    })
    metadata.pop("processing_error", None)
    write_metadata(project_id, metadata)
    return {"ok": True, "project_id": project_id, "rows": int(len(frame))}


@app.route("/upload", methods=["POST"])
def upload():
    uploaded = request.files.get("dataset")
    if not uploaded or not uploaded.filename:
        flash("Choisissez un fichier CSV, Excel ou JSON.", "warn")
        return redirect(url_for("index"))
    safe_name = secure_filename(uploaded.filename)
    extension = Path(safe_name).suffix.lower()
    if extension not in datapilot.ALLOWED_EXTENSIONS:
        flash("Format refusé. Utilisez CSV, XLSX, XLS ou JSON.", "warn")
        return redirect(url_for("index"))

    project_id = uuid.uuid4().hex
    directory = project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=False)
    original_path = directory / f"source{extension}"
    try:
        uploaded.save(original_path)
        metadata = {
            "name": Path(safe_name).stem or "Fichier",
            "original_filename": safe_name,
            "extension": extension,
            "created_at": datapilot.utc_now(),
            "updated_at": datapilot.utc_now(),
            "cleaned": False,
            "transformations": [],
            "privacy": "Fichier brut traité localement",
            "processing_status": "queued",
            "processing_progress": 5,
            "processing_message": "Analyse placée dans la file d’attente…",
            "task_id": uuid.uuid4().hex,
        }
        write_metadata(project_id, metadata)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        message = str(exc) if isinstance(exc, datapilot.DataPilotError) else "Le fichier n’a pas pu être traité."
        flash(message, "warn")
        return redirect(url_for("index"))

    queued = False
    if not app.config.get("TESTING") and os.environ.get("DATAPILOT_TASKS_EAGER") != "1":
        queued, _queue_state = task_queue.enqueue_project_import(
            project_id,
            safe_name,
            extension,
            task_id=metadata["task_id"],
        )
    if queued:
        flash("Fichier reçu. L’analyse continue en arrière-plan.", "ok")
        return redirect(url_for("project_processing", project_id=project_id))

    try:
        process_project_import(project_id, safe_name, extension)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        message = str(exc) if isinstance(exc, datapilot.DataPilotError) else "Le fichier n’a pas pu être traité."
        flash(message, "warn")
        return redirect(url_for("index"))

    flash("Fichier importé et analysé.", "ok")
    return redirect(url_for("project", project_id=project_id))


@app.route("/invoice", methods=["POST"])
def upload_invoices():
    uploads = [item for item in request.files.getlist("invoices") if item and item.filename]
    if not uploads:
        flash("Choisissez au moins une facture PDF ou une photo.", "warn")
        return redirect(url_for("index") + "#invoice-import")
    if len(uploads) > 10:
        flash("Sélectionnez au maximum 10 factures à la fois.", "warn")
        return redirect(url_for("index") + "#invoice-import")

    accepted = []
    for uploaded in uploads:
        safe_name = secure_filename(uploaded.filename)
        if Path(safe_name).suffix.lower() in invoice_features.SUPPORTED_EXTENSIONS:
            accepted.append((uploaded, safe_name))
    if not accepted:
        flash("Formats acceptés : PDF, PNG, JPG, TIFF ou WEBP.", "warn")
        return redirect(url_for("index") + "#invoice-import")

    project_id = uuid.uuid4().hex
    directory = project_dir(project_id)
    documents = directory / "documents"
    documents.mkdir(parents=True, exist_ok=False)
    rows: list[dict] = []
    reviews: list[dict] = []
    try:
        for uploaded, safe_name in accepted:
            target = documents / f"{uuid.uuid4().hex[:8]}-{safe_name}"
            uploaded.save(target)
            review = invoice_features.process_invoice(target)
            review["display_name"] = safe_name
            reviews.append(review)
            fields = review.get("fields", {})
            row = {
                column: fields.get(field, {}).get("value")
                for field, column in INVOICE_FIELD_COLUMNS.items()
            }
            row.update({
                "taux_tva": fields.get("vat_rate", {}).get("value"),
                "fichier_source": safe_name,
                "etat": "À vérifier" if review.get("review", {}).get("required") else "À compléter",
            })
            rows.append(row)

        frame = pd.DataFrame(rows)
        frame.to_csv(directory / "raw.csv", index=False, encoding="utf-8")
        profile = datapilot.profile_dataframe(frame)
        names = [safe_name for _uploaded, safe_name in accepted]
        metadata = {
            "name": "Factures importées",
            "original_filename": ", ".join(names[:3]) + ("…" if len(names) > 3 else ""),
            "extension": "documents",
            "source_kind": "invoices",
            "created_at": datapilot.utc_now(),
            "updated_at": datapilot.utc_now(),
            "cleaned": False,
            "quality_before": profile["quality_score"],
            "transformations": [],
            "invoice_reviews": reviews,
        }
        write_metadata(project_id, metadata)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        flash("Ces factures n’ont pas pu être préparées. Essayez un autre document.", "warn")
        return redirect(url_for("index") + "#invoice-import")

    flash(
        f"{counted_fr(len(rows), 'facture préparée', 'factures préparées')}. "
        "Vérifiez maintenant les informations détectées.",
        "ok",
    )
    return redirect(url_for("project", project_id=project_id) + "#invoice-review")


@app.route("/project/<project_id>/invoice/confirm", methods=["POST"])
def confirm_invoices(project_id: str):
    metadata = read_metadata(project_id)
    if metadata.get("source_kind") != "invoices":
        abort(404)
    frame = load_frame(project_id, prefer_clean=False).copy()
    text_columns = {"fournisseur", "numero_facture", "date_facture", "devise"}
    number_columns = {"total_ht", "tva", "taux_tva", "total_ttc"}
    for row_index in range(len(frame)):
        for column in text_columns:
            frame.at[row_index, column] = request.form.get(f"{column}_{row_index}", "").strip()[:160] or pd.NA
        for column in number_columns:
            try:
                value = parse_optional_number(request.form.get(f"{column}_{row_index}", ""))
            except ValueError:
                flash(f"Vérifiez le nombre saisi pour « {column.replace('_', ' ')} ».", "warn")
                return redirect(url_for("project", project_id=project_id) + "#invoice-review")
            frame.at[row_index, column] = value if value is not None else pd.NA
        frame.at[row_index, "etat"] = "Confirmé par vous"
    frame.to_csv(project_dir(project_id) / "clean.csv", index=False, encoding="utf-8")
    metadata.update({
        "cleaned": True,
        "invoice_confirmed_at": datapilot.utc_now(),
        "updated_at": datapilot.utc_now(),
        "quality_after": datapilot.profile_dataframe(frame)["quality_score"],
    })
    write_metadata(project_id, metadata)
    flash("Les factures sont confirmées et prêtes pour les graphiques ou l’export.", "ok")
    return redirect(url_for("project", project_id=project_id) + "#overview")


@app.route("/project/<project_id>", defaults={"section": "overview"})
@app.route("/project/<project_id>/<section>")
def project(project_id: str, section: str):
    metadata = read_metadata(project_id)
    if metadata.get("processing_status") in {"queued", "processing", "failed"}:
        return redirect(url_for("project_processing", project_id=project_id))
    if section not in PROJECT_SECTIONS:
        abort(404)
    return render_project(project_id, active_section=section)


@app.route("/project/<project_id>/processing")
def project_processing(project_id: str):
    metadata = read_metadata(project_id)
    if metadata.get("processing_status") in {None, "ready"}:
        return redirect(url_for("project", project_id=project_id))
    return render_template(
        "processing.html",
        project_id=project_id,
        metadata=metadata,
    )


@app.route("/project/<project_id>/status")
def project_status(project_id: str):
    metadata = read_metadata(project_id)
    status = str(metadata.get("processing_status") or "ready")
    response = json_response({
        "ok": status != "failed",
        "project_id": project_id,
        "status": status,
        "progress": int(metadata.get("processing_progress", 100 if status == "ready" else 0)),
        "message": str(metadata.get("processing_message") or "Analyse prête."),
        "redirect_url": url_for("project", project_id=project_id) if status == "ready" else None,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/project/<project_id>/processing/run-local", methods=["POST"])
def run_project_import_locally(project_id: str):
    metadata = read_metadata(project_id)
    extension = str(metadata.get("extension", "")).lower()
    safe_name = secure_filename(str(metadata.get("original_filename", "")))
    if extension not in datapilot.ALLOWED_EXTENSIONS or not safe_name:
        abort(404)
    try:
        process_project_import(project_id, safe_name, extension)
    except Exception:
        flash("L’analyse locale n’a pas pu aboutir. Vérifiez le fichier puis réessayez.", "warn")
        return redirect(url_for("project_processing", project_id=project_id))
    flash("Analyse terminée sur cet ordinateur.", "ok")
    return redirect(url_for("project", project_id=project_id))


@app.route("/health")
def health():
    queue = task_queue.queue_health()
    response = json_response({
        "ok": True,
        "application": "ready",
        "queue": queue,
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/project/<project_id>/clean", methods=["POST"])
def clean(project_id: str):
    read_metadata(project_id)
    selected = request.form.getlist("actions")
    if not selected:
        flash("Sélectionnez au moins une correction à appliquer.", "warn")
        return redirect(url_for("project", project_id=project_id, section="cleaning"))
    applied = apply_cleaning_batch(project_id, selected, "Corrections choisies")
    if not applied:
        flash("Ces corrections ne sont plus nécessaires pour ce fichier.", "info")
        return redirect(url_for("project", project_id=project_id, section="cleaning"))
    flash(
        f"{counted_fr(applied, 'correction appliquée', 'corrections appliquées')}. "
        "Vous pouvez annuler cette étape.",
        "ok",
    )
    return redirect(url_for("project", project_id=project_id, section="cleaning"))


@app.route("/project/<project_id>/reset", methods=["POST"])
def reset(project_id: str):
    metadata = read_metadata(project_id)
    clean_path = project_dir(project_id) / "clean.csv"
    if clean_path.exists():
        clean_path.unlink()
    versions = project_dir(project_id) / "versions"
    if versions.exists():
        shutil.rmtree(versions)
    metadata.update({
        "updated_at": datapilot.utc_now(),
        "cleaned": False,
        "transformations": [],
        "cleaning_batches": [],
    })
    metadata.pop("quality_after", None)
    write_metadata(project_id, metadata)
    flash("Retour au fichier d’origine.", "info")
    return redirect(url_for("project", project_id=project_id, section="cleaning"))


@app.route("/project/<project_id>/clean/undo", methods=["POST"])
def undo_cleaning(project_id: str):
    metadata = read_metadata(project_id)
    batches = list(metadata.get("cleaning_batches", []))
    if not batches:
        flash("Aucune étape récente ne peut être annulée.", "info")
        return redirect(url_for("project", project_id=project_id, section="cleaning"))

    batch = batches.pop()
    snapshot = project_dir(project_id) / "versions" / str(batch.get("snapshot", ""))
    clean_path = project_dir(project_id) / "clean.csv"
    if not snapshot.exists():
        flash("La copie nécessaire à cette annulation est indisponible.", "warn")
        return redirect(url_for("project", project_id=project_id, section="cleaning"))
    if batch.get("source_was_clean"):
        shutil.copy2(snapshot, clean_path)
    elif clean_path.exists():
        clean_path.unlink()
    snapshot.unlink(missing_ok=True)

    removed = len(batch.get("log", []))
    transformations = list(metadata.get("transformations", []))
    metadata["transformations"] = transformations[:-removed] if removed else transformations
    metadata["cleaning_batches"] = batches
    metadata["cleaned"] = clean_path.exists()
    metadata["updated_at"] = datapilot.utc_now()
    if clean_path.exists():
        metadata["quality_after"] = datapilot.profile_dataframe(load_frame(project_id))["quality_score"]
    else:
        metadata.pop("quality_after", None)
    write_metadata(project_id, metadata)
    flash("La dernière étape de correction a été annulée.", "info")
    return redirect(url_for("project", project_id=project_id, section="cleaning"))


@app.route("/project/<project_id>/recipes", methods=["POST"])
def save_recipe(project_id: str):
    metadata = read_metadata(project_id)
    batches = list(metadata.get("cleaning_batches", []))
    if not batches:
        flash("Appliquez d’abord une correction avant de créer un modèle.", "info")
        return redirect(url_for("project", project_id=project_id, section="cleaning"))
    name = request.form.get("name", "").strip()[:80] or "Mon modèle de correction"
    recipes = read_recipes()
    recipes.append({
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "actions": list(batches[-1].get("actions", [])),
        "created_at": datapilot.utc_now(),
    })
    write_recipes(recipes)
    flash("Votre modèle de correction est prêt pour les prochains fichiers.", "ok")
    return redirect(url_for("project", project_id=project_id, section="cleaning"))


@app.route("/project/<project_id>/recipes/<recipe_id>/apply", methods=["POST"])
def apply_recipe(project_id: str, recipe_id: str):
    read_metadata(project_id)
    if not CHART_ID_RE.fullmatch(recipe_id):
        abort(404)
    recipe = next((item for item in read_recipes() if item.get("id") == recipe_id), None)
    if not recipe:
        abort(404)
    applied = apply_cleaning_batch(
        project_id,
        [str(item) for item in recipe.get("actions", [])],
        f"Modèle : {recipe.get('name', 'Corrections')}",
    )
    if applied:
        flash(
            f"Le modèle a appliqué "
            f"{counted_fr(applied, 'correction', 'corrections')}.",
            "ok",
        )
    else:
        flash("Ce modèle ne contient aucune correction utile pour ce fichier.", "info")
    return redirect(url_for("project", project_id=project_id, section="cleaning"))


@app.route("/project/<project_id>/kpis", methods=["POST"])
def create_kpi(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    name = request.form.get("name", "").strip()[:80]
    measure = request.form.get("measure", "").strip() or None
    aggregation = request.form.get("aggregation", "sum").strip()
    direction = request.form.get("direction", "higher").strip()
    try:
        target = parse_optional_number(request.form.get("target", ""))
    except ValueError:
        flash("L’objectif doit être un nombre, par exemple 1000 ou 1000,50.", "warn")
        return redirect(url_for("project", project_id=project_id) + "#tracked-numbers")
    result = advanced_features.calculate_kpi(
        frame,
        measure=measure,
        aggregation=aggregation,
        target=target,
        favorable_direction=direction,
    )
    if not result.get("ok"):
        flash(str(result.get("message", "Ce chiffre ne peut pas être calculé.")), "warn")
        return redirect(url_for("project", project_id=project_id) + "#tracked-numbers")
    kpi = result["kpi"]
    default_name = f"{kpi['aggregation_label']} · {kpi['measure_label']}"
    specs = read_project_items(project_id, "kpis.json")
    specs.append({
        "id": uuid.uuid4().hex[:12],
        "name": name or default_name,
        "measure": measure,
        "aggregation": aggregation,
        "target": target,
        "direction": direction,
        "created_at": datapilot.utc_now(),
    })
    write_project_items(project_id, "kpis.json", specs, limit=8)
    flash("Ce chiffre apparaît maintenant dans votre résumé.", "ok")
    return redirect(url_for("project", project_id=project_id) + "#tracked-numbers")


@app.route("/project/<project_id>/kpis/<kpi_id>/delete", methods=["POST"])
def delete_kpi(project_id: str, kpi_id: str):
    read_metadata(project_id)
    if not CHART_ID_RE.fullmatch(kpi_id):
        abort(404)
    specs = read_project_items(project_id, "kpis.json")
    remaining = [item for item in specs if item.get("id") != kpi_id]
    if len(remaining) == len(specs):
        abort(404)
    write_project_items(project_id, "kpis.json", remaining, limit=8)
    flash("Le chiffre a été retiré du résumé.", "info")
    return redirect(url_for("project", project_id=project_id) + "#tracked-numbers")


@app.route("/project/<project_id>/dictionary/preview", methods=["POST"])
def preview_dictionary_names(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    entries = read_project_items(project_id, "dictionary.json")
    form_state = dictionary_form_state()
    column = request.form.get("column", "").strip()
    include_values = request.form.get("include_values") == "1"
    title_mode = request.form.get("title_mode", "manual").strip()
    language_name = request.form.get("language_name", "").strip()
    display_name = request.form.get("display_name", "")
    suggestion_message = ""
    if title_mode == "suggest":
        if column not in frame.columns:
            message = "Choisissez d’abord la colonne à renommer."
            if wants_json_response():
                return dictionary_fragment_response(
                    project_id,
                    frame=frame,
                    entries=entries,
                    message=message,
                    level="warn",
                    ok=False,
                    status=422,
                    translation_form=form_state,
                    focus="#translation-column",
                    error_code="validation_error",
                )
            flash("Choisissez d’abord la colonne à renommer.", "warn")
            return redirect(url_for("project", project_id=project_id, section="names"))
        if not language_name:
            message = "Indiquez la langue du nouveau titre."
            if wants_json_response():
                return dictionary_fragment_response(
                    project_id,
                    frame=frame,
                    entries=entries,
                    message=message,
                    level="warn",
                    ok=False,
                    status=422,
                    translation_form=form_state,
                    focus="#translation-language",
                    error_code="validation_error",
                )
            flash("Indiquez la langue du nouveau titre.", "warn")
            return redirect(url_for("project", project_id=project_id, section="names"))
        if request.form.get("suggestion_consent") != "1":
            message = "Autorisez l’envoi du titre choisi ou utilisez la saisie manuelle."
            if wants_json_response():
                return dictionary_fragment_response(
                    project_id,
                    frame=frame,
                    entries=entries,
                    message=message,
                    level="warn",
                    ok=False,
                    status=422,
                    translation_form=form_state,
                    focus="#translation-notice",
                    error_code="consent_required",
                )
            flash("Autorisez l’envoi du titre choisi ou utilisez la saisie manuelle.", "warn")
            return redirect(url_for("project", project_id=project_id, section="names"))
        source_title = display_column_name(column, entries)
        result = name_translation.suggest_column_names(
            [source_title],
            language_name,
            api_key=os.environ.get("GROQ_API_KEY", ""),
        )
        if result.get("ok") and result.get("suggestions"):
            display_name = str(result["suggestions"][0].get("suggested", ""))
            suggestion_message = (
                "Proposition préparée à partir du titre choisi. Vous pouvez encore la modifier."
                if result.get("source") == "groq"
                else "Proposition préparée avec le vocabulaire disponible sur cet ordinateur. Vous pouvez la modifier."
            )
        else:
            display_name = source_title
            suggestion_message = "La suggestion n’est pas disponible pour le moment. Saisissez le titre souhaité ci-dessous."
            if not wants_json_response():
                flash(suggestion_message, "warn")
    try:
        draft = make_translation_draft(
            frame,
            entries,
            column=column,
            display_name=display_name,
            language_name=language_name,
            include_values=include_values,
        )
    except datapilot.DataPilotError as exc:
        if wants_json_response():
            return dictionary_fragment_response(
                project_id,
                frame=frame,
                entries=entries,
                message=str(exc),
                level="warn",
                ok=False,
                status=422,
                translation_form=form_state,
                focus="#translation-notice",
                error_code="validation_error",
            )
        flash(str(exc), "warn")
        return redirect(url_for("project", project_id=project_id, section="names"))
    notice = "L’aperçu est prêt. Vérifiez les noms avant de les appliquer."
    notice_level = "info"
    if include_values and not draft["include_values"]:
        notice = draft["reason"]
        if not wants_json_response():
            flash(notice, "info")
    elif suggestion_message and not result.get("ok"):
        notice = suggestion_message
        notice_level = "warn"
    draft["suggestion_message"] = suggestion_message
    if wants_json_response():
        return dictionary_fragment_response(
            project_id,
            frame=frame,
            entries=entries,
            message=notice,
            level=notice_level,
            translation_draft=draft,
            translation_form=form_state,
            focus="#translation-preview",
        )
    return render_project(project_id, translation_draft=draft, active_section="names")


@app.route("/project/<project_id>/dictionary/apply", methods=["POST"])
def apply_dictionary_names(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    entries = read_project_items(project_id, "dictionary.json")
    form_state = dictionary_form_state()

    def fail(
        message: str,
        *,
        draft: dict | None = None,
        status: int = 422,
        code: str = "validation_error",
        focus: str = "#translation-notice",
    ):
        if wants_json_response():
            return dictionary_fragment_response(
                project_id,
                frame=frame,
                entries=entries,
                message=message,
                level="warn",
                ok=False,
                status=status,
                translation_draft=draft,
                translation_form=form_state,
                focus=focus,
                error_code=code,
            )
        flash(message, "warn")
        if draft is not None:
            return render_project(project_id, translation_draft=draft, active_section="names")
        return redirect(url_for("project", project_id=project_id, section="names"))

    column = request.form.get("column", "").strip()
    if column not in frame.columns:
        return fail("Choisissez une colonne existante.", focus="#translation-column")

    include_values = request.form.get("include_values") == "1"
    submitted_labels: dict[str, str] = {}
    if include_values:
        option = translation_column_option(frame, entries, column)
        if not option or not option["can_translate_values"]:
            return fail((option or {}).get("reason", "Les valeurs de cette colonne ne peuvent pas être renommées."))
        try:
            value_count = int(request.form.get("value_count", "0"))
        except ValueError:
            value_count = -1
        if not 0 <= value_count <= 50:
            return fail("Le nombre de catégories à adapter n’est pas valide.")
        expected_rows = category_rows(frame, column)
        if value_count != len(expected_rows):
            return fail(
                "La liste des catégories a changé. Préparez un nouvel aperçu avant de continuer.",
                status=409,
                code="preview_stale",
            )
        allowed_sources = {row["source"] for row in expected_rows}
        for index in range(value_count):
            source = request.form.get(f"source_{index}", "")
            if source not in allowed_sources or source in submitted_labels:
                return fail(
                    "Une catégorie proposée n’existe plus dans cette colonne. Préparez un nouvel aperçu.",
                    status=409,
                    code="preview_stale",
                )
            submitted_labels[source] = clean_display_label(
                request.form.get(f"value_label_{index}", ""), 160
            ) or source

    try:
        draft = make_translation_draft(
            frame,
            entries,
            column=column,
            display_name=request.form.get("display_name", ""),
            language_name=request.form.get("language_name", ""),
            include_values=include_values,
            submitted_labels=submitted_labels if include_values else None,
        )
    except datapilot.DataPilotError as exc:
        return fail(str(exc))

    existing = dictionary_entry(entries, column)
    confirm_merge = request.form.get("confirm_merge") == "1"
    value_labels = (
        [
            {"source": row["source"], "display": row["display"]}
            for row in draft["values"]
            if row["display"] != row["source"]
        ]
        if include_values
        else list((existing or {}).get("value_labels", []))
    )
    replacements = {item["source"]: item["display"] for item in value_labels}
    if replacements:
        value_report = rename_features.validate_category_replacements(
            frame,
            column,
            replacements,
            allow_merge=confirm_merge,
        )
        if not value_report.get("ok"):
            message = str(value_report.get("errors", ["Ces catégories ne peuvent pas être appliquées."])[0])
            code = "merge_confirmation_required" if not confirm_merge else "validation_error"
            return fail(message, draft=draft, status=409, code=code)

    updated = dict(existing or {})
    updated.update({
        "id": updated.get("id") or uuid.uuid4().hex[:12],
        "column": column,
        "display_name": draft["display_name"],
        "language_name": draft["language_name"],
        "language_code": draft["language_code"],
        "direction": draft["direction"],
        "value_labels": value_labels,
        "allow_value_merge": bool(confirm_merge and value_labels),
    })
    proposed_entries = [item for item in entries if item.get("column") != column] + [updated]
    column_report = rename_features.validate_column_renames(
        frame,
        {str(name): display_column_name(name, proposed_entries) for name in frame.columns},
    )
    if not column_report.get("ok"):
        return fail(
            str(column_report.get("errors", ["Ce titre ne peut pas être utilisé."])[0]),
            draft=draft,
            status=409,
            code="column_name_conflict",
        )

    comparable_existing = dict(existing or {})
    for item in (updated, comparable_existing):
        item.pop("updated_at", None)
    no_visible_change = draft["display_name"] == column and not value_labels
    if (existing and comparable_existing == updated) or (not existing and no_visible_change):
        message = "Ces noms sont déjà utilisés. Aucune modification n’était nécessaire."
        if wants_json_response():
            return dictionary_fragment_response(
                project_id,
                frame=frame,
                entries=entries,
                message=message,
                level="info",
                changed=False,
                translation_form=form_state,
                focus="#business-words-title",
            )
        flash(message, "info")
        return redirect(url_for("project", project_id=project_id, section="names"))

    save_dictionary_snapshot(project_id, entries, f"Avant le renommage de {column}")
    updated["updated_at"] = datapilot.utc_now()
    proposed_entries = [item for item in entries if item.get("column") != column] + [updated]
    write_project_items(project_id, "dictionary.json", proposed_entries, limit=80)
    message = "Les nouveaux noms sont maintenant utilisés dans l’aperçu, les graphiques et les exports."
    if wants_json_response():
        return dictionary_fragment_response(
            project_id,
            frame=frame,
            entries=proposed_entries,
            message=message,
            level="ok",
            changed=True,
            focus="#business-words-title",
            refresh_views=True,
        )
    flash(message, "ok")
    return redirect(url_for("project", project_id=project_id, section="names"))


@app.route("/project/<project_id>/dictionary/undo", methods=["POST"])
def undo_dictionary_names(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    history = read_dictionary_history(project_id)
    if not history:
        message = "Aucune modification récente de noms ne peut être annulée."
        if wants_json_response():
            return dictionary_fragment_response(
                project_id,
                frame=frame,
                entries=read_project_items(project_id, "dictionary.json"),
                message=message,
                level="info",
                changed=False,
            )
        flash(message, "info")
        return redirect(url_for("project", project_id=project_id, section="names"))
    previous = history.pop()
    snapshot = previous.get("snapshot", [])
    restored = snapshot if isinstance(snapshot, list) else []
    write_project_items(
        project_id,
        "dictionary.json",
        restored,
        limit=80,
    )
    write_dictionary_history(project_id, history)
    message = "La dernière modification de noms a été annulée."
    if wants_json_response():
        return dictionary_fragment_response(
            project_id,
            frame=frame,
            entries=restored,
            message=message,
            level="info",
            changed=True,
            refresh_views=True,
        )
    flash(message, "info")
    return redirect(url_for("project", project_id=project_id, section="names"))


@app.route("/project/<project_id>/dictionary", methods=["POST"])
def save_dictionary_entry(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    column = request.form.get("column", "").strip()
    if column not in frame.columns:
        flash("Choisissez une colonne existante.", "warn")
        return redirect(url_for("project", project_id=project_id, section="names"))
    entry = {
        "column": column,
        "label_fr": request.form.get("label_fr", "").strip()[:80],
        "label_ar": request.form.get("label_ar", "").strip()[:80],
        "description": request.form.get("description", "").strip()[:240],
        "unit": request.form.get("unit", "").strip()[:30],
        "synonyms": request.form.get("synonyms", "").strip()[:240],
    }
    if not any(entry[key] for key in ("label_fr", "label_ar", "description", "synonyms")):
        flash("Ajoutez au moins un nom clair ou une courte explication.", "warn")
        return redirect(url_for("project", project_id=project_id, section="names"))
    entries = read_project_items(project_id, "dictionary.json")
    existing = next((item for item in entries if item.get("column") == column), None)
    if existing:
        entry = {**existing, **entry}
    entry["id"] = existing.get("id") if existing else uuid.uuid4().hex[:12]
    entry["updated_at"] = datapilot.utc_now()
    save_dictionary_snapshot(project_id, entries, f"Avant la modification de {column}")
    entries = [item for item in entries if item.get("column") != column] + [entry]
    write_project_items(project_id, "dictionary.json", entries, limit=80)
    flash("DataPilot utilisera désormais ces mots dans vos questions.", "ok")
    return redirect(url_for("project", project_id=project_id, section="names"))


@app.route("/project/<project_id>/dictionary/<entry_id>/delete", methods=["POST"])
def delete_dictionary_entry(project_id: str, entry_id: str):
    read_metadata(project_id)
    if not CHART_ID_RE.fullmatch(entry_id):
        if wants_json_response():
            return json_response({
                "ok": False,
                "message": "Ce nom personnalisé n’existe pas.",
                "error": {"code": "resource_not_found", "message": "Ce nom personnalisé n’existe pas."},
            }, 404)
        abort(404)
    frame = load_frame(project_id)
    entries = read_project_items(project_id, "dictionary.json")
    remaining = [item for item in entries if item.get("id") != entry_id]
    if len(remaining) == len(entries):
        if wants_json_response():
            return dictionary_fragment_response(
                project_id,
                frame=frame,
                entries=entries,
                message="Ce nom personnalisé a déjà été retiré.",
                level="info",
                ok=False,
                status=404,
                error_code="resource_not_found",
            )
        abort(404)
    removed = next(item for item in entries if item.get("id") == entry_id)
    save_dictionary_snapshot(
        project_id,
        entries,
        f"Avant le retrait de {removed.get('column', 'ce nom')}",
    )
    write_project_items(project_id, "dictionary.json", remaining, limit=80)
    message = "Ce nom personnalisé a été supprimé."
    if wants_json_response():
        return dictionary_fragment_response(
            project_id,
            frame=frame,
            entries=remaining,
            message=message,
            level="info",
            changed=True,
            refresh_views=True,
        )
    flash(message, "info")
    return redirect(url_for("project", project_id=project_id, section="names"))


@app.route("/project/<project_id>/charts", methods=["POST"])
def create_chart(project_id: str):
    read_metadata(project_id)
    frame = load_frame(project_id)
    dictionary_entries = read_project_items(project_id, "dictionary.json")
    dimension = request.form.get("dimension", "").strip()
    measure = request.form.get("measure", "").strip()
    aggregation = request.form.get("aggregation", "count").strip()
    chart_type = request.form.get("chart_type", "auto").strip()
    try:
        datapilot.build_custom_chart(frame, dimension, measure, aggregation, chart_type)
    except datapilot.DataPilotError as exc:
        if wants_json_response():
            return chart_fragment_response(
                project_id,
                frame=frame,
                dictionary_entries=dictionary_entries,
                message=str(exc),
                level="warn",
                ok=False,
                status=422,
            )
        flash(str(exc), "warn")
        return redirect(url_for("project", project_id=project_id, section="charts"))

    specs = read_chart_specs(project_id)
    chart_id = uuid.uuid4().hex[:12]
    specs.append({
        "id": chart_id,
        "dimension": dimension,
        "measure": measure,
        "aggregation": aggregation,
        "chart_type": chart_type,
        "created_at": datapilot.utc_now(),
    })
    write_chart_specs(project_id, specs)
    if wants_json_response():
        return chart_fragment_response(
            project_id,
            frame=frame,
            dictionary_entries=dictionary_entries,
            message="Le graphique est prêt.",
            level="ok",
        )
    flash("Votre graphique a été créé et enregistré sur cet ordinateur.", "ok")
    return redirect(url_for("project", project_id=project_id, section="charts"))


@app.route("/project/<project_id>/charts/<chart_id>/delete", methods=["POST"])
def delete_chart(project_id: str, chart_id: str):
    read_metadata(project_id)
    if not CHART_ID_RE.fullmatch(chart_id):
        abort(404)
    specs = read_chart_specs(project_id)
    remaining = [spec for spec in specs if spec.get("id") != chart_id]
    if len(remaining) == len(specs):
        abort(404)
    write_chart_specs(project_id, remaining)
    message = "Le graphique a été supprimé."
    if wants_json_response():
        return chart_fragment_response(
            project_id,
            frame=load_frame(project_id),
            dictionary_entries=read_project_items(project_id, "dictionary.json"),
            message=message,
        )
    flash(message, "info")
    return redirect(url_for("project", project_id=project_id, section="charts"))


@app.route("/project/<project_id>/ask", methods=["GET", "POST"])
def ask(project_id: str):
    read_metadata(project_id)
    if request.method == "GET":
        return redirect(url_for("project", project_id=project_id))
    question = request.form.get("question", "").strip()
    mode = request.form.get("mode", "local")
    return_section = request.form.get("section", "overview").strip()
    if return_section not in PROJECT_SECTIONS:
        return_section = "overview"

    if not question or len(question) > 500:
        answer = normalize_answer({
            "ok": False,
            "answer": "Écrivez une question de 500 caractères maximum.",
            "source": "local",
        }, question=question[:500], mode="local")
        if wants_json_response():
            return json_response(answer, 422)
        return render_project(project_id, answer=answer, active_section=return_section)

    if mode not in {"local", "groq"}:
        answer = normalize_answer({
            "ok": False,
            "answer": "Ce niveau d’explication n’est pas disponible.",
            "source": "local",
        }, question=question, mode="local")
        if wants_json_response():
            return json_response(answer, 422)
        return render_project(project_id, answer=answer, active_section=return_section)

    frame = load_frame(project_id)
    dictionary_entries = read_project_items(project_id, "dictionary.json")
    understood_question = apply_business_words(question, dictionary_entries)
    if mode == "groq":
        answer = groq_analysis.answer_with_groq(frame, understood_question)
    else:
        answer = datapilot.answer_question(frame, understood_question)
        answer.setdefault("source", "local")
    payload = localize_answer_payload(
        normalize_answer(answer, question=question, mode=mode),
        dictionary_entries,
    )
    chart_proposal = advanced_features.suggest_chart_from_question(
        frame,
        question,
        aliases=dictionary_aliases(dictionary_entries),
    )
    if chart_proposal.get("ok") and chart_proposal.get("chart"):
        if not payload["ok"]:
            payload.update({
                "ok": True,
                "answer": str(chart_proposal["message"]),
                "source": "local",
            })
        interpretation = chart_proposal["interpretation"]
        payload["chart"] = chart_proposal["chart"]
        try:
            display_frame = build_display_frame(frame, dictionary_entries)
            payload["chart"] = datapilot.build_custom_chart(
                display_frame,
                dimension=display_column_name(interpretation.get("dimension", ""), dictionary_entries),
                measure=display_column_name(interpretation.get("measure") or "", dictionary_entries),
                aggregation=str(interpretation.get("aggregation", "count")),
                chart_type=str(interpretation.get("chart_type", "auto")),
            )
        except datapilot.DataPilotError:
            pass
        payload["chart_spec"] = interpretation
        payload["chart_message"] = chart_proposal["message"]
        payload["pin_url"] = url_for("create_chart", project_id=project_id)
        payload["cautions"] = (payload.get("cautions", []) + chart_proposal.get("warnings", []))[:5]
    payload = localize_answer_payload(payload, dictionary_entries)
    if wants_json_response():
        return json_response(payload)
    return render_project(project_id, answer=payload, active_section=return_section)


@app.route("/project/<project_id>/export/<fmt>")
def export(project_id: str, fmt: str):
    metadata = read_metadata(project_id)
    entries = read_project_items(project_id, "dictionary.json")
    base_name = secure_filename(metadata.get("name", "dataset_nettoye")) or "dataset_nettoye"
    if fmt == "csv":
        return fast_csv_download(project_id, entries, f"{base_name}_nettoye.csv")
    if fmt == "zip":
        payload = make_fast_archive(project_id, entries, base_name)
        return download_response(
            payload,
            mimetype="application/zip",
            download_name=f"{base_name}_nettoye.zip",
        )
    frame = build_display_frame(load_frame(project_id), entries)
    if fmt == "json":
        payload = io.BytesIO(
            frame.to_json(orient="records", force_ascii=False, date_format="iso").encode("utf-8")
        )
        return download_response(
            payload,
            mimetype="application/json",
            download_name=f"{base_name}_nettoye.json",
        )
    if fmt == "xlsx":
        payload = make_excel_export(datapilot.safe_export_frame(frame))
        return download_response(
            payload,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            download_name=f"{base_name}_nettoye.xlsx",
        )
    abort(404)


@app.errorhandler(413)
def too_large(_error):
    if wants_json_response():
        message = "Le fichier dépasse la limite de 20 Mo."
        return json_response({
            "ok": False,
            "message": message,
            "error": {"code": "file_too_large", "message": message},
        }, 413)
    flash("Le fichier dépasse la limite de 20 Mo.", "warn")
    return redirect(url_for("index"))


@app.errorhandler(400)
@app.errorhandler(404)
def json_http_error(error):
    status = int(getattr(error, "code", 500))
    message = (
        "Votre session a expiré. Rechargez la page puis réessayez."
        if status == 400
        else "Cette analyse n’existe plus ou n’est pas accessible."
    )
    if not wants_json_response():
        return render_template(
            "error.html",
            status_code=status,
            error_title="Session expirée" if status == 400 else "Analyse introuvable",
            error_message=message,
        ), status
    code = "csrf_invalid" if status == 400 else "resource_not_found"
    return json_response({
        "ok": False,
        "answer": message,
        "message": message,
        "evidence": [],
        "error": {"code": code, "message": message},
    }, status)


@app.errorhandler(500)
def internal_error(error):
    app.logger.error("Erreur interne DataPilot", exc_info=getattr(error, "original_exception", error))
    message = "Une erreur inattendue s’est produite. Vos fichiers sont restés intacts."
    if wants_json_response():
        return json_response({
            "ok": False,
            "message": message,
            "error": {"code": "internal_error", "message": message},
        }, 500)
    return render_template(
        "error.html",
        status_code=500,
        error_title="Impossible de terminer cette action",
        error_message=message,
    ), 500


if __name__ == "__main__":
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("DATAPILOT_PORT", "5071"))
    app.run(host="127.0.0.1", port=port, debug=os.environ.get("DATAPILOT_DEBUG") == "1")
