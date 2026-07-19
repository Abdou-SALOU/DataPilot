# -*- coding: utf-8 -*-
"""Moteur local de DataPilot : lecture, profilage, nettoyage et interrogation.

Le module ne dépend d'aucune API externe. Toutes les opérations sont explicites,
reproductibles et limitées à des données tabulaires.
"""

from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


MAX_ROWS = 100_000
MAX_COLUMNS = 200
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json"}
CHART_TYPES = {"auto", "bar", "line", "doughnut"}
CHART_AGGREGATIONS = {"count", "mean", "sum", "median"}
CHART_PALETTE = [
    "#5b5bd6", "#2dd4bf", "#8b5cf6", "#38bdf8", "#f59e0b", "#f97316",
    "#ec4899", "#14b8a6", "#818cf8", "#84cc16", "#fb7185", "#06b6d4",
]

FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

VALUE_ALIASES = {
    "city": {
        "casa": "Casablanca", "casblanca": "Casablanca", "marrakesh": "Marrakech",
        "fes": "Fès",
    },
    "channel": {
        "whatsapp": "WhatsApp", "wathsapp": "WhatsApp", "en magasin": "Boutique",
        "magasin": "Boutique",
    },
    "status": {"livree": "Livrée", "livre": "Livrée", "annulee": "Annulée"},
    "customer_type": {"fidele": "Fidèle"},
    "category": {"epicerie": "Épicerie"},
}

SALES_COLUMN_ALIASES = {
    "quantity": {"quantite", "quantity", "qte"},
    "unit_price": {"prix_unitaire_mad", "prix_unitaire", "unit_price"},
    "discount": {"remise_mad", "remise", "discount"},
    "delivery_fee": {"frais_livraison_factures_mad", "frais_livraison", "delivery_fee"},
    "delivery_cost": {"cout_livraison_mad", "cout_livraison", "delivery_cost"},
    "gross": {"total_brut_mad", "total_brut", "gross_total"},
    "net": {"chiffre_affaires_net_mad", "chiffre_affaires_net", "net_revenue"},
    "margin": {"marge_brute_mad", "marge_brute", "gross_margin"},
    "status": {"statut_commande", "statut", "status"},
    "score": {"note_satisfaction", "satisfaction", "score", "note"},
}


class DataPilotError(ValueError):
    """Erreur compréhensible pouvant être affichée à l'utilisateur."""


@dataclass(frozen=True)
class CleaningAction:
    id: str
    label: str
    description: str
    impact: int
    severity: str = "conseille"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "impact": self.impact,
            "severity": self.severity,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(
                path,
                sep=None,
                engine="python",
                encoding=encoding,
                nrows=MAX_ROWS + 1,
                on_bad_lines="error",
            )
        except (UnicodeDecodeError, pd.errors.ParserError, csv.Error) as exc:
            last_error = exc
    raise DataPilotError(f"CSV illisible ou structure incohérente : {last_error}")


def _read_json(path: Path) -> pd.DataFrame:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataPilotError(f"JSON invalide : {exc}") from exc
    if isinstance(raw, list):
        if not raw or all(isinstance(item, dict) for item in raw):
            return pd.json_normalize(raw)
    if isinstance(raw, dict):
        for key in ("data", "records", "items", "results"):
            if isinstance(raw.get(key), list):
                return pd.json_normalize(raw[key])
        try:
            return pd.DataFrame(raw)
        except ValueError:
            return pd.json_normalize(raw)
    raise DataPilotError("Le JSON doit contenir une liste d'objets ou un objet tabulaire.")


