import json

import pandas as pd

import advanced_features


def business_frame():
    return pd.DataFrame({
        "Date_vente": ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"],
        "Chiffre_affaires": [100, 150, 80, 120],
        "Région": ["Casa", "Rabat", "Casa", "Rabat"],
        "Produit": ["A", "A", "B", "B"],
    })


def assert_strict_json(value):
    # allow_nan=False garantit que NaN et Infinity ne se glissent pas dans le contrat.
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def test_business_synonyms_resolve_french_and_custom_vocabulary():
    frame = business_frame()
    result = advanced_features.resolve_business_columns(
        frame,
        "Comparer le CA par zone commerciale",
        aliases={"Région": ["zone commerciale"]},
    )
    assert result["ok"] is True
    assert result["columns"] == ["Région", "Chiffre_affaires"]
    assert {item["confidence"] for item in result["matches"]} == {0.95, 0.85}
    assert_strict_json(result)


def test_business_synonyms_resolve_arabic_without_external_service():
    frame = business_frame()
    result = advanced_features.resolve_business_columns(
        frame, "اعرض المبيعات حسب المنطقة"
    )
    assert result["ok"] is True
    assert set(result["columns"]) == {"Chiffre_affaires", "Région"}
    assert all("column" in item and "reason" in item for item in result["matches"])


def test_short_ca_synonym_does_not_match_casablanca_inside_a_word():
    frame = pd.DataFrame({"ville": ["Casablanca"]})
    result = advanced_features.resolve_business_columns(frame, "Clients à Casablanca")
    assert result["columns"] == []


def test_chart_question_builds_a_controlled_sum_by_region():
    result = advanced_features.suggest_chart_from_question(
        business_frame(), "Montre le total du chiffre d'affaires par région en barres"
    )
    assert result["ok"] is True
    assert result["interpretation"] == {
        "dimension": "Région",
        "measure": "Chiffre_affaires",
        "aggregation": "sum",
        "aggregation_label": "Somme",
        "chart_type": "bar",
    }
    assert dict(zip(result["chart"]["labels"], result["chart"]["values"])) == {
        "Rabat": 270,
        "Casa": 180,
    }
    assert_strict_json(result)


def test_chart_question_uses_a_line_for_a_time_trend():
    result = advanced_features.suggest_chart_from_question(
        business_frame(), "Courbe de la moyenne du CA par date"
    )
    assert result["ok"] is True
    assert result["interpretation"]["dimension"] == "Date_vente"
    assert result["interpretation"]["aggregation"] == "mean"
    assert result["chart"]["type"] == "line"


def test_chart_question_asks_for_columns_instead_of_inventing_them():
    result = advanced_features.suggest_chart_from_question(
        business_frame(), "Montre-moi quelque chose d'intéressant"
    )
    assert result["ok"] is False
    assert result["chart"] is None
    assert "colonnes" in result["message"]


def test_anomaly_explanation_ranks_descriptive_category_contributions():
    frame = pd.DataFrame({
        "semaine": ["S1", "S1", "S2", "S2", "S3", "S3"],
        "canal": ["En ligne", "Magasin"] * 3,
        "ventes": [10, 20, 12, 20, 50, 22],
    })
    result = advanced_features.explain_anomaly(
        frame,
        dimension="semaine",
        category="canal",
        measure="ventes",
    )
    assert result["ok"] is True
    assert result["focus"] == "S3"
    assert result["metric"]["value"] == 72
    assert result["metric"]["usual_value"] == 31
    assert result["metric"]["difference"] == 41
    assert result["contributions"][0] == {
        "category": "En ligne",
        "value": 50,
        "usual_value": 11,
        "difference": 39,
        "direction": "hausse",
    }
    assert "ne prouve pas" in result["caution"]
    assert_strict_json(result)


def test_anomaly_explanation_needs_a_real_comparison_group():
    frame = pd.DataFrame({"mois": ["mai", "mai"], "canal": ["A", "B"], "ventes": [1, 2]})
    result = advanced_features.explain_anomaly(frame, "mois", "canal", "ventes")
    assert result["ok"] is False
    assert "au moins deux" in result["message"]


def test_anomaly_explanation_handles_an_invalid_display_limit_safely():
    frame = pd.DataFrame({
        "mois": ["mai", "mai", "juin", "juin"],
        "canal": ["A", "B", "A", "B"],
        "ventes": [1, 2, 5, 3],
    })
    result = advanced_features.explain_anomaly(
        frame, "mois", "canal", "ventes", top_n="invalide"
    )
    assert result["ok"] is True
    assert result["contributions"]


def test_kpi_reports_a_reached_higher_target_in_plain_french():
    frame = pd.DataFrame({"ventes": [40, 50, 30]})
    result = advanced_features.calculate_kpi(
        frame, "ventes", aggregation="sum", target=100, favorable_direction="higher"
    )
    assert result["ok"] is True
    assert result["kpi"]["value"] == 120
    assert result["kpi"]["status"] == "target_reached"
    assert result["kpi"]["status_label"] == "Objectif atteint"
    assert result["kpi"]["gap_to_target"] == 0
    assert "objectif de 100" in result["message"]
    assert_strict_json(result)


def test_kpi_understands_when_a_lower_value_is_better():
    frame = pd.DataFrame({"delai": [2, 3, 4]})
    result = advanced_features.calculate_kpi(
        frame, "delai", aggregation="mean", target=3.5, favorable_direction="lower"
    )
    assert result["kpi"]["value"] == 3
    assert result["kpi"]["status_label"] == "Objectif atteint"
    assert result["kpi"]["favorable_direction_label"] == "Une valeur plus basse est préférable."


def test_kpi_without_target_is_neutral_and_explains_the_next_step():
    result = advanced_features.calculate_kpi(
        pd.DataFrame({"client": ["A", "B", None]}),
        "client",
        aggregation="count",
    )
    assert result["kpi"]["value"] == 2
    assert result["kpi"]["status"] == "no_target"
    assert "Ajoutez un objectif" in result["message"]
