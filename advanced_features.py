# -*- coding: utf-8 -*-
"""Fonctions avancées, locales et déterministes pour DataPilot.

Le module transforme des intentions simples en calculs contrôlés. Il ne lance
aucune requête libre, ne modifie jamais le DataFrame reçu et ne transmet aucune
donnée à un service externe. Toutes les valeurs retournées sont sérialisables
en JSON.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

import datapilot


# Les synonymes restent volontairement explicites. L'absence de rapprochement
# approximatif évite d'associer silencieusement deux notions métier différentes.
BUSINESS_SYNONYMS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "chiffre d affaires", "chiffre affaires", "ca", "ventes", "vente", "revenu", "revenus",
        "recette", "recettes", "sales", "revenue", "المبيعات", "الإيرادات",
        "رقم المعاملات",
    ),
    "amount": (
        "montant", "valeur", "prix", "amount", "value", "price", "المبلغ",
        "القيمة", "السعر",
    ),
    "cost": (
        "cout", "couts", "depense", "depenses", "charge", "charges", "cost",
        "costs", "التكلفة", "التكاليف", "المصاريف",
    ),
    "profit": (
        "benefice", "benefices", "marge", "profit", "profits", "margin",
        "الربح", "الأرباح", "الهامش",
    ),
    "quantity": (
        "quantite", "quantites", "qte", "volume", "unites", "quantity",
        "units", "الكمية", "الكميات", "الوحدات",
    ),
    "date": (
        "date", "jour", "semaine", "mois", "trimestre", "annee", "time",
        "day", "week", "month", "year", "التاريخ", "اليوم", "الأسبوع",
        "الشهر", "السنة",
    ),
    "region": (
        "region", "zone", "territoire", "province", "area", "territory",
        "المنطقة", "الجهة", "الإقليم",
    ),
    "city": (
        "ville", "commune", "city", "town", "المدينة", "البلدية",
    ),
    "customer": (
        "client", "clients", "customer", "customers", "acheteur", "acheteurs",
        "العميل", "العملاء", "الزبون", "الزبناء",
    ),
    "product": (
        "produit", "produits", "article", "articles", "service", "services",
        "product", "products", "المنتج", "المنتوج", "المنتجات",
    ),
    "channel": (
        "canal", "canaux", "channel", "channels", "reseau", "réseau",
        "القناة", "القنوات",
    ),
}

CONCEPT_LABELS = {
    "revenue": "ventes ou chiffre d'affaires",
    "amount": "montant ou prix",
    "cost": "coûts ou dépenses",
    "profit": "bénéfice ou marge",
    "quantity": "quantité ou volume",
    "date": "date ou période",
    "region": "région ou zone",
    "city": "ville",
    "customer": "client",
    "product": "produit ou service",
    "channel": "canal",
}

AGGREGATION_LABELS = {
    "count": "Nombre de lignes",
    "sum": "Somme",
    "mean": "Moyenne",
    "median": "Médiane",
    "min": "Minimum",
    "max": "Maximum",
}


def _normalized(value: Any) -> str:
    return datapilot.normalize_text(str(value))


def _contains_phrase(text: str, phrase: str) -> bool:
    """Cherche une suite de mots complète, sans faux positif par sous-chaîne."""
    haystack = _normalized(text).split()
    needle = _normalized(phrase).split()
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[index:index + width] == needle for index in range(len(haystack) - width + 1))


def _concepts_in_text(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for concept, terms in BUSINESS_SYNONYMS.items():
        # Les expressions les plus précises passent avant les mots courts.
        for term in sorted(terms, key=lambda item: len(_normalized(item).split()), reverse=True):
            if _contains_phrase(text, term):
                found[concept] = term
                break
    return found


def _column_concepts(column: str, kind: str | None = None) -> set[str]:
    # « Date_vente » décrit une date de vente, pas le montant des ventes. Le
    # type détecté du fichier est plus fiable que la présence d'un second mot.
    if kind == "date":
        return {"date"}
    return {
        concept
        for concept, terms in BUSINESS_SYNONYMS.items()
        if any(_contains_phrase(column, term) for term in terms)
    }


def resolve_business_columns(
    frame: pd.DataFrame,
    question: str,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Résout les mots métier français/arabe vers des colonnes réelles.

    ``aliases`` permet à une PME de définir son vocabulaire sous la forme
    ``{"nom_colonne": ["terme interne", ...]}``. Les colonnes inconnues sont
    ignorées et signalées, jamais inventées.
    """
    query = str(question or "").strip()
    columns = [str(column) for column in frame.columns]
    column_positions = {column: index for index, column in enumerate(columns)}
    warnings: list[str] = []

    safe_aliases: dict[str, list[str]] = {}
    if aliases:
        for requested_column, terms in aliases.items():
            column = next(
                (name for name in columns if _normalized(name) == _normalized(requested_column)),
                None,
            )
            if column is None:
                warnings.append(f"Le raccourci vers « {requested_column} » a été ignoré : cette colonne n'existe pas.")
                continue
            if isinstance(terms, str):
                safe_aliases[column] = [terms]
            elif isinstance(terms, Sequence):
                safe_aliases[column] = [str(term) for term in terms if str(term).strip()]
            else:
                warnings.append(f"Les raccourcis de « {column} » ont été ignorés : utilisez une liste de mots.")

    query_concepts = _concepts_in_text(query)
    kinds = _column_kinds(frame)
    matches: list[dict[str, Any]] = []
    resolved_concepts: set[str] = set()

    for column in columns:
        direct = _contains_phrase(query, column)
        custom_term = next(
            (term for term in safe_aliases.get(column, []) if _contains_phrase(query, term)),
            None,
        )
        shared_concepts = _column_concepts(column, kinds.get(column)).intersection(query_concepts)

        if direct:
            match = {
                "column": column,
                "confidence": 1.0,
                "matched_term": column,
                "reason": "Le nom de la colonne est écrit dans la question.",
            }
        elif custom_term is not None:
            match = {
                "column": column,
                "confidence": 0.95,
                "matched_term": custom_term,
                "reason": "Un raccourci métier défini pour ce fichier a été reconnu.",
            }
        elif shared_concepts:
            concept = sorted(shared_concepts)[0]
            resolved_concepts.update(shared_concepts)
            match = {
                "column": column,
                "confidence": 0.85,
                "matched_term": query_concepts[concept],
                "reason": f"Le terme correspond à la notion « {CONCEPT_LABELS[concept]} ».",
            }
        else:
            continue

        resolved_concepts.update(shared_concepts)
        matches.append(match)

    matches.sort(key=lambda item: (-item["confidence"], column_positions[item["column"]]))
    unresolved = [
        CONCEPT_LABELS[concept]
        for concept in query_concepts
        if concept not in resolved_concepts
        and not any(_contains_phrase(query, match["column"]) for match in matches)
    ]
    resolved_columns = [match["column"] for match in matches]

    if resolved_columns:
        message = "Colonne(s) comprise(s) : " + ", ".join(resolved_columns) + "."
    else:
        message = "Je n'ai pas reconnu de colonne. Utilisez son nom ou ajoutez un raccourci métier."

    return {
        "ok": bool(resolved_columns),
        "query": query,
        "columns": resolved_columns,
        "matches": matches,
        "unresolved": unresolved,
        "warnings": warnings,
        "message": message,
    }