def read_dataset(path: str | Path) -> pd.DataFrame:
    """Lit un fichier tabulaire contrôlé et retourne un DataFrame."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DataPilotError("Format non pris en charge. Utilisez CSV, Excel ou JSON.")
    try:
        if ext == ".csv":
            frame = _read_csv(path)
        elif ext in {".xlsx", ".xls"}:
            frame = pd.read_excel(path, sheet_name=0, nrows=MAX_ROWS + 1)
        else:
            frame = _read_json(path)
    except DataPilotError:
        raise
    except Exception as exc:
        raise DataPilotError(f"Impossible de lire le fichier : {exc}") from exc

    if len(frame) > MAX_ROWS:
        raise DataPilotError(f"Le MVP accepte au maximum {MAX_ROWS:,} lignes.")
    if len(frame.columns) > MAX_COLUMNS:
        raise DataPilotError(f"Le MVP accepte au maximum {MAX_COLUMNS} colonnes.")
    if len(frame.columns) == 0:
        raise DataPilotError("Aucune colonne détectée dans ce fichier.")
    if frame.columns.duplicated().any():
        duplicates = frame.columns[frame.columns.duplicated()].tolist()
        raise DataPilotError(f"Noms de colonnes dupliqués : {', '.join(map(str, duplicates))}")

    frame.columns = [str(col).strip() or f"colonne_{idx + 1}" for idx, col in enumerate(frame.columns)]
    return frame


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if math.isnan(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return str(value) if not isinstance(value, (str, int, float, bool)) else value


def dataframe_preview(frame: pd.DataFrame, limit: int = 12) -> list[dict[str, Any]]:
    return [
        {str(k): _json_value(v) for k, v in row.items()}
        for row in frame.head(limit).to_dict(orient="records")
    ]


def column_kind(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "booléen"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_numeric_dtype(series):
        return "nombre"
    return "texte"


def profile_dataframe(frame: pd.DataFrame) -> dict[str, Any]:
    rows, columns = frame.shape
    missing = int(frame.isna().sum().sum())
    empty_strings = int(
        sum(
            series.astype("string").str.strip().eq("").fillna(False).sum()
            for _, series in frame.select_dtypes(include=["object", "string"]).items()
        )
    )
    duplicates = int(frame.duplicated().sum())
    total_cells = max(rows * columns, 1)
    constant_columns = 0
    details: list[dict[str, Any]] = []
    for name in frame.columns:
        series = frame[name]
        unique = int(series.nunique(dropna=True))
        if rows > 1 and unique <= 1:
            constant_columns += 1
        examples = [_json_value(v) for v in series.dropna().head(3).tolist()]
        details.append({
            "name": str(name),
            "kind": column_kind(series),
            "missing": int(series.isna().sum()),
            "missing_pct": round(float(series.isna().mean() * 100), 1) if rows else 0,
            "unique": unique,
            "examples": examples,
        })

    missing_ratio = (missing + empty_strings) / total_cells
    duplicate_ratio = duplicates / max(rows, 1)
    constant_ratio = constant_columns / max(columns, 1)
    score = max(0, round(100 - missing_ratio * 45 - duplicate_ratio * 30 - constant_ratio * 10))
    return {
        "rows": rows,
        "columns_count": columns,
        "missing": missing,
        "empty_strings": empty_strings,
        "duplicates": duplicates,
        "constant_columns": constant_columns,
        "quality_score": score,
        "columns": details,
    }


def _object_series(frame: pd.DataFrame) -> Iterable[tuple[str, pd.Series]]:
    return frame.select_dtypes(include=["object", "string"]).items()


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _parse_local_number(value: Any, *, allow_free: bool = False) -> float:
    """Lit les nombres saisis comme du texte sans perdre les montants négatifs."""
    if _is_missing(value):
        return math.nan
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return math.nan
    if text.startswith("'"):
        text = text[1:].strip()
    normalized = normalize_text(text)
    if normalized in {"na", "n a", "null", "none", "inconnu", "non renseigne"}:
        return math.nan
    if allow_free and normalized in {"gratuit", "offert", "free"}:
        return 0.0

    compact = text.replace("\u202f", "").replace("\xa0", " ")
    compact = re.sub(r"(?i)(mad|dhs?|dirhams?|€|\$)", "", compact)
    compact = compact.replace(" ", "")
    if compact.endswith("%"):
        compact = compact[:-1]
    if "," in compact and "." in compact:
        if compact.rfind(",") > compact.rfind("."):
            compact = compact.replace(".", "").replace(",", ".")
        else:
            compact = compact.replace(",", "")
    else:
        compact = compact.replace(",", ".")
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", compact):
        return math.nan
    try:
        return float(compact)
    except ValueError:
        return math.nan


def _coerce_numeric_series(series: pd.Series, *, allow_free: bool = False) -> pd.Series:
    return pd.to_numeric(
        series.map(lambda value: _parse_local_number(value, allow_free=allow_free)), errors="coerce"
    )


def _is_plain_number_input(value: Any) -> bool:
    if _is_missing(value) or isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return True
    text = str(value).strip().replace("\u202f", "").replace("\xa0", " ").replace(" ", "")
    return bool(re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?", text))


def _parse_local_date(value: Any) -> pd.Timestamp | pd.NaT:
    if _is_missing(value):
        return pd.NaT
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Timestamp(value)
    text = str(value).strip()
    if not text:
        return pd.NaT
    month_year = re.fullmatch(r"([\wÀ-ÿ]+)\s+(\d{4})", text.casefold())
    if month_year:
        month = FRENCH_MONTHS.get(normalize_text(month_year.group(1)))
        if month:
            return pd.Timestamp(year=int(month_year.group(2)), month=month, day=1)
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True, format="mixed")
    return pd.Timestamp(parsed) if not _is_missing(parsed) else pd.NaT


def _coerce_date_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    text = series.astype("string").str.strip()
    parsed = pd.to_datetime(text, errors="coerce", dayfirst=True, format="mixed")

    # Les noms de mois français ne sont pas toujours compris par pandas selon
    # la langue de Windows. On ne traite en Python que ces rares valeurs,
    # plutôt que de relancer le parseur complet pour chaque cellule.
    month_year_mask = parsed.isna() & text.str.match(
        r"^[A-Za-zÀ-ÿ]+\s+\d{4}$", na=False
    )
    for value in text[month_year_mask].dropna().unique():
        converted = _parse_local_date(value)
        if not _is_missing(converted):
            parsed.loc[text.eq(value)] = converted
    return parsed


def _semantic_value_role(column: object) -> str | None:
    name = normalize_text(str(column)).replace(" ", "_")
    if any(term in name for term in ("ville", "city", "localisation")):
        return "city"
    if any(term in name for term in ("canal", "channel")):
        return "channel"
    if any(term in name for term in ("statut", "status", "etat")):
        return "status"
    if "client" in name and any(term in name for term in ("type", "profil", "segment")):
        return "customer_type"
    if any(term in name for term in ("categorie", "category")):
        return "category"
    return None


def _can_fill_with_mode(column: object) -> bool:
    """Évite de fabriquer un identifiant, un produit ou un nom manquant."""
    name = normalize_text(str(column)).replace(" ", "_")
    if any(term in name for term in ("id", "code", "identifiant", "numero", "nom", "name", "produit", "vendeur")):
        return False
    if _semantic_value_role(column) is not None:
        return True
    return any(term in name for term in ("mode", "paiement", "payment", "pays", "region", "segment"))


def _value_harmonization_map(series: pd.Series, column: object) -> dict[str, str]:
    """Propose des regroupements explicables vers une valeur connue ou déjà présente."""
    role = _semantic_value_role(column)
    if role is None:
        return {}
    text = series.astype("string").str.strip()
    counts = text.dropna()[text.dropna().ne("")].value_counts()
    if counts.empty:
        return {}
    canonical_by_key: dict[str, str] = {}
    for value in counts.index:
        key = normalize_text(str(value))
        current = canonical_by_key.get(key)
        if current is None or counts[value] > counts[current]:
            canonical_by_key[key] = str(value)

    mappings: dict[str, str] = {}
    aliases = VALUE_ALIASES.get(role, {})
    protected_values = set(aliases.values())
    for value in counts.index:
        source = str(value)
        if source in protected_values:
            continue
        key = normalize_text(source)
        target = canonical_by_key.get(key)
        if target and target != source:
            mappings[source] = target
            continue
        alias_target = aliases.get(key)
        if alias_target and alias_target != source:
            mappings[source] = alias_target
            continue
        if len(key) < 4:
            continue
        candidates = [
            (candidate_key, candidate)
            for candidate_key, candidate in canonical_by_key.items()
            if candidate != source and (counts[candidate] >= 2 or candidate in protected_values)
        ]
        if not candidates:
            continue
        best_key, best_target = max(
            candidates,
            key=lambda item: (
                item[1] in protected_values,
                SequenceMatcher(None, key, item[0]).ratio(),
                counts[item[1]],
            ),
        )
        if SequenceMatcher(None, key, best_key).ratio() >= 0.86:
            mappings[source] = best_target
    return mappings


def _sales_columns(frame: pd.DataFrame) -> dict[str, object]:
    normalized = {normalize_text(str(name)).replace(" ", "_"): name for name in frame.columns}
    selected: dict[str, object] = {}
    for meaning, aliases in SALES_COLUMN_ALIASES.items():
        selected_name = next((normalized[alias] for alias in aliases if alias in normalized), None)
        if selected_name is not None:
            selected[meaning] = selected_name
    required = {"quantity", "unit_price", "gross"}
    return selected if required.issubset(selected) else {}


def _sales_metric_issue_rows(frame: pd.DataFrame) -> set[object]:
    columns = _sales_columns(frame)
    if not columns:
        return set()
    quantity = _coerce_numeric_series(frame[columns["quantity"]])
    price = _coerce_numeric_series(frame[columns["unit_price"]])
    gross = _coerce_numeric_series(frame[columns["gross"]])
    issues = set(frame.index[(quantity <= 0) | (price < 0) | ((quantity * price - gross).abs() > 0.01)])
    if "discount" in columns:
        discount = _coerce_numeric_series(frame[columns["discount"]])
        issues.update(frame.index[(discount < 0) | (discount > gross)])
    if "delivery_cost" in columns:
        cost = _coerce_numeric_series(frame[columns["delivery_cost"]])
        issues.update(frame.index[cost < 0])
    if "score" in columns:
        score = _coerce_numeric_series(frame[columns["score"]])
        issues.update(frame.index[(score < 1) | (score > 5) | (score % 1 != 0)])
    for meaning, column in columns.items():
        if meaning == "status":
            continue
        raw_issue = frame[column].map(lambda value: not _is_missing(value) and not _is_plain_number_input(value))
        issues.update(frame.index[raw_issue])
    return issues


def _repair_sales_metrics(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Répare les incohérences arithmétiques d'un tableau de ventes reconnu."""
    columns = _sales_columns(frame)
    if not columns:
        return frame, 0
    result = frame.copy()
    quantity_column = columns["quantity"]
    price_column = columns["unit_price"]
    gross_column = columns["gross"]
    quantities = _coerce_numeric_series(result[quantity_column])
    prices = _coerce_numeric_series(result[price_column])
    gross_values = _coerce_numeric_series(result[gross_column])
    valid_quantities = quantities[(quantities > 0) & quantities.notna()]
    valid_prices = prices[(prices >= 0) & prices.notna()]
    quantity_fallback = float(valid_quantities.median()) if not valid_quantities.empty else 1.0
    price_fallback = float(valid_prices.median()) if not valid_prices.empty else 0.0
    score_fallback = 4.0
    if "score" in columns:
        scores = _coerce_numeric_series(result[columns["score"]])
        valid_scores = scores[(scores >= 1) & (scores <= 5) & (scores % 1 == 0)]
        if not valid_scores.empty:
            score_fallback = float(valid_scores.mode().iloc[0])
    cost_fallback = 0.0
    if "delivery_cost" in columns:
        costs = _coerce_numeric_series(result[columns["delivery_cost"]])
        valid_costs = costs[(costs >= 0) & costs.notna()]
        if not valid_costs.empty:
            cost_fallback = float(valid_costs.median())

    repaired_rows: set[object] = set()
    for index in result.index:
        old_quantity = _parse_local_number(result.at[index, quantity_column])
        old_price = _parse_local_number(result.at[index, price_column])
        old_gross = _parse_local_number(result.at[index, gross_column])
        quantity = old_quantity
        price = old_price
        gross = old_gross
        changed = False

        if not math.isfinite(quantity) or quantity <= 0:
            inferred = gross / price if math.isfinite(gross) and gross > 0 and math.isfinite(price) and price > 0 else math.nan
            quantity = round(inferred) if math.isfinite(inferred) and inferred > 0 and abs(inferred - round(inferred)) < 0.01 else quantity_fallback
            changed = True
        if not math.isfinite(price) or price < 0:
            inferred = gross / quantity if math.isfinite(gross) and gross > 0 and quantity > 0 else math.nan
            price = round(inferred, 2) if math.isfinite(inferred) and inferred >= 0 else price_fallback
            changed = True
        expected_gross = round(quantity * price, 2)
        if not math.isfinite(gross) or gross < 0 or abs(gross - expected_gross) > 0.01:
            gross = expected_gross
            changed = True
        result.at[index, quantity_column] = quantity
        result.at[index, price_column] = price
        result.at[index, gross_column] = gross

        discount = 0.0
        if "discount" in columns:
            raw_discount = result.at[index, columns["discount"]]
            discount = _parse_local_number(raw_discount)
            if isinstance(raw_discount, str) and raw_discount.strip().endswith("%") and math.isfinite(discount):
                discount = round(gross * discount / 100, 2)
            if not math.isfinite(discount) or discount < 0 or discount > gross:
                discount = 0.0
            if _parse_local_number(raw_discount) != discount:
                changed = True
            result.at[index, columns["discount"]] = discount

        delivery_fee = 0.0
        if "delivery_fee" in columns:
            raw_fee = result.at[index, columns["delivery_fee"]]
            delivery_fee = _parse_local_number(raw_fee, allow_free=True)
            if not math.isfinite(delivery_fee) or delivery_fee < 0:
                delivery_fee = 0.0
                changed = True
            result.at[index, columns["delivery_fee"]] = delivery_fee

        delivery_cost = 0.0
        if "delivery_cost" in columns:
            raw_cost = result.at[index, columns["delivery_cost"]]
            delivery_cost = _parse_local_number(raw_cost, allow_free=True)
            if not math.isfinite(delivery_cost) or delivery_cost < 0:
                delivery_cost = cost_fallback
                changed = True
            result.at[index, columns["delivery_cost"]] = delivery_cost

        old_net = math.nan
        old_margin = math.nan
        net = math.nan
        if "net" in columns:
            old_net = _parse_local_number(result.at[index, columns["net"]])
            net = old_net
            status = normalize_text(str(result.at[index, columns["status"]])) if "status" in columns else ""
            expected_net = 0.0 if status == "annulee" else gross - discount + delivery_fee
            if status in {"annulee", "livree", "livre"} and (not math.isfinite(net) or abs(net - expected_net) > 0.01):
                net = round(expected_net, 2)
                changed = True
            result.at[index, columns["net"]] = net
        if "margin" in columns:
            old_margin = _parse_local_number(result.at[index, columns["margin"]])
            if changed and math.isfinite(old_net) and math.isfinite(old_margin):
                prior_cost = _parse_local_number(frame.at[index, columns["delivery_cost"]], allow_free=True) if "delivery_cost" in columns else 0.0
                if not math.isfinite(prior_cost):
                    prior_cost = 0.0
                estimated_product_cost = old_net - old_margin - prior_cost
                if math.isfinite(net):
                    result.at[index, columns["margin"]] = round(net - estimated_product_cost - delivery_cost, 2)

        if "score" in columns:
            score = _parse_local_number(result.at[index, columns["score"]])
            if not math.isfinite(score) or score < 1 or score > 5 or score % 1:
                result.at[index, columns["score"]] = score_fallback
                changed = True
        if changed:
            repaired_rows.add(index)
    for meaning, column in columns.items():
        if meaning != "status":
            result[column] = _coerce_numeric_series(
                result[column], allow_free=meaning in {"delivery_fee", "delivery_cost"}
            )
    return result, len(repaired_rows)


