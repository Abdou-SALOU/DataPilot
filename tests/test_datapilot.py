from pathlib import Path

import pandas as pd
import pytest

import datapilot


def sample_frame():
    return pd.DataFrame({
        "ville": [" Casablanca ", "Casablanca", "Rabat", None, "Rabat"],
        "montant": ["10,5", "10,5", "20", "30", "20"],
        "note": [5, 5, None, 3, None],
    })


def test_business_brief_uses_observed_sales_and_discloses_limits():
    frame = pd.DataFrame({
        "produit": ["Café", "Café", "Thé", "Thé"],
        "montant": ["100,50", "50", "-10", "inconnu"],
    })
    brief = datapilot.build_business_brief(frame)
    assert brief["available"] is True
    assert brief["total"] == 140.5
    assert brief["used_rows"] == 3
    assert brief["excluded_rows"] == 1
    assert "Café" in brief["findings"][0]["detail"]
    assert any("bénéfice" in item for item in brief["limitations"])
    assert any("négatif" in item for item in brief["limitations"])


def test_business_brief_does_not_invent_sales_for_generic_dataset():
    brief = datapilot.build_business_brief(pd.DataFrame({"age": [20, 30]}))
    assert brief["available"] is False
    invoices = datapilot.build_business_brief(pd.DataFrame({"total_ttc": [100]}), source_kind="invoices")
    assert invoices["available"] is False


def test_expense_brief_does_not_recommend_promoting_an_expense():
    brief = datapilot.build_business_brief(
        pd.DataFrame({"produit": ["Loyer", "Électricité"], "montant": [500, 100]}),
        source_kind="captured_expenses",
    )
    assert brief["total_label"] == "Dépenses déclarées"
    assert brief["total"] == 600
    assert all("promotion" not in action for action in brief["actions"])


def test_read_csv_detects_separator(tmp_path: Path):
    path = tmp_path / "ventes.csv"
    path.write_text("ville;montant\nCasablanca;10\nRabat;20\n", encoding="utf-8")
    frame = datapilot.read_dataset(path)
    assert list(frame.columns) == ["ville", "montant"]
    assert len(frame) == 2


def test_profile_and_suggestions_are_explainable():
    frame = sample_frame()
    profile = datapilot.profile_dataframe(frame)
    suggestions = {item["id"] for item in datapilot.suggest_cleaning(frame)}
    assert profile["rows"] == 5
    assert profile["missing"] == 3
    assert "strip_text" in suggestions
    assert "convert_numeric::montant" in suggestions
    assert "fill_median::note" in suggestions


def test_cleaning_only_parses_dates_for_plausible_columns(monkeypatch):
    frame = pd.DataFrame({
        "ville": ["Casablanca", "Rabat", "Fès", "Marrakech"],
        "segment_client": ["Fidèle", "Nouveau", "Fidèle", "Occasionnel"],
        "date_vente": ["2026-01-01", "mars 2026", "2026/03/14", "30-06-26"],
    })
    parsed_columns = []
    original = datapilot._coerce_date_series

    def tracked(series):
        parsed_columns.append(series.name)
        return original(series)

    monkeypatch.setattr(datapilot, "_coerce_date_series", tracked)
    suggestions = {item["id"] for item in datapilot.suggest_cleaning(frame)}
    assert parsed_columns == ["date_vente"]
    assert "convert_date::date_vente" in suggestions


def test_selected_cleaning_only_is_applied():
    frame = sample_frame()
    cleaned, log = datapilot.apply_cleaning(
        frame, ["strip_text", "convert_numeric::montant", "fill_median::note"]
    )
    assert cleaned.loc[0, "ville"] == "Casablanca"
    assert cleaned["montant"].dtype.kind in "fi"
    assert cleaned["note"].isna().sum() == 0
    assert len(log) == 3


def test_sales_cleaning_harmonizes_labels_and_repairs_linked_amounts():
    frame = pd.DataFrame({
        "id_vente": ["CMD-1", "CMD-1", "CMD-2", "CMD-3", "CMD-4"],
        "date_vente": ["2026-01-01", "2026-01-01", "mars 2026", "2026/03/14", "30-06-26"],
        "ville": ["Casblanca", "Casblanca", "Casa", "Casablanca", "Fes"],
        "canal_vente": ["whatsapp", "whatsapp", "En magasin", "Boutique", "WhatsApp"],
        "type_client": ["Fidele", "Fidele", "Fidèle", "Nouveau", "Fidèle"],
        "categorie_produit": ["Epicerie", "Epicerie", "Épicerie", "Épicerie", "Épicerie"],
        "quantite": [2, 2, "3 unités", 0, 2],
        "prix_unitaire_mad": [50, 50, "58 MAD", 55, -24],
        "remise_mad": [0, 0, "5%", 999, 0],
        "frais_livraison_factures_mad": [0, 0, "Gratuit", 0, 0],
        "cout_livraison_mad": [7, 7, 9, 7, 7],
        "statut_commande": ["Livree", "Livree", "Livrée", "Annuléé", "Livrée"],
        "total_brut_mad": [100, 100, 174, 55, 48],
        "chiffre_affaires_net_mad": [100, 100, 165.3, 0, 48],
        "marge_brute_mad": [43, 43, 80.3, -7, 17],
        "note_satisfaction": [4, 4, "5/5", 9, 4],
    })
    suggestions = {item["id"] for item in datapilot.suggest_cleaning(frame)}
    assert "harmonize_values::ville" in suggestions
    assert "repair_sales_metrics" in suggestions
    assert "fill_mode::id_vente" not in suggestions

    cleaned, _ = datapilot.apply_cleaning(frame, suggestions)
    assert len(cleaned) == 4
    assert set(cleaned["ville"]) == {"Casablanca", "Fès"}
    assert set(cleaned["canal_vente"]) == {"Boutique", "WhatsApp"}
    assert set(cleaned["statut_commande"]) == {"Livrée", "Annulée"}
    assert cleaned["date_vente"].dtype.kind == "M"
    assert (cleaned["quantite"] > 0).all()
    assert (cleaned["prix_unitaire_mad"] >= 0).all()
    assert (cleaned["remise_mad"] <= cleaned["total_brut_mad"]).all()
    assert cleaned["note_satisfaction"].between(1, 5).all()
    assert ((cleaned["quantite"] * cleaned["prix_unitaire_mad"] - cleaned["total_brut_mad"]).abs() < 0.01).all()


