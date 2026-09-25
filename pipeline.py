# -*- coding: utf-8 -*-
"""Couche data engineering de DataPilot : médaillon, contrat de données et lineage.

Chaque projet reçoit un petit « lakehouse » local :

    lakehouse/bronze/data.parquet   données telles qu'ingérées (texte brut)
    lakehouse/silver/data.parquet   données typées et corrigées après validation
    lakehouse/gold/<table>.parquet  tables métier agrégées, prêtes pour la BI
    lakehouse/contract.json         contrat de données inféré (schéma + règles)
    lakehouse/_runs.jsonl           journal append-only des exécutions (lineage)

Les contrôles qualité sont évalués sur Bronze puis sur Silver : l'écart montre
ce que le nettoyage a réellement apporté. Tout reste local et reproductible.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher
from typing import Any, Callable

import numpy as np
import pandas as pd

import datapilot


LAKEHOUSE = "lakehouse"
RUNS_FILE = "_runs.jsonl"
MAX_RUNS_KEPT = 50
SQL_ROW_LIMIT = 200

ID_TOKENS = {"id", "sku", "reference", "ref", "numero", "code_commande", "order_id", "invoice_id"}
AMOUNT_TOKENS = {
    "montant", "amount", "total", "prix", "price", "revenue", "chiffre", "ventes", "sales",
    "cout", "cost", "marge", "margin", "remise", "discount", "frais", "tva", "ttc", "ht",
}
QUANTITY_TOKENS = {"quantite", "quantity", "qte", "qty"}
NON_NEGATIVE_TOKENS = QUANTITY_TOKENS | {"prix", "price", "frais", "cout", "cost", "delai"}
RATING_TOKENS = {"note", "rating", "satisfaction", "stars", "etoiles"}
DATE_TOKENS = {"date", "jour", "mois", "annee", "time", "created", "updated", "timestamp"}
DIMENSION_PRIORITY = (
    "produit", "product", "categorie_produit", "categorie", "category", "ville", "city",
    "canal_vente", "canal", "channel", "fournisseur", "supplier", "client",
)
AMOUNT_PRIORITY = (
    "chiffre_affaires_net_mad", "chiffre_affaires_net", "net_revenue", "montant", "amount",
    "total_ttc", "total", "ventes", "sales", "revenue", "total_brut_mad",
)


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _key(name: object) -> str:
    return datapilot.normalize_text(str(name)).replace(" ", "_")


def _tokens(name: object) -> set[str]:
    key = _key(name)
    return set(key.split("_")) | {key}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def lakehouse_dir(project_dir: Path) -> Path:
    return project_dir / LAKEHOUSE


def _is_id_column(name: object) -> bool:
    key = _key(name)
    tokens = _tokens(name)
    return key in ID_TOKENS or key.startswith("id_") or key.endswith("_id") or bool(tokens & {"sku"})


def _is_amount_column(name: object) -> bool:
    return bool(_tokens(name) & AMOUNT_TOKENS) and not _is_id_column(name)


def _is_quantity_column(name: object) -> bool:
    return bool(_tokens(name) & QUANTITY_TOKENS)


def _is_date_column(name: object) -> bool:
    return bool(_tokens(name) & DATE_TOKENS)


def _text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(text.eq(""))


def _parquet_ready(frame: pd.DataFrame) -> pd.DataFrame:
    """Parquet exige des types homogènes : les colonnes mixtes deviennent du texte."""
    prepared = frame.copy()
    for name in prepared.columns:
        series = prepared[name]
        if pd.api.types.is_object_dtype(series):
            prepared[name] = series.map(lambda v: None if datapilot._is_missing(v) else str(v)).astype("string")
    prepared.columns = [str(column) for column in prepared.columns]
    return prepared


def _write_parquet(frame: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _parquet_ready(frame).to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path.stat().st_size


# ---------------------------------------------------------------------------
# Contrat de données et contrôles qualité
# ---------------------------------------------------------------------------

def infer_contract(frame: pd.DataFrame) -> dict[str, Any]:
    """Déduit un contrat à partir des noms de colonnes et de leur contenu observé.

    Les règles s'appuient d'abord sur la sémantique du nom (un « montant » doit
    être numérique, un « id » unique…) pour ne pas valider mécaniquement des
    données fausses simplement parce qu'elles existent.
    """
    columns: list[dict[str, Any]] = []
    for name in frame.columns:
        series = frame[name]
        text = _text(series)
        populated = text.dropna()
        numeric_rate = float(datapilot._coerce_numeric_series(populated.head(2000)).notna().mean()) if len(populated) else 0.0
        if pd.api.types.is_datetime64_any_dtype(series) or _is_date_column(name):
            expected = "date"
        elif pd.api.types.is_numeric_dtype(series) or _is_amount_column(name) or _is_quantity_column(name) or (numeric_rate >= 0.9 and not _is_id_column(name)):
            expected = "integer" if _is_quantity_column(name) else "decimal"
        else:
            expected = "text"
        unique = int(populated.nunique())
        spec: dict[str, Any] = {
            "name": str(name),
            "type": expected,
            "required": _is_id_column(name) or _is_amount_column(name) or expected == "date",
            "unique": _is_id_column(name) and not bool(_tokens(name) & {"sku"}),
            "min": 0 if (bool(_tokens(name) & (NON_NEGATIVE_TOKENS | RATING_TOKENS)) and expected != "text") else None,
            "max": _rating_scale(populated) if (_tokens(name) & RATING_TOKENS and expected != "text") else None,
            "categorical": expected == "text" and 2 <= unique <= 30 and not _is_id_column(name),
        }
        columns.append(spec)
    return {
        "version": 1,
        "generated_at": utc_now(),
        "columns": columns,
        "table": {"no_duplicate_rows": True, "min_rows": 1},
    }


def _rating_scale(populated: pd.Series) -> int | None:
    """Une note est presque toujours sur 5 ou sur 10 : on retient l'échelle majoritaire."""
    values = datapilot._coerce_numeric_series(populated).dropna()
    if values.empty:
        return None
    return 5 if float((values <= 5).mean()) >= 0.8 else 10 if float((values <= 10).mean()) >= 0.8 else None