def suggest_cleaning(frame: pd.DataFrame) -> list[dict[str, Any]]:
    suggestions: list[CleaningAction] = []
    duplicates = int(frame.duplicated().sum())
    if duplicates:
        suggestions.append(CleaningAction(
            "drop_duplicates", "Supprimer les doublons exacts",
            "Conserve la première occurrence de chaque ligne identique.", duplicates, "fort",
        ))
    blank_count = 0
    trim_count = 0
    cleaned_text: dict[object, pd.Series] = {}
    for name, series in _object_series(frame):
        as_text = series.astype("string")
        stripped = as_text.str.strip()
        cleaned_text[name] = stripped
        blank_count += int(stripped.eq("").fillna(False).sum())
        trim_count += int((as_text.ne(stripped) & as_text.notna()).sum())
    if blank_count:
        suggestions.append(CleaningAction(
            "normalize_blanks", "Convertir les cellules vides",
            "Transforme les chaînes vides ou composées d'espaces en valeurs manquantes.", blank_count, "fort",
        ))
    if trim_count:
        suggestions.append(CleaningAction(
            "strip_text", "Retirer les espaces inutiles",
            "Nettoie les espaces au début et à la fin des textes.", trim_count, "fort",
        ))

    for name, series in _object_series(frame):
        mappings = _value_harmonization_map(series, name)
        if not mappings:
            continue
        affected = int(series.isin(mappings).sum())
        targets = list(dict.fromkeys(mappings.values()))[:2]
        examples = ", ".join(targets)
        suggestions.append(CleaningAction(
            f"harmonize_values::{name}", f"Harmoniser les libellés de {name}",
            f"Regroupe {affected} saisie(s) proche(s) sous des libellés déjà présents, par exemple : {examples}.",
            affected, "fort",
        ))

    sales_issues = _sales_metric_issue_rows(frame)
    if sales_issues:
        suggestions.append(CleaningAction(
            "repair_sales_metrics", "Réparer les montants incohérents",
            "Corrige les quantités, prix, remises et totaux contradictoires à partir des autres valeurs de la vente.",
            len(sales_issues), "fort",
        ))

    sales_columns = set(_sales_columns(frame).values())

    for name in frame.columns:
        series = frame[name]
        missing = int(series.isna().sum())
        safe_name = str(name)
        if pd.api.types.is_numeric_dtype(series) and missing:
            suggestions.append(CleaningAction(
                f"fill_median::{safe_name}", f"Compléter {safe_name} par la médiane",
                "Méthode robuste aux valeurs extrêmes ; chaque remplacement reste journalisé.", missing,
            ))
            continue

        if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
            continue
        text = cleaned_text[name]
        populated = text.dropna()
        populated = populated[populated.ne("")]
        if len(populated) >= 3:
            sample = populated.head(1_500)
            numeric = _coerce_numeric_series(sample, allow_free="livraison" in normalize_text(safe_name))
            numeric_rate = float(numeric.notna().mean())
            if numeric_rate >= 0.80:
                if name not in sales_columns:
                    suggestions.append(CleaningAction(
                        f"convert_numeric::{safe_name}", f"Convertir {safe_name} en nombre",
                        f"{round(numeric_rate * 100)} % des valeurs reconnues comme numériques.",
                        int(numeric.notna().sum()),
                    ))
                    predicted_missing = int(_coerce_numeric_series(
                        text, allow_free="livraison" in normalize_text(safe_name)
                    ).isna().sum())
                    if predicted_missing:
                        suggestions.append(CleaningAction(
                            f"fill_median::{safe_name}", f"Compléter {safe_name} par la médiane",
                            "Applique une valeur centrale après la conversion des nombres.", predicted_missing,
                        ))
            else:
                date_hint = any(
                    token in normalize_text(safe_name).split()
                    for token in ("date", "jour", "mois", "annee", "heure", "time", "created", "updated", "timestamp")
                )
                date_shape_rate = float(sample.str.match(
                    r"^(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|[A-Za-zÀ-ÿ]+\s+\d{4})(?:[ T].*)?$",
                    na=False,
                ).mean())
                if date_hint or date_shape_rate >= 0.75:
                    date_values = _coerce_date_series(sample)
                    date_rate = float(date_values.notna().mean())
                    if date_rate >= 0.85 or (date_hint and date_rate >= 0.60):
                        suggestions.append(CleaningAction(
                            f"convert_date::{safe_name}", f"Convertir {safe_name} en date",
                            f"{round(date_rate * 100)} % des valeurs reconnues comme dates.",
                            int(date_values.notna().sum()), "fort" if date_rate >= 0.98 else "conseille",
                        ))
        if (
            missing
            and not populated.empty
            and "date" not in normalize_text(safe_name)
            and name not in sales_columns
            and _can_fill_with_mode(name)
        ):
            suggestions.append(CleaningAction(
                f"fill_mode::{safe_name}", f"Compléter {safe_name} par la valeur fréquente",
                "Utilise le mode de la colonne et conserve cette décision dans le journal.", missing,
            ))

    return [item.as_dict() for item in suggestions]