def test_read_only_assistant_computes_evidence():
    frame = pd.DataFrame({"montant": [10, 20, 30], "ville": ["Casa", "Casa", "Rabat"]})
    answer = datapilot.answer_question(frame, "Quelle est la moyenne de montant ?")
    assert answer["ok"] is True
    assert "20.00" in answer["answer"]
    assert answer["evidence"]


def test_read_only_assistant_counts_players_from_a_natural_question():
    frame = pd.DataFrame({
        "player_id": [10, 11, 11, 12],
        "player_name": ["Amina", "Youssef", "Youssef", "Nora"],
        "goals": [1, 0, 2, 1],
    })
    answer = datapilot.answer_question(frame, "Combien y a t il de joueurs ?")
    assert answer["ok"] is True
    assert "3" in answer["answer"]
    assert "player_id" in answer["evidence"][0]


def test_read_only_assistant_summarizes_the_file():
    frame = pd.DataFrame({
        "montant": [10, 20, 30, 40],
        "ville": ["Casa", "Casa", "Rabat", None],
    })
    answer = datapilot.answer_question(frame, "Que faut-il retenir de ce fichier ?")
    assert answer["ok"] is True
    assert "4 lignes" in answer["answer"]
    assert "2 colonnes" in answer["answer"]
    assert answer["insights"]
    assert answer["suggested_questions"]


def test_read_only_assistant_keeps_arabic_questions():
    frame = pd.DataFrame({"montant": [10, 20], "ville": ["Casa", "Rabat"]})
    assert datapilot.normalize_text("لخّص هذا الملف")
    answer = datapilot.answer_question(frame, "لخّص هذا الملف")
    assert answer["ok"] is True
    assert "2 lignes" in answer["answer"]


def test_semantic_manifest_is_read_only():
    manifest = datapilot.semantic_manifest(pd.DataFrame({"ventes": [1, 2]}))
    assert manifest["security"]["mode"] == "read_only"
    assert manifest["security"]["external_transmission"] is False


def test_export_escapes_spreadsheet_formulas():
    safe = datapilot.safe_export_frame(
        pd.DataFrame({"=texte": ["=CMD()", "  @SUM(A1:A2)", "-114.0", "+12,50", "normal"]})
    )
    assert list(safe.columns) == ["'=texte"]
    assert safe.loc[0, "'=texte"].startswith("'")
    assert safe.loc[1, "'=texte"].startswith("'")
    assert safe.loc[2, "'=texte"] == "-114.0"
    assert safe.loc[3, "'=texte"] == "+12,50"


def test_dashboard_detects_dates_and_discrete_numbers():
    frame = pd.DataFrame({
        "date_avis": ["2025-01-01", "2025-02-01", "2025-03-01", "2025-03-15"],
        "note": [1, 2, 5, 5],
    })
    charts = datapilot.build_dashboard(frame)["charts"]
    assert any(chart["type"] == "line" and "date_avis" in chart["title"] for chart in charts)
    note_chart = next(chart for chart in charts if "note" in chart["title"])
    assert note_chart["labels"] == ["1", "2", "5"]


def test_custom_chart_computes_grouped_mean():
    frame = pd.DataFrame({
        "application": ["A", "A", "B"],
        "note": [2, 4, 5],
    })
    chart = datapilot.build_custom_chart(frame, "application", "note", "mean", "bar")
    assert chart["dataset_label"] == "Moyenne de note"
    assert dict(zip(chart["labels"], chart["values"])) == {"B": 5, "A": 3}


def test_custom_chart_chooses_doughnut_for_small_categories():
    frame = pd.DataFrame({"sentiment": ["positif", "négatif", "positif", "neutre"]})
    chart = datapilot.build_custom_chart(frame, "sentiment")
    assert chart["type"] == "doughnut"
    assert sum(chart["values"]) == 4


def test_custom_chart_rejects_unknown_column():
    with pytest.raises(datapilot.DataPilotError):
        datapilot.build_custom_chart(pd.DataFrame({"ville": ["Casa"]}), "inconnue")
