import json

import numpy as np
import pandas as pd

import rename_features


def assert_strict_json(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False)


def test_column_renames_are_trimmed_atomic_and_do_not_mutate_source():
    source = pd.DataFrame({"ville": ["Casa", "Rabat"], "ca": [10, 20]})
    result, report = rename_features.apply_column_renames(
        source, {"ville": " Ville ", "ca": "Chiffre d'affaires"}
    )
    assert list(source.columns) == ["ville", "ca"]
    assert list(result.columns) == ["Ville", "Chiffre d'affaires"]
    assert result.to_numpy().tolist() == source.to_numpy().tolist()
    assert report["ok"] is True
    assert report["status"] == "applied"
    assert report["applied_count"] == 2
    assert_strict_json(report)


def test_column_rename_rejects_empty_and_case_insensitive_collisions():
    source = pd.DataFrame({"ville": ["Casa"], "ventes": [10]})
    unchanged, empty_report = rename_features.apply_column_renames(source, {"ville": "   "})
    assert empty_report["ok"] is False
    assert list(unchanged.columns) == list(source.columns)

    unchanged, collision_report = rename_features.apply_column_renames(source, {"ventes": "VILLE"})
    assert collision_report["ok"] is False
    assert "plusieurs colonnes" in collision_report["errors"][0]
    assert list(unchanged.columns) == list(source.columns)


def test_column_rename_allows_an_atomic_swap():
    source = pd.DataFrame({"A": [1], "B": [2]})
    result, report = rename_features.apply_column_renames(source, {"A": "B", "B": "A"})
    assert report["ok"] is True
    assert list(result.columns) == ["B", "A"]
    assert result.iloc[0].tolist() == [1, 2]


def test_column_rename_can_remove_whitespace_from_an_existing_name():
    source = pd.DataFrame([[1]], columns=["  montant  "])
    result, report = rename_features.apply_column_renames(
        source, {"  montant  ": "montant"}
    )
    assert report["applied_count"] == 1
    assert list(result.columns) == ["montant"]


def test_numeric_and_missing_column_labels_can_be_given_clear_names():
    source = pd.DataFrame([["x", 2, "Casa"]], columns=[None, 2025, "ville"])
    result, report = rename_features.apply_column_renames(
        source, {None: "information", 2025: 2026}
    )
    assert report["ok"] is True
    assert list(result.columns) == ["information", "2026", "ville"]
    assert report["warnings"] == ["Le nom 2026 a été converti en texte « 2026 »."]
    assert_strict_json(report)


def test_an_unrenamed_missing_column_name_is_rejected():
    source = pd.DataFrame([[1, 2]], columns=[None, "valeur"])
    report = rename_features.validate_column_renames(source, {"valeur": "montant"})
    assert report["ok"] is False
    assert "positions : 1" in report["errors"][0]


def test_existing_duplicate_columns_are_rejected_as_ambiguous():
    source = pd.DataFrame([[1, 2]], columns=["statut", "statut"])
    report = rename_features.validate_column_renames(source, {"statut": "état"})
    assert report["ok"] is False
    assert "déjà des colonnes en double" in report["errors"][0]


def test_category_translation_only_changes_the_selected_column():
    source = pd.DataFrame({
        "statut": ["Open", "Closed", "Open"],
        "montant": [10, 20, 30],
        "note": [None, "à vérifier", "ok"],
    })
    result, report = rename_features.replace_category_values(
        source, "statut", {"Open": "Ouvert", "Closed": "Fermé"}
    )
    assert source["statut"].tolist() == ["Open", "Closed", "Open"]
    assert result["statut"].tolist() == ["Ouvert", "Fermé", "Ouvert"]
    pd.testing.assert_series_equal(result["montant"], source["montant"])
    pd.testing.assert_series_equal(result["note"], source["note"])
    assert report["applied_rows"] == 3
    assert report["unchanged_rows"] == 0
    assert_strict_json(report)