def _column_kinds(frame: pd.DataFrame) -> dict[str, str]:
    options = datapilot.chart_builder_options(frame)
    kinds = {
        item["name"]: item["kind"]
        for group in ("dimensions", "measures")
        for item in options[group]
    }
    for column in frame.columns:
        name = str(column)
        if name not in kinds:
            kinds[name] = "number" if pd.api.types.is_numeric_dtype(frame[column]) else "category"
    return kinds


def _asked_aggregation(question: str) -> str | None:
    groups = (
        ("mean", ("moyenne", "moyen", "average", "mean", "متوسط", "المتوسط")),
        ("median", ("mediane", "median", "médiane", "الوسيط")),
        ("sum", ("somme", "total", "cumule", "cumul", "sum", "اجمالي", "إجمالي", "المجموع")),
        ("count", ("nombre", "combien", "compte", "count", "عدد", "كم")),
    )
    for aggregation, terms in groups:
        if any(_contains_phrase(question, term) for term in terms):
            return aggregation
    return None


def _asked_chart_type(question: str) -> str:
    groups = (
        ("line", ("courbe", "evolution", "évolution", "tendance", "chronologique", "line", "تطور", "منحنى")),
        ("doughnut", ("camembert", "anneau", "proportion", "parts", "doughnut", "دائري", "نسب")),
        ("bar", ("barre", "barres", "histogramme", "colonnes", "bar chart", "أعمدة")),
    )
    for chart_type, terms in groups:
        if any(_contains_phrase(question, term) for term in terms):
            return chart_type
    return "auto"