def apply_cleaning(frame: pd.DataFrame, action_ids: Iterable[str]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    result = frame.copy()
    log: list[dict[str, Any]] = []
    allowed = {item["id"]: item for item in suggest_cleaning(frame)}
    selected = [action for action in action_ids if action in allowed]

    # Les libellés et montants sont stabilisés avant la déduplication et les compléments.
    order = {
        "normalize_blanks": 0,
        "strip_text": 1,
        "harmonize_values": 2,
        "repair_sales_metrics": 3,
        "convert_numeric": 4,
        "convert_date": 4,
        "drop_duplicates": 5,
        "fill_median": 6,
        "fill_mode": 6,
    }
    selected.sort(key=lambda action: order.get(action.split("::", 1)[0], 3))
    for action in selected:
        before_rows = len(result)
        before_missing = int(result.isna().sum().sum())
        if action == "normalize_blanks":
            for name, series in _object_series(result):
                result[name] = series.replace(r"^\s*$", pd.NA, regex=True)
        elif action == "strip_text":
            for name, series in _object_series(result):
                result[name] = series.map(lambda value: value.strip() if isinstance(value, str) else value)
        elif action == "repair_sales_metrics":
            result, _ = _repair_sales_metrics(result)
        elif action == "drop_duplicates":
            result = result.drop_duplicates(keep="first").reset_index(drop=True)
        else:
            if "::" not in action:
                continue
            prefix, column = action.split("::", 1)
            if column not in result.columns:
                continue
            if prefix == "harmonize_values":
                mappings = _value_harmonization_map(result[column], column)
                if mappings:
                    result[column] = result[column].replace(mappings)
            elif prefix == "convert_numeric":
                result[column] = _coerce_numeric_series(
                    result[column], allow_free="livraison" in normalize_text(str(column))
                )
            elif prefix == "convert_date":
                result[column] = _coerce_date_series(result[column])
            elif prefix == "fill_median":
                median = result[column].median()
                if not _is_missing(median):
                    result[column] = result[column].fillna(median)
            elif prefix == "fill_mode":
                mode = result[column].mode(dropna=True)
                if not mode.empty:
                    result[column] = result[column].fillna(mode.iloc[0])
        log.append({
            "action": action,
            "label": allowed[action]["label"],
            "applied_at": utc_now(),
            "rows_before": before_rows,
            "rows_after": len(result),
            "missing_before": before_missing,
            "missing_after": int(result.isna().sum().sum()),
        })
    return result, log


def semantic_manifest(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "version": 1,
        "generated_at": utc_now(),
        "row_count": len(frame),
        "columns": [
            {
                "name": str(name),
                "normalized_name": normalize_text(str(name)).replace(" ", "_"),
                "type": column_kind(frame[name]),
                "nullable": bool(frame[name].isna().any()),
                "description": f"Colonne {column_kind(frame[name])} importée depuis le jeu de données.",
            }
            for name in frame.columns
        ],
        "security": {
            "mode": "read_only",
            "external_transmission": False,
            "allowed_operations": ["count", "mean", "sum", "min", "max", "top_values"],
        },
    }


def _date_series(series: pd.Series, name: str) -> pd.Series | None:
    """Reconnaît prudemment une colonne de dates sans modifier les données sources."""
    if pd.api.types.is_datetime64_any_dtype(series):
        parsed = pd.to_datetime(series, errors="coerce")
        return parsed if parsed.notna().any() else None
    if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return None

    # La décision se fait sur un échantillon borné ; la conversion complète
    # n'intervient qu'après avoir reconnu une vraie colonne de dates.
    sample = series.dropna().head(2_000).astype("string").str.strip()
    sample = sample[sample.ne("")].head(500)
    if len(sample) < 3:
        return None
    if pd.to_numeric(sample.str.replace(",", ".", regex=False), errors="coerce").notna().mean() >= 0.8:
        return None
    name_tokens = normalize_text(str(name)).split()
    hint = any(token in name_tokens for token in (
        "date", "jour", "mois", "annee", "heure", "time", "created", "updated", "timestamp",
    ))
    # Évite de lancer un parseur de dates coûteux sur chaque colonne de texte
    # (codes, noms, commentaires…). Sans indice dans le titre, on ne continue
    # que si la grande majorité de l'échantillon a bien la forme d'une date.
    if not hint:
        date_shape = sample.str.match(
            r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}(?:[ T].*)?$", na=False
        )
        if float(date_shape.mean()) < 0.75:
            return None
    parsed_sample = pd.to_datetime(sample, errors="coerce", dayfirst=True, format="mixed")
    threshold = 0.75 if hint else 0.95
    if float(parsed_sample.notna().mean()) < threshold:
        return None
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    return parsed if parsed.notna().any() else None


