# -*- coding: utf-8 -*-
"""Renommages contrôlés et explicables pour DataPilot.

Les opérations de ce module sont atomiques : si une règle est invalide, une
copie inchangée du DataFrame est retournée. Aucun appel externe n'est effectué.
Les rapports ne contiennent que des types sérialisables en JSON.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def _is_missing(value: Any) -> bool:
    if value is None or value is pd.NA:
        return True
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def _json_value(value: Any) -> Any:
    """Convertit une étiquette ou valeur scalaire en représentation JSON sûre."""
    if _is_missing(value):
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _values_equal(left: Any, right: Any) -> bool:
    """Égalité de catégorie : valeurs manquantes ensemble, booléens distincts de 0/1."""
    left_missing, right_missing = _is_missing(left), _is_missing(right)
    if left_missing or right_missing:
        return left_missing and right_missing
    if isinstance(left, (bool, np.bool_)) or isinstance(right, (bool, np.bool_)):
        return isinstance(left, (bool, np.bool_)) and isinstance(right, (bool, np.bool_)) and bool(left) == bool(right)
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    try:
        result = left == right
    except (TypeError, ValueError):
        return False
    return bool(result) if isinstance(result, (bool, np.bool_)) else False


def _find_equal(values: list[Any], searched: Any) -> int | None:
    return next((index for index, value in enumerate(values) if _values_equal(value, searched)), None)


def _name_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value).strip()


def _name_key(value: Any) -> str:
    return unicodedata.normalize("NFKC", _name_text(value)).casefold()


def _column_positions(columns: list[Any], searched: Any) -> list[int]:
    return [index for index, column in enumerate(columns) if _values_equal(column, searched)]


def _base_report(operation: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "status": "rejected",
        "applied": False,
        "errors": [],
        "warnings": [],
    }


def _plan_column_renames(
    frame: pd.DataFrame,
    renames: Mapping[Any, Any],
) -> tuple[dict[str, Any], list[Any]]:
    report = _base_report("rename_columns")
    columns = list(frame.columns)
    final_columns = list(columns)
    report.update({
        "requested_count": 0,
        "applied_count": 0,
        "changes": [],
        "columns_before": [_json_value(column) for column in columns],
        "columns_after": [_json_value(column) for column in columns],
    })

    if not isinstance(renames, Mapping):
        report["errors"].append("Les renommages doivent associer chaque ancienne colonne à un nouveau nom.")
        return report, final_columns

    report["requested_count"] = len(renames)
    seen_positions: set[int] = set()
    planned: list[tuple[int, Any, str]] = []

    # Des colonnes déjà dupliquées rendent une sélection par nom ambiguë.
    duplicate_positions: set[int] = set()
    for index, column in enumerate(columns):
        peers = _column_positions(columns, column)
        if len(peers) > 1:
            duplicate_positions.update(peers)
    if duplicate_positions:
        duplicated = []
        for index in sorted(duplicate_positions):
            display = _json_value(columns[index])
            if display not in duplicated:
                duplicated.append(display)
        report["errors"].append(
            "Le fichier contient déjà des colonnes en double : "
            + ", ".join("valeur manquante" if value is None else str(value) for value in duplicated)
            + "."
        )
        return report, final_columns

    for source, requested_target in renames.items():
        positions = _column_positions(columns, source)
        if not positions:
            report["errors"].append(f"La colonne « {_json_value(source)} » n'existe pas.")
            continue
        position = positions[0]
        if position in seen_positions:
            report["errors"].append(
                f"La colonne « {_json_value(columns[position])} » est demandée plusieurs fois."
            )
            continue
        seen_positions.add(position)

        if _is_missing(requested_target):
            report["errors"].append(
                f"Le nouveau nom de « {_json_value(columns[position])} » ne peut pas être vide."
            )
            continue
        if not pd.api.types.is_scalar(requested_target):
            report["errors"].append(
                f"Le nouveau nom de « {_json_value(columns[position])} » doit être un texte court."
            )
            continue
        target = str(requested_target).strip()
        if not target:
            report["errors"].append(
                f"Le nouveau nom de « {_json_value(columns[position])} » ne peut pas être vide."
            )
            continue
        if not isinstance(requested_target, str):
            report["warnings"].append(f"Le nom {requested_target!s} a été converti en texte « {target} ».")
        planned.append((position, columns[position], target))
        final_columns[position] = target

    # Toutes les colonnes finales, y compris celles non modifiées, doivent être
    # affichables et distinctes dans l'interface.
    empty_positions = [index for index, column in enumerate(final_columns) if not _name_text(column)]
    if empty_positions:
        report["errors"].append(
            "Chaque colonne doit avoir un nom. Renommez les colonnes vides aux positions : "
            + ", ".join(str(index + 1) for index in empty_positions)
            + "."
        )

    names_by_key: dict[str, list[int]] = {}
    for index, column in enumerate(final_columns):
        names_by_key.setdefault(_name_key(column), []).append(index)
    collisions = [positions for key, positions in names_by_key.items() if key and len(positions) > 1]
    for positions in collisions:
        visible_name = _name_text(final_columns[positions[0]])
        report["errors"].append(
            f"Le nom « {visible_name} » serait utilisé par plusieurs colonnes "
            f"(positions {', '.join(str(position + 1) for position in positions)})."
        )

    if report["errors"]:
        return report, list(columns)

    changes: list[dict[str, Any]] = []
    for position, old_name, target in planned:
        if old_name == target and isinstance(old_name, str):
            report["warnings"].append(f"La colonne « {target} » porte déjà ce nom.")
            # On conserve l'objet original lorsqu'il s'agit d'un vrai no-op.
            final_columns[position] = old_name
            continue
        changes.append({
            "position": position + 1,
            "old_name": _json_value(old_name),
            "new_name": target,
        })

    report.update({
        "ok": True,
        "status": "ready",
        "applied_count": len(changes),
        "changes": changes,
        "columns_after": [_json_value(column) for column in final_columns],
    })
    return report, final_columns


def validate_column_renames(frame: pd.DataFrame, renames: Mapping[Any, Any]) -> dict[str, Any]:
    """Valide un plan atomique de renommage sans modifier le DataFrame."""
    report, _ = _plan_column_renames(frame, renames)
    return report


def apply_column_renames(
    frame: pd.DataFrame,
    renames: Mapping[Any, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Applique des noms validés et retourne ``(copie, rapport JSON)``."""
    report, final_columns = _plan_column_renames(frame, renames)
    result = frame.copy(deep=True)
    if not report["ok"]:
        return result, report
    result.columns = final_columns
    report = dict(report)
    report["status"] = "applied"
    report["applied"] = True
    return result, report