def _is_grouped_by(question: str, match: Mapping[str, Any]) -> bool:
    term = str(match.get("matched_term") or match["column"])
    return any(
        _contains_phrase(question, f"{marker} {term}")
        for marker in ("par", "selon", "en fonction de", "by", "حسب", "لكل")
    )


def suggest_chart_from_question(
    frame: pd.DataFrame,
    question: str,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Transforme une question simple en graphique contrôlé et explicable."""
    resolution = resolve_business_columns(frame, question, aliases)
    if not resolution["columns"]:
        return {
            "ok": False,
            "message": "Je ne sais pas encore quelles colonnes utiliser. Exemple : « total des ventes par région ».",
            "interpretation": None,
            "chart": None,
            "warnings": resolution["warnings"],
        }

    kinds = _column_kinds(frame)
    matches_by_column = {item["column"]: item for item in resolution["matches"]}
    mentioned = resolution["columns"]
    warnings = list(resolution["warnings"])
    aggregation = _asked_aggregation(question)
    chart_type = _asked_chart_type(question)

    explicit_dimensions = [
        column for column in mentioned if _is_grouped_by(question, matches_by_column[column])
    ]
    dimension_candidates = [column for column in mentioned if kinds[column] in {"date", "category"}]
    numeric_candidates = [column for column in mentioned if kinds[column] == "number"]

    dimension = explicit_dimensions[0] if explicit_dimensions else None
    if len(explicit_dimensions) > 1:
        warnings.append(f"Plusieurs groupes ont été mentionnés ; « {dimension} » a été retenu en premier.")
    if dimension is None and dimension_candidates:
        date_candidates = [column for column in dimension_candidates if kinds[column] == "date"]
        trend_requested = chart_type == "line"
        dimension = (date_candidates[0] if trend_requested and date_candidates else dimension_candidates[0])

    measure = next((column for column in numeric_candidates if column != dimension), "")
    if len([column for column in numeric_candidates if column != dimension]) > 1:
        warnings.append(f"Plusieurs mesures ont été reconnues ; « {measure} » a été retenue en premier.")
    if aggregation is None:
        aggregation = "sum" if measure else "count"

    if aggregation == "count":
        measure = ""

    if dimension is None and measure and chart_type == "line":
        date_columns = [name for name, kind in kinds.items() if kind == "date"]
        if date_columns:
            dimension = date_columns[0]
            warnings.append(f"La période « {dimension} » a été choisie automatiquement pour montrer l'évolution.")

    if dimension is None and measure:
        automatic_dimensions = [name for name, kind in kinds.items() if kind in {"date", "category"}]
        if automatic_dimensions:
            dimension = automatic_dimensions[0]
            warnings.append(f"La colonne « {dimension} » a été choisie automatiquement pour comparer les valeurs.")
        else:
            # Sans groupe, le graphique le plus honnête est une distribution de
            # la mesure et non une somme artificiellement répétée.
            dimension, measure, aggregation, chart_type = measure, "", "count", "bar"
            warnings.append("Aucun groupe n'est disponible : la répartition des valeurs est affichée.")

    if dimension is None:
        dimension = mentioned[0]
        aggregation = "count"
        measure = ""

    if chart_type == "auto" and kinds.get(dimension) == "date":
        chart_type = "line"

    if chart_type == "doughnut" and int(frame[dimension].nunique(dropna=True)) > 8:
        chart_type = "bar"
        warnings.append("Les barres sont plus lisibles qu'un anneau lorsqu'il y a plus de 8 groupes.")

    try:
        chart = datapilot.build_custom_chart(
            frame,
            dimension=dimension,
            measure=measure,
            aggregation=aggregation,
            chart_type=chart_type,
        )
    except datapilot.DataPilotError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "interpretation": {
                "dimension": dimension,
                "measure": measure or None,
                "aggregation": aggregation,
                "chart_type": chart_type,
            },
            "chart": None,
            "warnings": warnings,
        }

    interpretation = {
        "dimension": dimension,
        "measure": measure or None,
        "aggregation": aggregation,
        "aggregation_label": AGGREGATION_LABELS[aggregation],
        "chart_type": chart["type"],
    }
    calculation = AGGREGATION_LABELS[aggregation].lower()
    measured_text = f" de « {measure} »" if measure else " des lignes"
    if aggregation == "count":
        proposal = f"le nombre de lignes par « {dimension} »"
    else:
        proposal = f"la {calculation}{measured_text} par « {dimension} »"
    return {
        "ok": True,
        "message": f"Je propose de montrer {proposal}.",
        "interpretation": interpretation,
        "chart": chart,
        "warnings": warnings,
        "evidence": ["Calcul local à partir des colonnes affichées dans l'interprétation."],
    }


def _safe_number(value: Any) -> int | float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Le calcul produit une valeur non finie.")
    return int(number) if number.is_integer() else round(number, 2)


def _format_number(value: int | float) -> str:
    number = float(value)
    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")
    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def explain_anomaly(
    frame: pd.DataFrame,
    dimension: str,
    category: str,
    measure: str | None = None,
    *,
    focus: Any | None = None,
    aggregation: str = "sum",
    top_n: int = 5,
) -> dict[str, Any]:
    """Décrit les catégories associées à un pic, sans inférer de causalité.

    Le niveau de référence est la moyenne des autres groupes de ``dimension``.
    ``focus=None`` sélectionne automatiquement le groupe au total le plus élevé.
    Les agrégations additives ``sum`` et ``count`` sont les seules acceptées afin
    que les contributions restent mathématiquement interprétables.
    """
    missing = [name for name in (dimension, category) if name not in frame.columns]
    if aggregation == "sum" and (not measure or measure not in frame.columns):
        missing.append(str(measure or "mesure"))
    if missing:
        return {
            "ok": False,
            "message": "Colonne(s) introuvable(s) : " + ", ".join(dict.fromkeys(missing)) + ".",
            "contributions": [],
        }
    if aggregation not in {"sum", "count"}:
        return {
            "ok": False,
            "message": "Pour expliquer un écart, choisissez une somme ou un nombre de lignes.",
            "contributions": [],
        }

    working = pd.DataFrame({
        "_dimension": frame[dimension].astype("string").fillna("Non renseigné"),
        "_category": frame[category].astype("string").fillna("Non renseigné"),
    })
    if aggregation == "sum":
        working["_value"] = pd.to_numeric(frame[measure], errors="coerce")
        working = working.dropna(subset=["_value"])
        if working.empty:
            return {
                "ok": False,
                "message": f"La colonne « {measure} » ne contient pas de nombres utilisables.",
                "contributions": [],
            }
        grouped = working.groupby(["_dimension", "_category"], observed=True)["_value"].sum().unstack(fill_value=0)
    else:
        grouped = working.groupby(["_dimension", "_category"], observed=True).size().unstack(fill_value=0)

    if len(grouped.index) < 2:
        return {
            "ok": False,
            "message": f"Il faut au moins deux valeurs dans « {dimension} » pour établir une comparaison.",
            "contributions": [],
        }

    totals = grouped.sum(axis=1)
    automatically_selected = focus is None
    if automatically_selected:
        focus_key = totals.idxmax()
    else:
        normalized_focus = _normalized(focus)
        focus_key = next((value for value in grouped.index if _normalized(value) == normalized_focus), None)
        if focus_key is None:
            return {
                "ok": False,
                "message": f"La valeur « {focus} » n'existe pas dans « {dimension} ».",
                "contributions": [],
            }

    other_groups = grouped.drop(index=focus_key)
    target_contributions = grouped.loc[focus_key]
    baseline_contributions = other_groups.mean(axis=0)
    differences = target_contributions - baseline_contributions
    target_value = _safe_number(totals.loc[focus_key])
    baseline_value = _safe_number(other_groups.sum(axis=1).mean())
    difference = _safe_number(float(target_value) - float(baseline_value))
    difference_percent = (
        round(float(difference) / abs(float(baseline_value)) * 100, 1)
        if float(baseline_value) != 0 else None
    )

    try:
        limit = max(1, min(int(top_n), 10))
    except (TypeError, ValueError):
        limit = 5
    contribution_items: list[dict[str, Any]] = []
    for category_value in differences.sort_values(ascending=False, kind="stable").index[:limit]:
        delta = _safe_number(differences.loc[category_value])
        contribution_items.append({
            "category": str(category_value),
            "value": _safe_number(target_contributions.loc[category_value]),
            "usual_value": _safe_number(baseline_contributions.loc[category_value]),
            "difference": delta,
            "direction": "hausse" if delta > 0 else ("baisse" if delta < 0 else "stable"),
        })

    if difference > 0:
        comparison = f"{_format_number(difference)} au-dessus"
    elif difference < 0:
        comparison = f"{_format_number(abs(float(difference)))} en dessous"
    else:
        comparison = "au même niveau que"
    summary = (
        f"« {focus_key} » atteint {_format_number(target_value)}, soit {comparison} "
        f"du niveau habituel de {_format_number(baseline_value)}."
    )

    return {
        "ok": True,
        "message": summary,
        "focus": str(focus_key),
        "focus_selected_automatically": automatically_selected,
        "metric": {
            "measure": measure if aggregation == "sum" else None,
            "aggregation": aggregation,
            "aggregation_label": AGGREGATION_LABELS[aggregation],
            "value": target_value,
            "usual_value": baseline_value,
            "difference": difference,
            "difference_percent": difference_percent,
        },
        "contributions": contribution_items,
        "method": f"Comparaison avec la moyenne des {len(other_groups)} autre(s) groupe(s) de « {dimension} ».",
        "caution": "Cette comparaison est descriptive : elle indique où l'écart apparaît, mais ne prouve pas qu'une catégorie en est la cause.",
    }


def calculate_kpi(
    frame: pd.DataFrame,
    measure: str | None = None,
    aggregation: str = "sum",
    target: int | float | None = None,
    favorable_direction: str = "higher",
) -> dict[str, Any]:
    """Calcule un indicateur simple et explique son statut face à un objectif."""
    aggregations = {"sum", "mean", "median", "min", "max", "count"}
    if aggregation not in aggregations:
        return {"ok": False, "message": "Calcul inconnu. Choisissez somme, moyenne, médiane, minimum, maximum ou nombre."}
    if aggregation != "count" and (not measure or measure not in frame.columns):
        return {"ok": False, "message": "Choisissez une colonne numérique existante pour cet indicateur."}
    if aggregation == "count" and measure is not None and measure not in frame.columns:
        return {"ok": False, "message": f"La colonne « {measure} » n'existe pas."}

    direction_aliases = {
        "higher": "higher", "higher_is_better": "higher", "plus": "higher", "hausse": "higher",
        "plus eleve": "higher", "maximiser": "higher", "augmenter": "higher",
        "lower": "lower", "lower_is_better": "lower", "moins": "lower", "baisse": "lower",
        "plus bas": "lower", "minimiser": "lower", "reduire": "lower",
    }
    direction = direction_aliases.get(_normalized(favorable_direction))
    if direction is None:
        return {"ok": False, "message": "Précisez si une valeur plus élevée ou plus basse est préférable."}

    if aggregation == "count":
        raw_value = len(frame) if measure is None else int(frame[measure].notna().sum())
        usable_values = int(raw_value)
    else:
        numeric = pd.to_numeric(frame[measure], errors="coerce").dropna()
        if numeric.empty:
            return {"ok": False, "message": f"La colonne « {measure} » ne contient pas de nombres utilisables."}
        raw_value = numeric.agg(aggregation)
        usable_values = int(len(numeric))

    try:
        value = _safe_number(raw_value)
    except (TypeError, ValueError):
        return {"ok": False, "message": "Le calcul de cet indicateur n'a pas produit de valeur utilisable."}

    if target is not None:
        try:
            if isinstance(target, bool):
                raise ValueError
            target_value = _safe_number(target)
        except (TypeError, ValueError):
            return {"ok": False, "message": "L'objectif doit être un nombre valide."}
    else:
        target_value = None

    measure_label = measure or "lignes"
    calculation_label = AGGREGATION_LABELS[aggregation]
    direction_label = (
        "Une valeur plus élevée est préférable."
        if direction == "higher" else "Une valeur plus basse est préférable."
    )

    if target_value is None:
        status_code, status_label, gap = "no_target", "À suivre", None
        summary = (
            f"{calculation_label} de « {measure_label} » : {_format_number(value)}. "
            "Ajoutez un objectif pour savoir si le résultat est satisfaisant."
        )
        difference = None
        achievement_percent = None
    else:
        difference = _safe_number(float(value) - float(target_value))
        reached = value >= target_value if direction == "higher" else value <= target_value
        status_code = "target_reached" if reached else "needs_attention"
        status_label = "Objectif atteint" if reached else "Objectif non atteint"
        raw_gap = (
            max(float(target_value) - float(value), 0)
            if direction == "higher" else max(float(value) - float(target_value), 0)
        )
        gap = _safe_number(raw_gap)
        achievement_percent = (
            round(float(value) / float(target_value) * 100, 1)
            if direction == "higher" and float(target_value) > 0 else None
        )
        relation = "atteint" if reached else "n'atteint pas"
        summary = (
            f"{calculation_label} de « {measure_label} » : {_format_number(value)}. "
            f"Le résultat {relation} l'objectif de {_format_number(target_value)}."
        )

    return {
        "ok": True,
        "message": summary,
        "kpi": {
            "measure": measure,
            "measure_label": measure_label,
            "aggregation": aggregation,
            "aggregation_label": calculation_label,
            "value": value,
            "target": target_value,
            "difference_from_target": difference,
            "gap_to_target": gap,
            "achievement_percent": achievement_percent,
            "favorable_direction": direction,
            "favorable_direction_label": direction_label,
            "status": status_code,
            "status_label": status_label,
            "usable_values": usable_values,
        },
        "evidence": [f"Calcul local sur {usable_values} valeur(s) utilisable(s)."],
    }


__all__ = [
    "BUSINESS_SYNONYMS",
    "resolve_business_columns",
    "suggest_chart_from_question",
    "explain_anomaly",
    "calculate_kpi",
]