def _check(check_id: str, column: str | None, rule: str, label: str, failing: int, total: int,
           *, warn_ratio: float = 0.0, detail: str = "", severity: str = "error") -> dict[str, Any]:
    rate = 1 - failing / total if total else 1.0
    if failing == 0:
        status = "pass"
    elif severity == "warning" or failing / max(total, 1) <= warn_ratio:
        status = "warn"
    else:
        status = "fail"
    return {
        "id": check_id,
        "column": column,
        "rule": rule,
        "label": label,
        "failing_rows": int(failing),
        "total_rows": int(total),
        "pass_rate": round(rate * 100, 1),
        "status": status,
        "detail": detail,
    }


def _label_variants(series: pd.Series) -> tuple[int, list[str]]:
    """Compte les libellés qui ne diffèrent que par la casse, les accents ou une faute proche."""
    text = _text(series).dropna()
    if text.empty:
        return 0, []
    counts = text.value_counts()
    # Même référentiel que le nettoyage : les libellés connus (Casablanca, Livrée…)
    # l'emportent, même lorsque la faute de frappe est la saisie la plus fréquente.
    known = datapilot._value_harmonization_map(series, series.name)
    if known:
        rows = int(text.isin(list(known)).sum())
        return rows, [f"{source} → {target}" for source, target in list(known.items())[:3]]
    canonical: dict[str, str] = {}
    variant_rows = 0
    examples: list[str] = []
    for value, count in counts.items():
        key = datapilot.normalize_text(str(value))
        match = canonical.get(key)
        if match is None:
            # Faute de frappe proche d'un libellé plus fréquent (ex. « Casblanca »).
            for known_key, known_value in canonical.items():
                if len(key) >= 5 and SequenceMatcher(None, key, known_key).ratio() >= 0.88:
                    match = known_value
                    break
        if match is None:
            canonical[key] = str(value)
        elif str(value) != match:
            variant_rows += int(count)
            if len(examples) < 3:
                examples.append(f"{value} → {match}")
    return variant_rows, examples