def _unique_categories(series: pd.Series) -> tuple[list[Any], list[int]]:
    categories: list[Any] = []
    counts: list[int] = []
    for value in series.tolist():
        index = _find_equal(categories, value)
        if index is None:
            categories.append(value)
            counts.append(1)
        else:
            counts[index] += 1
    return categories, counts


def _plan_category_replacements(
    frame: pd.DataFrame,
    column: Any,
    replacements: Mapping[Any, Any],
    *,
    allow_merge: bool,
) -> tuple[dict[str, Any], int | None, list[tuple[Any, Any]]]:
    report = _base_report("replace_category_values")
    report.update({
        "column": _json_value(column),
        "requested_count": 0,
        "planned_rows": 0,
        "applied_rows": 0,
        "unchanged_rows": int(len(frame)),
        "changes": [],
        "merges": [],
    })
    columns = list(frame.columns)
    positions = _column_positions(columns, column)
    if not positions:
        report["errors"].append(f"La colonne « {_json_value(column)} » n'existe pas.")
        return report, None, []
    if len(positions) > 1:
        report["errors"].append(f"La colonne « {_json_value(column)} » existe plusieurs fois et ne peut pas être choisie sans ambiguïté.")
        return report, None, []
    position = positions[0]
    actual_column = columns[position]
    report["column"] = _json_value(actual_column)

    if not isinstance(replacements, Mapping):
        report["errors"].append("Les remplacements doivent associer chaque ancienne valeur à une nouvelle valeur.")
        return report, position, []
    report["requested_count"] = len(replacements)

    series = frame.iloc[:, position]
    categories, category_counts = _unique_categories(series)
    planned: list[tuple[Any, Any]] = []
    source_indexes: set[int] = set()

    for source, target in replacements.items():
        source_index = _find_equal(categories, source)
        if source_index is None:
            report["errors"].append(
                f"La valeur « {_json_value(source)} » n'existe pas dans « {_json_value(actual_column)} »."
            )
            continue
        if source_index in source_indexes:
            report["errors"].append(
                f"La valeur « {_json_value(source)} » est demandée plusieurs fois."
            )
            continue
        source_indexes.add(source_index)
        if not pd.api.types.is_scalar(target):
            report["errors"].append(
                f"Le remplacement de « {_json_value(source)} » doit être une valeur simple."
            )
            continue
        planned.append((categories[source_index], target))

    if report["errors"]:
        return report, position, []

    final_values: list[Any] = []
    for original in categories:
        replacement = next((target for source, target in planned if _values_equal(source, original)), original)
        final_values.append(replacement)

    merge_groups: list[list[int]] = []
    handled: set[int] = set()
    for index, target in enumerate(final_values):
        if index in handled:
            continue
        peers = [candidate for candidate, value in enumerate(final_values) if _values_equal(value, target)]
        handled.update(peers)
        if len(peers) > 1:
            merge_groups.append(peers)

    merges = [
        {
            "target": _json_value(final_values[indexes[0]]),
            "sources": [_json_value(categories[index]) for index in indexes],
            "affected_rows": int(sum(category_counts[index] for index in indexes)),
        }
        for indexes in merge_groups
    ]
    report["merges"] = merges
    if merges and not allow_merge:
        report["errors"].append(
            "Ce remplacement regrouperait plusieurs catégories. Confirmez explicitement la fusion pour continuer."
        )
        return report, position, []
    if merges:
        report["warnings"].append(
            f"{len(merges)} fusion(s) de catégories seront appliquées comme demandé."
        )

    changes: list[dict[str, Any]] = []
    changed_rows = 0
    effective_plan: list[tuple[Any, Any]] = []
    for source, target in planned:
        category_index = _find_equal(categories, source)
        matched_rows = category_counts[category_index] if category_index is not None else 0
        if _values_equal(source, target):
            report["warnings"].append(
                f"La valeur « {_json_value(source)} » est déjà identique au remplacement demandé."
            )
            continue
        effective_plan.append((source, target))
        changed_rows += matched_rows
        changes.append({
            "old_value": _json_value(source),
            "new_value": _json_value(target),
            "matched_rows": int(matched_rows),
        })

    report.update({
        "ok": True,
        "status": "ready",
        "planned_rows": int(changed_rows),
        "unchanged_rows": int(len(frame) - changed_rows),
        "changes": changes,
    })
    return report, position, effective_plan