def restore_semantic_types(frame: pd.DataFrame) -> pd.DataFrame:
    """Restaure les dates d'un CSV nettoyé pour les analyses et graphiques."""
    restored = frame.copy()
    for name in restored.columns:
        parsed = _date_series(restored[name], str(name))
        if parsed is not None:
            restored[name] = parsed
    return restored


def _time_groups(dates: pd.Series) -> tuple[pd.Series, str]:
    valid = dates.dropna()
    span_days = int((valid.max() - valid.min()).days) if len(valid) > 1 else 0
    if span_days <= 62:
        period, label = "D", "jour"
    elif span_days <= 730:
        period, label = "W", "semaine"
    else:
        period, label = "M", "mois"
    return dates.dt.to_period(period), label


def _chart_values(values: pd.Series) -> list[int | float]:
    result: list[int | float] = []
    for value in values.tolist():
        number = float(value)
        result.append(int(number) if number.is_integer() else round(number, 2))
    return result


def _chart_config(
    *,
    title: str,
    labels: list[str],
    values: list[int | float],
    chart_type: str = "bar",
    dataset_label: str = "Nombre de lignes",
    color: str | list[str] = "#5b5bd6",
    subtitle: str = "",
) -> dict[str, Any]:
    return {
        "type": chart_type,
        "title": title,
        "subtitle": subtitle,
        "labels": labels,
        "values": values,
        "dataset_label": dataset_label,
        "color": color,
        "index_axis": "y" if chart_type == "bar" and (len(labels) > 8 or any(len(v) > 14 for v in labels)) else "x",
    }