def test_category_dtype_is_safely_opened_for_new_translated_labels():
    source = pd.DataFrame({"statut": pd.Series(["Open", "Closed"], dtype="category")})
    result, report = rename_features.replace_category_values(
        source, "statut", {"Open": "Ouvert"}
    )
    assert report["ok"] is True
    assert result["statut"].tolist() == ["Ouvert", "Closed"]
    assert source["statut"].tolist() == ["Open", "Closed"]


def test_numeric_string_and_missing_categories_are_matched_without_confusion():
    source = pd.DataFrame({"code": [1, "1", 2, None, np.nan], "autre": list("abcde")})
    result, report = rename_features.replace_category_values(
        source,
        "code",
        {1: "Nombre un", "1": "Texte un", None: "Non renseigné"},
    )
    assert result["code"].tolist() == [
        "Nombre un", "Texte un", 2, "Non renseigné", "Non renseigné"
    ]
    assert result["autre"].tolist() == source["autre"].tolist()
    assert report["applied_rows"] == 4
    assert_strict_json(report)


def test_category_collision_requires_explicit_merge_confirmation():
    source = pd.DataFrame({"ville": ["Casa", "Casablanca", "Rabat"]})
    unchanged, rejected = rename_features.replace_category_values(
        source, "ville", {"Casa": "Casablanca"}
    )
    assert rejected["ok"] is False
    assert "Confirmez explicitement" in rejected["errors"][0]
    assert unchanged["ville"].tolist() == source["ville"].tolist()

    merged, accepted = rename_features.replace_category_values(
        source, "ville", {"Casa": "Casablanca"}, allow_merge=True
    )
    assert accepted["ok"] is True
    assert merged["ville"].tolist() == ["Casablanca", "Casablanca", "Rabat"]
    assert accepted["merges"] == [{
        "target": "Casablanca",
        "sources": ["Casa", "Casablanca"],
        "affected_rows": 2,
    }]


def test_many_to_one_normalization_is_reported_as_a_merge():
    source = pd.DataFrame({"ville": ["Casa", "CASA", "Rabat"]})
    result, report = rename_features.replace_category_values(
        source,
        "ville",
        {"Casa": "Casablanca", "CASA": "Casablanca"},
        allow_merge=True,
    )
    assert result["ville"].tolist() == ["Casablanca", "Casablanca", "Rabat"]
    assert report["merges"][0]["sources"] == ["Casa", "CASA"]
    assert report["planned_rows"] == 2


def test_unknown_category_is_rejected_without_partial_application():
    source = pd.DataFrame({"statut": ["A", "B"]})
    result, report = rename_features.replace_category_values(
        source, "statut", {"A": "Actif", "C": "Clôturé"}
    )
    assert report["ok"] is False
    assert result["statut"].tolist() == ["A", "B"]
    assert any("n'existe pas" in error for error in report["errors"])


def test_a_missing_target_is_json_safe_and_can_be_confirmed_as_a_merge():
    source = pd.DataFrame({"statut": ["Inconnu", None, "Actif"]})
    result, report = rename_features.replace_category_values(
        source, "statut", {"Inconnu": None}, allow_merge=True
    )
    assert pd.isna(result.loc[0, "statut"])
    assert pd.isna(result.loc[1, "statut"])
    assert report["changes"][0]["new_value"] is None
    assert_strict_json(report)


def test_validation_and_no_op_are_deterministic():
    source = pd.DataFrame({"statut": ["Actif", "Inactif"]})
    validation = rename_features.validate_category_replacements(
        source, "statut", {"Actif": "Actif"}
    )
    result, applied = rename_features.replace_category_values(
        source, "statut", {"Actif": "Actif"}
    )
    assert validation["ok"] is True
    assert validation["status"] == "ready"
    assert applied["ok"] is True
    assert applied["applied_rows"] == 0
    assert result.equals(source)