def validate_category_replacements(
    frame: pd.DataFrame,
    column: Any,
    replacements: Mapping[Any, Any],
    *,
    allow_merge: bool = False,
) -> dict[str, Any]:
    """Valide des traductions/clarifications de catégories sans les appliquer."""
    report, _, _ = _plan_category_replacements(
        frame, column, replacements, allow_merge=allow_merge
    )
    return report


def replace_category_values(
    frame: pd.DataFrame,
    column: Any,
    replacements: Mapping[Any, Any],
    *,
    allow_merge: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remplace uniquement les valeurs choisies et retourne ``(copie, rapport)``."""
    report, position, plan = _plan_category_replacements(
        frame, column, replacements, allow_merge=allow_merge
    )
    result = frame.copy(deep=True)
    if not report["ok"] or position is None:
        return result, report

    original = frame.iloc[:, position]
    updated_values: list[Any] = []
    for value in original.tolist():
        replacement = next((target for source, target in plan if _values_equal(source, value)), value)
        updated_values.append(replacement)
    # Le type objet accepte une traduction textuelle d'un code numérique ainsi
    # que les valeurs manquantes, sans conversion des autres colonnes.
    result.isetitem(position, pd.Series(updated_values, index=result.index, dtype="object"))

    report = dict(report)
    report["status"] = "applied"
    report["applied"] = True
    report["applied_rows"] = report["planned_rows"]
    return result, report


__all__ = [
    "validate_column_renames",
    "apply_column_renames",
    "validate_category_replacements",
    "replace_category_values",
]