def build_dashboard(frame: pd.DataFrame, *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """Génère des graphiques automatiques adaptés aux types et distributions."""
    profile = profile if profile is not None else profile_dataframe(frame)
    charts: list[dict[str, Any]] = []
    date_columns: set[str] = set()

    for name in frame.columns:
        dates = _date_series(frame[name], str(name))
        if dates is None:
            continue
        date_columns.add(str(name))
        groups, period_label = _time_groups(dates)
        counts = groups.dropna().value_counts().sort_index().tail(24)
        if not counts.empty:
            charts.append(_chart_config(
                title=f"Évolution des lignes · {name}",
                labels=[str(value) for value in counts.index],
                values=[int(value) for value in counts.tolist()],
                chart_type="line",
                dataset_label="Nombre de lignes",
                subtitle=f"Regroupement par {period_label} · 24 périodes récentes maximum",
            ))
        break

    for name in frame.select_dtypes(include=["number"]).columns[:3]:
        values = pd.to_numeric(frame[name], errors="coerce").dropna()
        if values.empty or values.nunique() <= 1:
            continue
        unique = int(values.nunique())
        dominant_rate = float(values.value_counts(normalize=True).iloc[0])
        if unique <= 15 or dominant_rate >= 0.50:
            counts = values.value_counts().head(10).sort_index()
            title = f"Répartition des valeurs · {name}"
            labels = [f"{float(value):g}" for value in counts.index]
        else:
            bin_count = min(8, max(4, int(math.sqrt(len(values)))))
            counts_array, edges = np.histogram(values, bins=bin_count)
            counts = pd.Series(counts_array)
            labels = [f"{edges[i]:.2g}–{edges[i + 1]:.2g}" for i in range(len(counts_array))]
            title = f"Distribution · {name}"
        charts.append(_chart_config(
            title=title,
            labels=labels,
            values=[int(value) for value in counts.tolist()],
            dataset_label="Nombre de lignes",
        ))

    for name in frame.select_dtypes(include=["object", "string", "category", "bool"]).columns:
        if str(name) in date_columns:
            continue
        unique = int(frame[name].nunique(dropna=True))
        if unique <= 1 or unique > 50:
            continue
        counts = frame[name].astype("string").fillna("Non renseigné").value_counts().head(10)
        if counts.empty:
            continue
        chart_type = "doughnut" if len(counts) <= 6 else "bar"
        charts.append(_chart_config(
            title=f"Répartition · {name}",
            labels=[str(value) for value in counts.index.tolist()],
            values=[int(value) for value in counts.tolist()],
            chart_type=chart_type,
            dataset_label="Nombre de lignes",
            color=CHART_PALETTE[:len(counts)] if chart_type == "doughnut" else "#2dd4bf",
        ))
        if len(charts) >= 6:
            break

    # Les corrélations servent d'indices, pas d'étude exhaustive. Limiter le
    # calcul aux premières mesures disponibles évite une matrice coûteuse sur
    # les exports très larges tout en conservant cinq résultats lisibles.
    numeric = frame.select_dtypes(include=["number"]).iloc[:, :24]
    correlations: list[dict[str, Any]] = []
    if len(numeric.columns) >= 2:
        corr = numeric.corr(numeric_only=True)
        for i, left in enumerate(corr.columns):
            for right in corr.columns[i + 1:]:
                value = corr.loc[left, right]
                if pd.notna(value):
                    correlations.append({
                        "left": str(left), "right": str(right), "value": round(float(value), 3)
                    })
        correlations.sort(key=lambda item: abs(item["value"]), reverse=True)

    return {"profile": profile, "charts": charts[:6], "correlations": correlations[:5]}


def chart_builder_options(frame: pd.DataFrame) -> dict[str, list[dict[str, str]]]:
    """Liste les colonnes adaptées au constructeur de graphiques."""
    dimensions: list[dict[str, str]] = []
    measures: list[dict[str, str]] = []
    row_limit = max(50, int(len(frame) * 0.10))
    for name in frame.columns:
        safe_name = str(name)
        dates = _date_series(frame[name], safe_name)
        if dates is not None:
            dimensions.append({"name": safe_name, "kind": "date", "label": f"{safe_name} · date"})
            continue
        if pd.api.types.is_numeric_dtype(frame[name]):
            dimensions.append({"name": safe_name, "kind": "number", "label": f"{safe_name} · nombre"})
            measures.append({"name": safe_name, "kind": "number", "label": safe_name})
            continue
        unique = int(frame[name].nunique(dropna=True))
        if 1 < unique <= row_limit:
            dimensions.append({"name": safe_name, "kind": "category", "label": f"{safe_name} · catégorie"})
    return {"dimensions": dimensions, "measures": measures}


def build_custom_chart(
    frame: pd.DataFrame,
    dimension: str,
    measure: str = "",
    aggregation: str = "count",
    chart_type: str = "auto",
) -> dict[str, Any]:
    """Construit un graphique contrôlé à partir de choix validés."""
    if dimension not in frame.columns:
        raise DataPilotError("Choisissez une colonne à comparer.")
    if aggregation not in CHART_AGGREGATIONS:
        raise DataPilotError("Le calcul demandé n’est pas disponible.")
    if chart_type not in CHART_TYPES:
        raise DataPilotError("Le type de graphique demandé n’est pas disponible.")
    if aggregation != "count":
        if measure not in frame.columns:
            raise DataPilotError("Choisissez une valeur numérique à mesurer.")
        numeric_measure = pd.to_numeric(frame[measure], errors="coerce")
        if numeric_measure.notna().sum() == 0:
            raise DataPilotError("La valeur choisie ne contient pas de nombres utilisables.")
    else:
        numeric_measure = None

    dates = _date_series(frame[dimension], dimension)
    is_date = dates is not None
    working = pd.DataFrame({"dimension": frame[dimension]})
    if numeric_measure is not None:
        working["measure"] = numeric_measure

    period_label = ""
    if is_date:
        working["dimension"], period_label = _time_groups(dates)
    elif pd.api.types.is_numeric_dtype(frame[dimension]) and frame[dimension].nunique(dropna=True) > 20 and aggregation == "count":
        numeric_dimension = pd.to_numeric(frame[dimension], errors="coerce")
        dominant_rate = float(numeric_dimension.value_counts(normalize=True).iloc[0]) if numeric_dimension.notna().any() else 0
        if dominant_rate < 0.50:
            working["dimension"] = pd.cut(numeric_dimension, bins=10, duplicates="drop")

    working = working.dropna(subset=["dimension"])
    if working.empty:
        raise DataPilotError("Cette colonne ne contient pas assez de données pour créer un graphique.")

    if aggregation == "count":
        grouped = working.groupby("dimension", observed=True).size()
        calculation_label = "Nombre de lignes"
        title = f"Nombre de lignes par {dimension}"
    else:
        working = working.dropna(subset=["measure"])
        grouped = working.groupby("dimension", observed=True)["measure"].agg(aggregation)
        labels = {"mean": "Moyenne", "sum": "Somme", "median": "Médiane"}
        calculation_label = f"{labels[aggregation]} de {measure}"
        title = f"{calculation_label} par {dimension}"

    grouped = grouped.dropna()
    if grouped.empty:
        raise DataPilotError("Aucune valeur exploitable pour ce graphique.")

    subtitle = ""
    if is_date:
        grouped = grouped.sort_index().tail(24)
        subtitle = f"Regroupement par {period_label} · 24 périodes récentes maximum"
    else:
        grouped = grouped.sort_values(ascending=False)
        if len(grouped) > 12:
            remaining = grouped.iloc[12:]
            grouped = grouped.iloc[:12].copy()
            if aggregation in {"count", "sum"}:
                grouped.loc["Autres"] = remaining.sum()
            subtitle = "12 groupes principaux affichés"

    labels_list = [str(value) for value in grouped.index.tolist()]
    selected_type = chart_type
    if selected_type == "auto":
        selected_type = "line" if is_date else ("doughnut" if aggregation == "count" and len(grouped) <= 6 else "bar")
    color: str | list[str] = CHART_PALETTE[:len(grouped)] if selected_type == "doughnut" else "#5b5bd6"
    return _chart_config(
        title=title,
        labels=labels_list,
        values=_chart_values(grouped),
        chart_type=selected_type,
        dataset_label=calculation_label,
        color=color,
        subtitle=subtitle,
    )


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    # Conserve toutes les lettres Unicode afin que les questions en arabe ne
    # disparaissent pas pendant la normalisation.
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in normalized.lower()).split()
    )