def run_checks(frame: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Évalue le contrat sur un DataFrame (Bronze texte ou Silver typé)."""
    total = int(len(frame))
    checks: list[dict[str, Any]] = []
    duplicates = int(frame.astype("string").duplicated().sum()) if total else 0
    checks.append(_check("table.no_duplicates", None, "unique_rows", "Aucune ligne en double", duplicates, total,
                         detail=f"{duplicates} doublon(s) exact(s)"))
    today = pd.Timestamp.now().normalize()
    for spec in contract.get("columns", []):
        name = spec["name"]
        if name not in frame.columns:
            checks.append(_check(f"{name}.exists", name, "exists", "Colonne présente", 1, 1))
            continue
        series = frame[name]
        text = _text(series)
        populated = text.notna()
        if spec.get("required"):
            missing = int((~populated).sum())
            checks.append(_check(f"{name}.not_null", name, "not_null", "Valeur obligatoire", missing, total,
                                 warn_ratio=0.02, detail=f"{missing} cellule(s) vide(s)"))
        if spec["type"] in {"decimal", "integer"}:
            numeric = series if pd.api.types.is_numeric_dtype(series) else datapilot._coerce_numeric_series(text)
            invalid = int((populated & pd.Series(numeric, index=frame.index).isna()).sum())
            checks.append(_check(f"{name}.type", name, "numeric", "Format numérique", invalid, int(populated.sum()),
                                 detail=f"{invalid} valeur(s) non numérique(s)"))
            if spec.get("min") is not None:
                values = pd.to_numeric(pd.Series(numeric, index=frame.index), errors="coerce")
                below = int((values < spec["min"]).sum())
                checks.append(_check(f"{name}.min", name, "min", f"Valeur ≥ {spec['min']}", below, int(values.notna().sum()),
                                     detail=f"{below} valeur(s) négative(s)"))
            if spec.get("max") is not None:
                values = pd.to_numeric(pd.Series(numeric, index=frame.index), errors="coerce")
                above = int((values > spec["max"]).sum())
                checks.append(_check(f"{name}.max", name, "max", f"Valeur ≤ {spec['max']}", above, int(values.notna().sum()),
                                     detail=f"{above} valeur(s) hors échelle"))
        elif spec["type"] == "date":
            dates = series if pd.api.types.is_datetime64_any_dtype(series) else datapilot._coerce_date_series(text)
            dates = pd.to_datetime(dates, errors="coerce")
            invalid = int((populated & dates.isna()).sum())
            checks.append(_check(f"{name}.type", name, "date", "Format de date", invalid, int(populated.sum()),
                                 detail=f"{invalid} date(s) illisible(s)"))
            future = int((dates > today + pd.Timedelta(days=1)).sum())
            old = int((dates < pd.Timestamp("2000-01-01")).sum())
            checks.append(_check(f"{name}.range", name, "date_range", "Date plausible", future + old, int(dates.notna().sum()),
                                 severity="warning", detail=f"{future} date(s) future(s), {old} avant 2000"))
        if spec.get("unique"):
            repeated = int(text.dropna().duplicated().sum())
            checks.append(_check(f"{name}.unique", name, "unique", "Identifiant unique", repeated, int(populated.sum()),
                                 detail=f"{repeated} identifiant(s) répété(s)"))
        if spec.get("categorical"):
            variants, examples = _label_variants(series)
            checks.append(_check(f"{name}.consistent", name, "consistent_labels", "Libellés cohérents", variants, int(populated.sum()),
                                 detail=", ".join(examples) if examples else "Aucune variante"))
    return checks


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in checks if item["status"] == "pass")
    warned = sum(1 for item in checks if item["status"] == "warn")
    failed = sum(1 for item in checks if item["status"] == "fail")
    total = len(checks)
    return {
        "total": total,
        "passed": passed,
        "warnings": warned,
        "failed": failed,
        "score": round((passed + 0.5 * warned) / total * 100) if total else 100,
    }


# ---------------------------------------------------------------------------
# Tables Gold
# ---------------------------------------------------------------------------

def _pick(frame: pd.DataFrame, priority: tuple[str, ...], predicate: Callable[[str], bool] | None = None) -> str | None:
    keys = {_key(name): str(name) for name in frame.columns}
    for wanted in priority:
        if wanted in keys:
            return keys[wanted]
    if predicate:
        return next((str(name) for name in frame.columns if predicate(str(name))), None)
    return None


def build_gold_tables(silver: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Prépare des tables métier stables, directement consommables par Power BI ou SQL."""
    tables: dict[str, pd.DataFrame] = {}
    profile_rows = []
    for name in silver.columns:
        series = silver[name]
        profile_rows.append({
            "column_name": str(name),
            "data_type": datapilot.column_kind(series),
            "non_null": int(series.notna().sum()),
            "completeness_pct": round(float(series.notna().mean() * 100), 1) if len(series) else 0.0,
            "distinct_values": int(series.nunique(dropna=True)),
        })
    tables["gold_column_profile"] = pd.DataFrame(profile_rows)

    amount = _pick(silver, AMOUNT_PRIORITY, _is_amount_column)
    dimension = _pick(silver, DIMENSION_PRIORITY)
    date_column = next((str(n) for n in silver.columns if pd.api.types.is_datetime64_any_dtype(silver[n])), None)
    if date_column is None:
        date_column = next((str(n) for n in silver.columns if _is_date_column(n)), None)

    values = None
    if amount is not None:
        values = silver[amount] if pd.api.types.is_numeric_dtype(silver[amount]) else datapilot._coerce_numeric_series(silver[amount])
        values = pd.to_numeric(values, errors="coerce")

    if dimension is not None:
        labels = _text(silver[dimension]).fillna("Non renseigné")
        if values is not None:
            grouped = pd.DataFrame({"dimension": labels, "value": values}).dropna(subset=["value"])
            table = grouped.groupby("dimension", dropna=False).agg(
                total=("value", "sum"), transactions=("value", "size"), average=("value", "mean"),
            ).reset_index().sort_values("total", ascending=False)
            positive_total = float(table["total"].clip(lower=0).sum()) or 1.0
            table["share_pct"] = (table["total"].clip(lower=0) / positive_total * 100).round(1)
            table[["total", "average"]] = table[["total", "average"]].round(2)
            table = table.rename(columns={"dimension": _key(dimension)})
            tables[f"gold_{_key(amount)[:24]}_by_{_key(dimension)[:24]}"] = table.reset_index(drop=True)
        else:
            counts = labels.value_counts().rename_axis(_key(dimension)).reset_index(name="rows")
            tables[f"gold_rows_by_{_key(dimension)[:24]}"] = counts

    if date_column is not None:
        dates = silver[date_column] if pd.api.types.is_datetime64_any_dtype(silver[date_column]) else datapilot._coerce_date_series(silver[date_column])
        dates = pd.to_datetime(dates, errors="coerce")
        if dates.notna().sum() >= 2:
            period = dates.dt.to_period("M").astype("string")
            frame = pd.DataFrame({"month": period})
            frame["value"] = values if values is not None else 1
            frame = frame.dropna(subset=["month"])
            monthly = frame.groupby("month").agg(
                total=("value", "sum"), transactions=("value", "size"),
            ).reset_index().sort_values("month")
            monthly["total"] = monthly["total"].round(2)
            monthly["mom_change_pct"] = (monthly["total"].pct_change() * 100).round(1)
            tables["gold_monthly_trend"] = monthly.replace([np.inf, -np.inf], np.nan)
    return tables


# ---------------------------------------------------------------------------
# Exécution du pipeline
# ---------------------------------------------------------------------------

def _stage(stages: list[dict[str, Any]], name: str, func: Callable[[], dict[str, Any]]) -> Any:
    started = time.perf_counter()
    result = func()
    stages.append({
        "name": name,
        "status": "success",
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        **{key: value for key, value in result.items() if key != "value"},
    })
    return result.get("value")


def run_pipeline(
    project_dir: Path,
    *,
    source_path: Path | None,
    bronze_path: Path,
    silver: pd.DataFrame,
    trigger: str,
    applied_actions: list[str] | None = None,
) -> dict[str, Any]:
    """Exécute Bronze → contrôles → Silver → Gold et journalise l'exécution."""
    root = lakehouse_dir(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    started_at = utc_now()
    started = time.perf_counter()
    stages: list[dict[str, Any]] = []
    source = source_path if source_path and source_path.exists() else bronze_path

    def ingest() -> dict[str, Any]:
        bronze = pd.read_csv(bronze_path, dtype="string", keep_default_na=False, encoding="utf-8")
        bronze = bronze.mask(bronze.eq(""))
        audited = bronze.copy()
        audited["_ingested_at"] = started_at
        audited["_source_file"] = source.name
        audited["_row_number"] = np.arange(1, len(bronze) + 1)
        size = _write_parquet(audited, root / "bronze" / "data.parquet")
        return {"value": bronze, "rows_in": int(len(bronze)), "rows_out": int(len(bronze)),
                "output": "bronze/data.parquet", "bytes": size}

    bronze = _stage(stages, "ingest_bronze", ingest)

    def validate() -> dict[str, Any]:
        contract = infer_contract(silver)
        (root / "contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
        before = run_checks(bronze, contract)
        return {"value": (contract, before), "rows_in": int(len(bronze)), "rows_out": int(len(bronze)),
                "output": "contract.json", "checks": summarize_checks(before)}

    contract, bronze_checks = _stage(stages, "validate_contract", validate)

    def transform() -> dict[str, Any]:
        typed = silver.copy()
        size = _write_parquet(typed, root / "silver" / "data.parquet")
        after = run_checks(typed, contract)
        return {"value": after, "rows_in": int(len(bronze)), "rows_out": int(len(typed)),
                "output": "silver/data.parquet", "bytes": size, "checks": summarize_checks(after),
                "actions": list(applied_actions or [])}

    silver_checks = _stage(stages, "transform_silver", transform)

    def aggregate() -> dict[str, Any]:
        gold_dir = root / "gold"
        if gold_dir.exists():
            for old in gold_dir.glob("*.parquet"):
                old.unlink()
        tables = build_gold_tables(silver)
        outputs = []
        for table_name, table in tables.items():
            _write_parquet(table, gold_dir / f"{table_name}.parquet")
            outputs.append({"name": table_name, "rows": int(len(table)), "columns": [str(c) for c in table.columns]})
        return {"value": outputs, "rows_in": int(len(silver)), "rows_out": int(sum(t["rows"] for t in outputs)),
                "output": "gold/", "tables": outputs}

    gold_tables = _stage(stages, "publish_gold", aggregate)

    run = {
        "run_id": run_id,
        "trigger": trigger,
        "started_at": started_at,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "status": "success",
        "input": {"file": source.name, "sha256": sha256_file(source), "bytes": source.stat().st_size},
        "stages": stages,
        "quality": {
            "bronze": summarize_checks(bronze_checks),
            "silver": summarize_checks(silver_checks),
        },
        "checks": {"bronze": bronze_checks, "silver": silver_checks},
        "gold_tables": gold_tables,
        "rows": {"bronze": int(len(bronze)), "silver": int(len(silver))},
    }
    _append_run(root, run)
    return run


def _append_run(root: Path, run: dict[str, Any]) -> None:
    path = root / RUNS_FILE
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines.append(json.dumps(run, ensure_ascii=False, default=str))
    path.write_text("\n".join(lines[-MAX_RUNS_KEPT:]) + "\n", encoding="utf-8")


def list_runs(project_dir: Path, limit: int = 10) -> list[dict[str, Any]]:
    path = lakehouse_dir(project_dir) / RUNS_FILE
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(runs))[:limit]


def latest_run(project_dir: Path) -> dict[str, Any] | None:
    runs = list_runs(project_dir, limit=1)
    return runs[0] if runs else None


# ---------------------------------------------------------------------------
# Lecture des couches et SQL en lecture seule
# ---------------------------------------------------------------------------

TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def available_tables(project_dir: Path) -> dict[str, Path]:
    root = lakehouse_dir(project_dir)
    tables: dict[str, Path] = {}
    for layer in ("bronze", "silver"):
        path = root / layer / "data.parquet"
        if path.exists():
            tables[layer] = path
    for path in sorted((root / "gold").glob("*.parquet")) if (root / "gold").exists() else []:
        if TABLE_NAME_RE.fullmatch(path.stem):
            tables[path.stem] = path
    return tables


def read_table(project_dir: Path, name: str, limit: int | None = None) -> pd.DataFrame:
    tables = available_tables(project_dir)
    if name not in tables:
        raise KeyError(name)
    frame = pd.read_parquet(tables[name])
    return frame.head(limit) if limit else frame


FORBIDDEN_SQL = re.compile(
    r"\b(attach|detach|copy|export|import|install|load|pragma|set|reset|call|create|insert|update|delete|"
    r"drop|alter|replace|truncate|checkpoint|vacuum|use|begin|commit|rollback|grant|revoke)\b",
    re.IGNORECASE,
)


class SQLError(ValueError):
    """Requête refusée ou invalide, message affichable."""


def validate_sql(query: str) -> str:
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        raise SQLError("Écrivez une requête SELECT.")
    if len(cleaned) > 4000:
        raise SQLError("La requête dépasse 4 000 caractères.")
    without_strings = re.sub(r"'(?:[^']|'')*'", "''", cleaned)
    if ";" in without_strings:
        raise SQLError("Une seule requête à la fois.")
    if not re.match(r"^\s*(select|with)\b", without_strings, re.IGNORECASE):
        raise SQLError("Seules les requêtes SELECT (ou WITH … SELECT) sont autorisées.")
    if FORBIDDEN_SQL.search(without_strings) or re.search(r"\b(read_|glob|getenv)\w*\s*\(", without_strings, re.IGNORECASE):
        raise SQLError("Cette requête utilise une instruction non autorisée en lecture seule.")
    return cleaned


def run_sql(project_dir: Path, query: str, limit: int = SQL_ROW_LIMIT) -> dict[str, Any]:
    """Exécute une requête SELECT sur les couches du projet, sans accès disque ni écriture."""
    import duckdb

    statement = validate_sql(query)
    tables = available_tables(project_dir)
    if not tables:
        raise SQLError("Aucune table n'est encore publiée pour ce projet.")
    frames = {name: pd.read_parquet(path) for name, path in tables.items()}
    connection = duckdb.connect(database=":memory:")
    try:
        for name, frame in frames.items():
            connection.register(name, frame)
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        started = time.perf_counter()
        try:
            relation = connection.execute(f"SELECT * FROM ({statement}) AS dp_query LIMIT {int(limit) + 1}")
            columns = [item[0] for item in relation.description]
            rows = relation.fetchall()
        except duckdb.Error as exc:
            message = str(exc).splitlines()[0]
            raise SQLError(f"Erreur SQL : {message[:240]}") from exc
        elapsed = round((time.perf_counter() - started) * 1000, 1)
    finally:
        connection.close()
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [[_sql_value(value) for value in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": elapsed,
        "tables": sorted(tables),
    }


def _sql_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        if np.isnan(value):
            return None
        return round(value, 4)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, (int, str, bool)):
        return value
    return str(value)


def table_schemas(project_dir: Path) -> list[dict[str, Any]]:
    """Décrit chaque table publiée (couche, colonnes, lignes) pour l'explorateur SQL."""
    result = []
    for name, path in available_tables(project_dir).items():
        try:
            import pyarrow.parquet as pq
            metadata = pq.ParquetFile(path)
            schema = metadata.schema_arrow
            columns = [{"name": field.name, "type": str(field.type)} for field in schema]
            rows = metadata.metadata.num_rows
        except Exception:
            frame = pd.read_parquet(path)
            columns = [{"name": str(c), "type": str(t)} for c, t in frame.dtypes.items()]
            rows = len(frame)
        layer = "gold" if name.startswith("gold_") else name
        result.append({"name": name, "layer": layer, "rows": int(rows), "columns": columns, "bytes": path.stat().st_size})
    return result


def read_contract(project_dir: Path) -> dict[str, Any]:
    path = lakehouse_dir(project_dir) / "contract.json"
    if not path.exists():
        return {"columns": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"columns": []}