def _countable_entity_column(frame: pd.DataFrame, question: str) -> tuple[str, str] | None:
    """Associe quelques formulations naturelles à une colonne identifiante.

    Cette petite couche reste déterministe et locale. Elle couvre les termes
    usuels (joueurs, clients, équipes…) sans faire croire à une compréhension
    générale du langage.
    """
    if not any(term in question for term in ("combien", "nombre de", "how many")):
        return None

    columns = {normalize_text(str(name)).replace(" ", "_"): str(name) for name in frame.columns}
    entities = {
        "joueurs": ("joueur", "joueurs", "player", "players", "footballeur", "footballeurs"),
        "clients": ("client", "clients", "customer", "customers"),
        "équipes": ("equipe", "equipes", "team", "teams", "club", "clubs"),
        "produits": ("produit", "produits", "product", "products", "article", "articles"),
    }
    for label, terms in entities.items():
        if not any(term in question.split() for term in terms):
            continue
        for term in terms:
            preferred = (f"{term}_id", f"{term}_name", term, f"{term}s")
            for candidate in preferred:
                if candidate in columns:
                    return columns[candidate], label
            matching = [name for normalized, name in columns.items() if term in normalized.split("_")]
            if matching:
                matching.sort(key=lambda name: ("_id" not in normalize_text(name).replace(" ", "_"), len(name)))
                return matching[0], label

    # Repli direct : « combien de villes ? » peut être relié à une colonne
    # portant ce nom, même si elle ne fait pas partie des exemples ci-dessus.
    for normalized, name in columns.items():
        tokens = [token for token in normalized.split("_") if len(token) > 2]
        if tokens and any(token in question.split() for token in tokens):
            return name, str(name).replace("_", " ")
    return None


def answer_question(frame: pd.DataFrame, question: str) -> dict[str, Any]:
    """Assistant local déterministe, uniquement en lecture.

    Cette couche prouve le contrat d'interrogation avant le branchement futur
    d'un LLM : aucune requête arbitraire ni modification n'est autorisée.
    """
    q = normalize_text(question)
    if not q:
        return {"ok": False, "answer": "Écrivez une question sur vos données.", "evidence": []}

    if any(token in q for token in ("combien de lignes", "nombre de lignes", "observations")):
        return {"ok": True, "answer": f"Le fichier contient {len(frame):,} lignes.",
                "evidence": ["Comptage direct des lignes"]}
    entity = _countable_entity_column(frame, q)
    if entity:
        column, label = entity
        count = int(frame[column].dropna().nunique())
        adjective = "différentes" if label == "équipes" else "différents"
        return {
            "ok": True,
            "answer": f"Le fichier contient {count:,} {label} {adjective}.".replace(",", " "),
            "evidence": [f"Comptage des valeurs distinctes de « {column} »"],
            "suggested_questions": [
                f"Quelles sont les principales valeurs de {column} ?",
                "Combien de lignes contient le fichier ?",
            ],
        }
    if any(token in q for token in ("combien de colonnes", "nombre de colonnes")):
        return {"ok": True, "answer": f"Le fichier contient {len(frame.columns)} colonnes.",
                "evidence": [", ".join(map(str, frame.columns))]}
    if "doublon" in q:
        count = int(frame.duplicated().sum())
        return {"ok": True, "answer": f"J’ai trouvé {count} ligne(s) dupliquée(s) exactement.",
                "evidence": ["Comparaison de toutes les colonnes"]}
    if any(token in q for token in ("manquant", "vide", "non renseigne")):
        missing = frame.isna().sum().sort_values(ascending=False)
        top = missing[missing.gt(0)].head(5)
        if top.empty:
            answer = "Aucune valeur manquante n’est détectée."
        else:
            answer = "Colonnes les plus incomplètes : " + ", ".join(
                f"{name} ({int(value)})" for name, value in top.items()
            ) + "."
        return {"ok": True, "answer": answer, "evidence": ["Comptage des valeurs nulles"]}

    if any(token in q for token in (
        "que faut il retenir", "a retenir", "resume", "synthese", "vue d ensemble",
        "apercu", "global", "principaux enseignements", "analyse le fichier",
        "ماذا يجب ان اعرف", "ملخص", "لخص", "نظره عامه", "اهم النتائج",
    )):
        profile = profile_dataframe(frame)
        missing_count = profile["missing"] + profile["empty_strings"]
        answer = (
            f"Votre fichier contient {profile['rows']:,} lignes et {profile['columns_count']} colonnes, "
            f"avec une qualité globale de {profile['quality_score']}/100. "
            f"J’ai détecté {missing_count} cellule(s) vide(s) et {profile['duplicates']} doublon(s)."
        )
        insights: list[str] = []
        suggested_questions: list[str] = [
            "Quelles valeurs sont manquantes ?",
            "Combien de doublons contient le fichier ?",
        ]

        numeric_columns = list(frame.select_dtypes(include=["number"]).columns)
        for name in numeric_columns[:2]:
            values = pd.to_numeric(frame[name], errors="coerce").dropna()
            if values.empty:
                continue
            insights.append(
                f"{name} : moyenne {float(values.mean()):,.2f}, "
                f"de {float(values.min()):,.2f} à {float(values.max()):,.2f}."
            )
            suggested_questions.append(f"Quelle est la moyenne de {name} ?")

        for name, series in _object_series(frame):
            populated = series.dropna()
            if populated.empty or populated.nunique() > 50:
                continue
            counts = populated.value_counts()
            top_value = str(counts.index[0])
            insights.append(
                f"Dans {name}, la valeur la plus fréquente est « {top_value} » "
                f"({int(counts.iloc[0])} ligne(s))."
            )
            suggested_questions.append(f"Quelle est la répartition de {name} ?")
            break

        cautions: list[str] = []
        if missing_count:
            cautions.append(f"{missing_count} cellule(s) vide(s) peuvent limiter certaines analyses.")
        if profile["duplicates"]:
            cautions.append(f"{profile['duplicates']} doublon(s) devraient être vérifiés.")
        if not cautions:
            cautions.append("Aucune anomalie évidente n’est détectée, mais le sens métier des colonnes reste à confirmer.")

        return {
            "ok": True,
            "answer": answer,
            "evidence": ["Résumé calculé localement à partir du profil du fichier"],
            "insights": insights[:4],
            "cautions": cautions[:3],
            "suggested_questions": suggested_questions[:3],
        }

    if any(token in q for token in (
        "verifier", "controler", "probleme", "qualite", "fiable",
        "تحقق", "التحقق", "جوده", "مشاكل", "موثوق",
    )):
        profile = profile_dataframe(frame)
        missing_count = profile["missing"] + profile["empty_strings"]
        checks: list[str] = []
        if missing_count:
            checks.append(f"{missing_count} cellule(s) vide(s)")
        if profile["duplicates"]:
            checks.append(f"{profile['duplicates']} doublon(s)")
        constant_columns = [
            str(column["name"]) for column in profile["columns"] if column["unique"] <= 1
        ]
        if constant_columns:
            checks.append("colonne(s) sans variation : " + ", ".join(constant_columns[:3]))
        if checks:
            answer = "À vérifier en priorité : " + "; ".join(checks) + "."
        else:
            answer = "Aucun problème structurel majeur n’est détecté. Vérifiez surtout que les libellés et unités correspondent bien à votre activité."
        return {
            "ok": True,
            "answer": answer,
            "evidence": [f"Score qualité calculé localement : {profile['quality_score']}/100"],
            "insights": ["Les contrôles portent sur les cellules vides, doublons et colonnes sans variation."],
            "cautions": ["Un fichier techniquement propre peut encore contenir des valeurs incorrectes sur le plan métier."],
            "suggested_questions": ["Quelles valeurs sont manquantes ?", "Combien de doublons contient le fichier ?"],
        }

    matched: str | None = None
    for name in sorted(frame.columns, key=lambda value: len(str(value)), reverse=True):
        if normalize_text(str(name)) in q:
            matched = str(name)
            break
    if matched:
        series = frame[matched]
        numeric = pd.to_numeric(series, errors="coerce")
        if any(token in q for token in ("moyenne", "moyen")) and numeric.notna().any():
            value = float(numeric.mean())
            return {"ok": True, "answer": f"La moyenne de {matched} est {value:,.2f}.",
                    "evidence": [f"{int(numeric.notna().sum())} valeurs numériques utilisées"]}
        if "somme" in q and numeric.notna().any():
            value = float(numeric.sum())
            return {"ok": True, "answer": f"La somme de {matched} est {value:,.2f}.",
                    "evidence": [f"{int(numeric.notna().sum())} valeurs numériques utilisées"]}
        if any(token in q for token in ("maximum", "plus grand", "max")) and numeric.notna().any():
            return {"ok": True, "answer": f"Le maximum de {matched} est {float(numeric.max()):,.2f}.",
                    "evidence": ["Maximum calculé sur les valeurs numériques"]}
        if any(token in q for token in ("minimum", "plus petit", "min")) and numeric.notna().any():
            return {"ok": True, "answer": f"Le minimum de {matched} est {float(numeric.min()):,.2f}.",
                    "evidence": ["Minimum calculé sur les valeurs numériques"]}
        if any(token in q for token in ("top", "frequent", "repartition", "catégorie", "categorie")):
            counts = series.astype("string").fillna("Non renseigné").value_counts().head(5)
            answer = "; ".join(f"{value}: {int(count)}" for value, count in counts.items())
            return {"ok": True, "answer": f"Principales valeurs de {matched} — {answer}.",
                    "evidence": ["Top 5 par fréquence"]}

    return {
        "ok": False,
        "answer": "Je ne peux pas encore répondre de façon fiable à cette question.",
        "evidence": [],
        "suggested_questions": [
            "Combien de lignes contient le fichier ?",
            "Quelles valeurs sont manquantes ?",
            "Que dois-je vérifier ?",
        ],
    }


def safe_spreadsheet_text(value: str) -> str:
    """Neutralise une formule potentielle sans altérer un nombre signé."""
    candidate = value.lstrip()
    if not candidate.startswith(("=", "+", "-", "@")):
        return value
    compact = candidate.replace("\u202f", "").replace("\xa0", " ").replace(" ", "")
    if re.fullmatch(r"[+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?", compact):
        return value
    return "'" + value


def safe_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Neutralise les formules tableur potentielles dans les exports."""
    safe = frame.copy()
    safe.columns = [
        safe_spreadsheet_text(name) if isinstance(name, str) else name
        for name in safe.columns
    ]
    for name, series in _object_series(safe):
        safe[name] = series.map(
            lambda value: safe_spreadsheet_text(value) if isinstance(value, str) else value
        )
    return safe
