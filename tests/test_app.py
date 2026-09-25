import io
import json
import zipfile

import pandas as pd

import app as datapilot_app


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(datapilot_app, "STORAGE_ROOT", tmp_path / "storage")
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return datapilot_app.app.test_client()


def csrf(test_client):
    test_client.get("/")
    with test_client.session_transaction() as session:
        return session["_csrf"]


def test_csv_export_keeps_negative_numbers_numeric_and_blocks_formulas():
    assert datapilot_app.safe_export_text("-114.0") == "-114.0"
    assert datapilot_app.safe_export_text("+12,50") == "+12,50"
    assert datapilot_app.safe_export_text("-CMD()") == "'-CMD()"
    assert datapilot_app.safe_export_text("=2+2") == "'=2+2"
    assert datapilot_app.safe_export_text("  =2+2") == "'  =2+2"


def test_french_counts_are_grammatically_correct():
    assert datapilot_app.counted_fr(1, "graphique enregistré", "graphiques enregistrés") == "1 graphique enregistré"
    assert datapilot_app.counted_fr(2, "graphique enregistré", "graphiques enregistrés") == "2 graphiques enregistrés"


def test_local_name_suggestions_are_readable_and_keep_existing_choices():
    assert datapilot_app.suggested_display_name("prix_unitaire_mad") == "Prix unitaire (MAD)"
    assert datapilot_app.suggested_display_name("mode_paiement") == "Mode de paiement"
    assert datapilot_app.suggested_display_name("delai_livraison_jours") == "Délai de livraison (jours)"
    assert datapilot_app.suggested_display_name(
        "canal_vente",
        [{"column": "canal_vente", "display_name": "Vente"}],
    ) == "Vente"


def test_recent_projects_have_a_clear_date_label(tmp_path, monkeypatch):
    monkeypatch.setattr(datapilot_app, "STORAGE_ROOT", tmp_path / "storage")
    directory = tmp_path / "storage" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "project.json").write_text(
        json.dumps({
            "name": "ventes",
            "original_filename": "ventes.csv",
            "updated_at": "2026-07-19T02:35:00+00:00",
        }),
        encoding="utf-8",
    )
    (directory / "raw.csv").write_text("ville\nRabat\n", encoding="utf-8")
    projects = datapilot_app.recent_projects()
    assert projects[0]["date_label"] == "19/07/2026 · 02:35"


def test_home_responds(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    response = test_client.get("/")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Fichier brut local" not in page
    assert ">Privé<" not in page


def test_health_reports_queue_state_without_becoming_unavailable(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        datapilot_app.task_queue,
        "queue_health",
        lambda: {"enabled": True, "available": False, "state": "unavailable"},
    )
    response = test_client.get("/health")
    payload = response.get_json()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert payload["application"] == "ready"
    assert payload["queue"]["state"] == "unavailable"


def test_home_ignores_incomplete_or_corrupted_projects(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    storage = tmp_path / "storage"
    invalid_json = storage / ("a" * 32)
    invalid_json.mkdir(parents=True)
    (invalid_json / "project.json").write_text("{invalid", encoding="utf-8")
    (invalid_json / "raw.csv").write_text("ville\nRabat\n", encoding="utf-8")

    invalid_shape = storage / ("b" * 32)
    invalid_shape.mkdir()
    (invalid_shape / "project.json").write_text("[]", encoding="utf-8")
    (invalid_shape / "raw.csv").write_text("ville\nFès\n", encoding="utf-8")

    missing_data = storage / ("c" * 32)
    missing_data.mkdir()
    (missing_data / "project.json").write_text(
        json.dumps({"name": "incomplet", "updated_at": "2026-07-19T00:00:00+00:00"}),
        encoding="utf-8",
    )

    response = test_client.get("/")
    assert response.status_code == 200
    assert "incomplet" not in response.get_data(as_text=True)


def test_corrupted_project_uses_the_branded_error_page(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    directory = tmp_path / "storage" / ("d" * 32)
    directory.mkdir(parents=True)
    (directory / "project.json").write_text("[]", encoding="utf-8")
    response = test_client.get(f"/project/{'d' * 32}")
    page = response.get_data(as_text=True)
    assert response.status_code == 404
    assert "Analyse introuvable" in page
    assert "Revenir à l’accueil" in page


def test_post_without_csrf_is_blocked(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    response = test_client.post("/upload", data={})
    assert response.status_code == 400


def test_oversized_ajax_upload_returns_a_json_error(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    monkeypatch.setitem(datapilot_app.app.config, "MAX_CONTENT_LENGTH", 10)
    response = test_client.post(
        "/upload",
        data={"dataset": (io.BytesIO(b"ville\nCasablanca\n"), "grand.csv")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 413
    assert payload["error"]["code"] == "file_too_large"


def test_csv_upload_creates_project(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    response = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"ventes" in response.data
    assert "Tableau de bord automatique" in response.get_data(as_text=True)
    assert len(list((tmp_path / "storage").glob("*/project.json"))) == 1


def test_queued_upload_has_status_tracking_and_local_fallback(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    monkeypatch.setitem(datapilot_app.app.config, "TESTING", False)
    monkeypatch.setattr(
        datapilot_app.task_queue,
        "enqueue_project_import",
        lambda *args, **kwargs: (True, "queued"),
    )
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert upload.status_code == 302
    assert upload.headers["Location"].endswith("/processing")
    processing_url = upload.headers["Location"]
    project_url = processing_url.removesuffix("/processing")
    directory = next((tmp_path / "storage").glob("*"))
    metadata = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    assert metadata["processing_status"] == "queued"
    assert not (directory / "raw.csv").exists()

    status = test_client.get(project_url + "/status")
    assert status.status_code == 200
    assert status.headers["Cache-Control"] == "no-store"
    assert status.get_json()["status"] == "queued"
    page = test_client.get(processing_url).get_data(as_text=True)
    assert "Analyse de votre fichier en cours" in page
    assert "Analyser maintenant sur cet ordinateur" in page

    local = test_client.post(
        processing_url + "/run-local",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert local.status_code == 302
    assert local.headers["Location"] == project_url
    metadata = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    assert metadata["processing_status"] == "ready"
    assert (directory / "raw.csv").exists()


def test_project_keeps_only_the_guided_rename_workspace(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"player_name,team\nAmina,Atlas\nYoussef,Raja\n"), "joueurs.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    overview_page = test_client.get(project_url).get_data(as_text=True)
    page = test_client.get(project_url + "/names").get_data(as_text=True)
    assert 'class="meta-row"' in page
    assert page.count("joueurs.csv") == 1
    assert '<select id="translation-column" name="column" required>' not in overview_page
    assert '<select id="translation-column" name="column" required>' in page
    assert "data-translation-column-card" not in page
    assert "Simplifiez un nom compliqué" in page
    assert "Quel nom est difficile à comprendre ?" in page
    assert "Quel nom préférez-vous ?" in page
    assert "Vérifier le résultat" in page
    assert 'data-suggested-name="Player name"' in page
    assert "data-ready-button" in page
    assert "Options facultatives" not in page
    assert "Langue du nouveau nom" not in page
    assert "Vous pouvez revenir en arrière" not in page
    assert "Rien ne change avant votre confirmation." in page
    assert f'href="{project_url}/names"' in page
    assert 'aria-current="page"' in page
    assert "DataPilot vous aide" in page
    assert "Poser une question" not in page
    assert page.count('class="side-link') == 6
    assert f'href="{project_url}/pipeline"' in page
    assert f'href="{project_url}/sql"' in page
    assert "Télécharger Excel" in page
    assert "Fichier JSON" in page
    assert "Fichier CSV" in page


def test_project_sections_are_distinct_pages_with_a_global_chatbot(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    pages = {
        "overview": test_client.get(project_url).get_data(as_text=True),
        "cleaning": test_client.get(project_url + "/cleaning").get_data(as_text=True),
        "names": test_client.get(project_url + "/names").get_data(as_text=True),
        "charts": test_client.get(project_url + "/charts").get_data(as_text=True),
    }

    assert 'id="overview"' in pages["overview"]
    assert 'id="cleaning"' not in pages["overview"]
    assert 'id="cleaning"' in pages["cleaning"]
    assert 'id="overview"' not in pages["cleaning"]
    assert 'id="business-words"' in pages["names"]
    assert 'id="chart-studio"' in pages["charts"]
    for section, page in pages.items():
        assert f'data-project-section="{section}"' in page
        assert 'data-chatbot' in page
        assert 'data-async-chat' in page
        assert f'name="section" value="{section}"' in page
        assert project_url + "/cleaning" in page
        assert project_url + "/names" in page
        assert project_url + "/charts" in page

    assert test_client.get(project_url + "/unknown").status_code == 404


def test_demo_creates_ready_to_explore_project(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    response = test_client.post(
        "/demo",
        data={"_csrf": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Comptoir Atlas · ventes" in page
    assert len(list((tmp_path / "storage").glob("*/project.json"))) == 1
    lakehouse = next((tmp_path / "storage").glob("*/lakehouse"))
    assert (lakehouse / "bronze" / "data.parquet").exists()
    assert (lakehouse / "silver" / "data.parquet").exists()
    assert list((lakehouse / "gold").glob("gold_*.parquet"))


def test_invalid_extension_is_rejected(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    response = test_client.post(
        "/upload",
        data={"_csrf": token, "dataset": (io.BytesIO(b"bad"), "danger.exe")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Format refus" in response.data


def test_ask_get_redirects_instead_of_405(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    ask_url = upload.headers["Location"] + "/ask"
    response = test_client.get(ask_url, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith(upload.headers["Location"])


def test_groq_mode_without_key_is_explained(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    response = test_client.post(
        upload.headers["Location"] + "/ask",
        data={
            "_csrf": token,
            "mode": "groq",
            "section": "names",
            "question": "Quels enseignements ?",
        },
    )
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "L’explication détaillée n’est pas encore disponible" in page
    assert "IA Groq" not in page
    assert 'data-project-section="names"' in page
    assert 'id="business-words"' in page


def test_ask_ajax_returns_only_json_without_rendering_the_page(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    monkeypatch.setattr(
        datapilot_app,
        "render_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full page render")),
    )
    response = test_client.post(
        upload.headers["Location"] + "/ask",
        data={
            "_csrf": token,
            "mode": "local",
            "question": "Que faut-il retenir de ce fichier ?",
        },
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    assert response.status_code == 200
    assert response.is_json
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["question"] == "Que faut-il retenir de ce fichier ?"
    assert payload["source"] == "local"
    assert "Tableau de bord automatique" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


def test_ask_ajax_validation_and_csrf_errors_are_json(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    headers = {"Accept": "application/json", "X-Requested-With": "DataPilot"}
    empty = test_client.post(
        upload.headers["Location"] + "/ask",
        data={"_csrf": token, "mode": "local", "question": "   "},
        headers=headers,
    )
    assert empty.status_code == 422
    assert empty.is_json

    invalid_csrf = test_client.post(
        upload.headers["Location"] + "/ask",
        data={"_csrf": "invalid", "mode": "local", "question": "Combien de lignes ?"},
        headers=headers,
    )
    assert invalid_csrf.status_code == 400
    assert invalid_csrf.is_json


def test_chart_can_be_created_and_deleted(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nCasablanca,20\nRabat,30\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    created = test_client.post(
        project_url + "/charts",
        data={
            "_csrf": token,
            "dimension": "ville",
            "measure": "montant",
            "aggregation": "mean",
            "chart_type": "bar",
        },
        follow_redirects=False,
    )
    assert created.status_code == 302
    assert created.headers["Location"] == project_url + "/charts"

    charts_path = next((tmp_path / "storage").glob("*/charts.json"))
    specs = json.loads(charts_path.read_text(encoding="utf-8"))
    assert specs[0]["dimension"] == "ville"
    page = test_client.get(project_url + "/charts").get_data(as_text=True)
    assert "Moyenne de montant par ville" in page

    deleted = test_client.post(
        project_url + f"/charts/{specs[0]['id']}/delete",
        data={"_csrf": token},
        follow_redirects=False,
    )
    assert deleted.status_code == 302
    assert json.loads(charts_path.read_text(encoding="utf-8")) == []


def test_chart_ajax_returns_an_updated_chart_studio_fragment(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    monkeypatch.setattr(
        datapilot_app,
        "render_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full page render")),
    )
    response = test_client.post(
        upload.headers["Location"] + "/charts",
        data={
            "_csrf": token,
            "dimension": "ville",
            "measure": "montant",
            "aggregation": "sum",
            "chart_type": "bar",
        },
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert payload["fragment"]["selector"] == "#chart-studio"
    assert "Somme de montant par ville" in payload["fragment"]["html"]


def test_invalid_chart_ajax_does_not_create_a_saved_chart(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    response = test_client.post(
        upload.headers["Location"] + "/charts",
        data={
            "_csrf": token,
            "dimension": "colonne_inconnue",
            "aggregation": "count",
            "chart_type": "bar",
        },
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 422
    assert payload["ok"] is False
    assert payload["fragment"]["selector"] == "#chart-studio"
    assert not list((tmp_path / "storage").glob("*/charts.json"))


def test_unknown_project_json_has_a_stable_error(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    response = test_client.get(
        "/project/not-a-project",
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 404
    assert payload["ok"] is False
    assert payload["error"]["code"] == "resource_not_found"


def test_cleaning_is_cumulative_reversible_and_reusable(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\n Casa ,10\nCasa,10\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    stripped = test_client.post(project_url + "/clean", data={"_csrf": token, "actions": "strip_text"})
    assert stripped.status_code == 302
    assert stripped.headers["Location"] == project_url + "/cleaning"
    deduplicated = test_client.post(project_url + "/clean", data={"_csrf": token, "actions": "drop_duplicates"})
    assert deduplicated.status_code == 302
    assert deduplicated.headers["Location"] == project_url + "/cleaning"

    metadata_path = next((tmp_path / "storage").glob("*/project.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert len(metadata["cleaning_batches"]) == 2
    clean_path = metadata_path.parent / "clean.csv"
    assert len(datapilot_app.pd.read_csv(clean_path)) == 1

    undone = test_client.post(project_url + "/clean/undo", data={"_csrf": token})
    assert undone.status_code == 302
    assert undone.headers["Location"] == project_url + "/cleaning"
    restored = datapilot_app.pd.read_csv(clean_path)
    assert len(restored) == 2
    assert set(restored["ville"]) == {"Casa"}

    saved = test_client.post(
        project_url + "/recipes",
        data={"_csrf": token, "name": "Nettoyage mensuel"},
    )
    assert saved.status_code == 302
    assert saved.headers["Location"] == project_url + "/cleaning"
    recipes = json.loads((tmp_path / "storage" / "cleaning_recipes.json").read_text(encoding="utf-8"))
    assert recipes[-1]["name"] == "Nettoyage mensuel"

    reset = test_client.post(project_url + "/reset", data={"_csrf": token})
    assert reset.status_code == 302
    assert reset.headers["Location"] == project_url + "/cleaning"

    nothing_selected = test_client.post(project_url + "/clean", data={"_csrf": token})
    assert nothing_selected.status_code == 302
    assert nothing_selected.headers["Location"] == project_url + "/cleaning"


def test_saved_kpi_and_business_words_feed_the_assistant_chart(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasablanca,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    created_kpi = test_client.post(
        project_url + "/kpis",
        data={
            "_csrf": token,
            "name": "Ventes visées",
            "measure": "montant",
            "aggregation": "sum",
            "target": "40",
            "direction": "higher",
        },
    )
    assert created_kpi.status_code == 302

    saved_word = test_client.post(
        project_url + "/dictionary",
        data={
            "_csrf": token,
            "column": "montant",
            "label_fr": "Chiffre d’affaires",
            "label_ar": "المبيعات",
            "synonyms": "CA, ventes",
            "description": "Total vendu",
        },
    )
    assert saved_word.status_code == 302
    summary_page = test_client.get(project_url).get_data(as_text=True)
    names_page = test_client.get(project_url + "/names").get_data(as_text=True)
    assert "Ventes visées" in summary_page
    assert "Chiffre d’affaires" in names_page
    assert "المبيعات" in names_page

    headers = {"Accept": "application/json", "X-Requested-With": "DataPilot"}
    answer = test_client.post(
        project_url + "/ask",
        data={"_csrf": token, "mode": "local", "question": "Montre le total du CA par ville"},
        headers=headers,
    )
    payload = answer.get_json()
    assert answer.status_code == 200
    assert payload["ok"] is True
    assert payload["chart_spec"]["dimension"] == "ville"
    assert payload["chart_spec"]["measure"] == "montant"

    pinned = test_client.post(
        payload["pin_url"],
        data={
            "_csrf": token,
            "dimension": payload["chart_spec"]["dimension"],
            "measure": payload["chart_spec"]["measure"],
            "aggregation": payload["chart_spec"]["aggregation"],
            "chart_type": payload["chart_spec"]["chart_type"],
        },
        headers=headers,
    )
    assert pinned.status_code == 200
    assert pinned.get_json()["ok"] is True


def test_translation_preview_is_safe_and_does_not_modify_the_file(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (
                io.BytesIO("statut,montant\nOpen,10\nClosed,20\nOpen,30\n".encode()),
                "suivi.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    raw_path = next((tmp_path / "storage").glob("*/raw.csv"))
    original = raw_path.read_bytes()

    preview = test_client.post(
        project_url + "/dictionary/preview",
        data={
            "_csrf": token,
            "column": "statut",
            "display_name": "État",
            "language_name": "Français",
            "include_values": "1",
        },
    )
    page = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert "Est-ce bien le résultat souhaité ?" in page
    assert "Open" in page and "Closed" in page
    assert raw_path.read_bytes() == original
    assert not (raw_path.parent / "dictionary.json").exists()


def test_translation_preview_ajax_returns_only_the_targeted_fragment(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"statut,montant\nOpen,10\nClosed,20\n"), "suivi.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    monkeypatch.setattr(
        datapilot_app,
        "render_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full render called")),
    )
    response = test_client.post(
        upload.headers["Location"] + "/dictionary/preview",
        data={
            "_csrf": token,
            "column": "statut",
            "display_name": "État",
            "language_name": "Français",
            "include_values": "1",
        },
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert payload["fragment"]["selector"] == "#business-words"
    assert payload["fragment"]["focus"] == "#translation-preview"
    assert "Est-ce bien le résultat souhaité ?" in payload["fragment"]["html"]
    assert "<!doctype html>" not in payload["fragment"]["html"].lower()


def test_translation_preview_ajax_validation_preserves_the_form(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"statut,montant\nOpen,10\nClosed,20\n"), "suivi.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    calls = []
    monkeypatch.setattr(
        datapilot_app.name_translation,
        "suggest_column_names",
        lambda *_args, **_kwargs: calls.append(True),
    )
    response = test_client.post(
        upload.headers["Location"] + "/dictionary/preview",
        data={
            "_csrf": token,
            "title_mode": "suggest",
            "column": "statut",
            "language_name": "Darija marocaine",
        },
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    payload = response.get_json()
    assert response.status_code == 422
    assert payload["error"]["code"] == "consent_required"
    assert calls == []
    assert 'value="statut"' in payload["fragment"]["html"]
    assert 'value="Darija marocaine"' in payload["fragment"]["html"]
    assert "Autorisez l’envoi du titre choisi" in payload["fragment"]["html"]


def test_dictionary_apply_and_undo_ajax_update_without_full_page(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasa,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    raw_path = next((tmp_path / "storage").glob("*/raw.csv"))
    original = raw_path.read_bytes()
    headers = {"Accept": "application/json", "X-Requested-With": "DataPilot"}

    applied = test_client.post(
        project_url + "/dictionary/apply",
        data={
            "_csrf": token,
            "column": "ville",
            "display_name": "City",
            "language_name": "English",
            "value_count": "0",
        },
        headers=headers,
    )
    applied_payload = applied.get_json()
    assert applied.status_code == 200
    assert applied_payload["changed"] is True
    assert applied_payload["refresh_targets"] == datapilot_app.DICTIONARY_DEPENDENT_TARGETS
    assert "City" in applied_payload["fragment"]["html"]
    assert json.loads((raw_path.parent / "dictionary.json").read_text(encoding="utf-8"))[0]["display_name"] == "City"

    undone = test_client.post(
        project_url + "/dictionary/undo",
        data={"_csrf": token},
        headers=headers,
    )
    undone_payload = undone.get_json()
    assert undone.status_code == 200
    assert undone_payload["changed"] is True
    assert json.loads((raw_path.parent / "dictionary.json").read_text(encoding="utf-8")) == []
    assert raw_path.read_bytes() == original


def test_ajax_csrf_error_uses_a_stable_json_contract(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasa,10\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    response = test_client.post(
        upload.headers["Location"] + "/dictionary/preview",
        data={"_csrf": "expired", "column": "ville", "display_name": "City"},
        headers={"Accept": "application/json", "X-Requested-With": "DataPilot"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "csrf_invalid"


def test_automatic_title_suggestion_requires_explicit_consent(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"statut,montant\nOpen,10\nClosed,20\n"), "suivi.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    calls = []
    monkeypatch.setattr(
        datapilot_app.name_translation,
        "suggest_column_names",
        lambda *_args, **_kwargs: calls.append((_args, _kwargs)),
    )
    response = test_client.post(
        upload.headers["Location"] + "/dictionary/preview",
        data={
            "_csrf": token,
            "title_mode": "suggest",
            "column": "statut",
            "language_name": "Darija marocaine",
        },
        follow_redirects=True,
    )
    assert calls == []
    assert "Autorisez l’envoi du titre choisi" in response.get_data(as_text=True)
    project_dir = next((tmp_path / "storage").glob("*"))
    assert not (project_dir / "dictionary.json").exists()


def test_automatic_darija_title_uses_only_the_selected_title(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (
                io.BytesIO(b"statut,email,montant\nOpen,a@example.com,10\nClosed,b@example.com,20\n"),
                "suivi.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    project_dir = next((tmp_path / "storage").glob("*"))
    raw_path = project_dir / "raw.csv"
    original = raw_path.read_bytes()
    captured = {}

    def suggest(columns, target_language, *, api_key=None, model=None):
        captured.update({
            "columns": columns,
            "target_language": target_language,
            "api_key_present": bool(api_key),
            "model": model,
        })
        return {
            "ok": True,
            "source": "groq",
            "suggestions": [{"original": "statut", "suggested": "الحالة"}],
        }

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(datapilot_app.name_translation, "suggest_column_names", suggest)
    preview = test_client.post(
        project_url + "/dictionary/preview",
        data={
            "_csrf": token,
            "title_mode": "suggest",
            "column": "statut",
            "language_name": "Darija marocaine",
            "suggestion_consent": "1",
            "include_values": "1",
        },
    )
    page = preview.get_data(as_text=True)
    assert captured == {
        "columns": ["statut"],
        "target_language": "Darija marocaine",
        "api_key_present": True,
        "model": None,
    }
    assert "email" not in captured["columns"] and "Open" not in captured["columns"]
    assert 'value="الحالة"' in page
    assert 'lang="ary" dir="rtl"' in page
    assert "Open" in page and "Closed" in page
    assert "Vous pouvez encore la modifier" in page
    assert raw_path.read_bytes() == original
    assert not (project_dir / "dictionary.json").exists()


def test_column_and_row_labels_are_applied_to_views_and_exports(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (
                io.BytesIO("statut,montant\nOpen,10\nClosed,20\nOpen,30\n".encode()),
                "suivi.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    raw_path = next((tmp_path / "storage").glob("*/raw.csv"))
    original = raw_path.read_bytes()

    applied = test_client.post(
        project_url + "/dictionary/apply",
        data={
            "_csrf": token,
            "column": "statut",
            "display_name": "الحالة",
            "language_name": "العربية",
            "include_values": "1",
            "value_count": "2",
            "source_0": "Open",
            "value_label_0": "مفتوح",
            "source_1": "Closed",
            "value_label_1": "مغلق",
        },
        follow_redirects=True,
    )
    page = applied.get_data(as_text=True)
    assert applied.status_code == 200
    assert "الحالة" in page
    assert 'lang="ar" dir="rtl"' in page
    assert raw_path.read_bytes() == original

    entries = json.loads((raw_path.parent / "dictionary.json").read_text(encoding="utf-8"))
    assert entries[0]["display_name"] == "الحالة"
    assert entries[0]["direction"] == "rtl"
    assert len(entries[0]["value_labels"]) == 2

    exported = test_client.get(project_url + "/export/csv")
    exported_text = exported.data.decode("utf-8-sig")
    assert exported_text.splitlines()[0] == "الحالة,montant"
    assert "مفتوح,10" in exported_text
    assert "مغلق,20" in exported_text

    exported_json = test_client.get(project_url + "/export/json")
    assert exported_json.status_code == 200
    assert exported_json.mimetype == "application/json"
    rows = json.loads(exported_json.get_data(as_text=True))
    assert rows[0]["الحالة"] == "مفتوح"

    exported_zip = test_client.get(project_url + "/export/zip")
    assert exported_zip.status_code == 200
    assert exported_zip.mimetype == "application/zip"
    assert "attachment" in exported_zip.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(exported_zip.data)) as archive:
        assert "README.txt" in archive.namelist()
        zipped_csv = next(name for name in archive.namelist() if name.endswith(".csv"))
        export_text = archive.read(zipped_csv).decode("utf-8-sig")
    assert export_text.splitlines()[0] == "الحالة,montant"
    assert "مفتوح,10" in export_text

    exported_excel = test_client.get(project_url + "/export/xlsx")
    assert exported_excel.status_code == 200
    assert exported_excel.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in exported_excel.headers["Content-Disposition"]
    excel_rows = pd.read_excel(io.BytesIO(exported_excel.data))
    assert excel_rows.columns.tolist() == ["الحالة", "montant"]
    assert excel_rows.iloc[0]["الحالة"] == "مفتوح"


def test_json_export_preserves_values_that_only_spreadsheets_must_escape(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (
                io.BytesIO(b"code,value\nA,=2+2\nB,-114.0\n"),
                "formules.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    exported = test_client.get(upload.headers["Location"] + "/export/json")
    assert exported.status_code == 200
    rows = json.loads(exported.get_data(as_text=True))
    assert rows == [
        {"code": "A", "value": "=2+2"},
        {"code": "B", "value": "-114.0"},
    ]


def test_invoice_import_rejects_more_than_ten_files(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    files = [(io.BytesIO(b"fake pdf"), f"facture-{index}.pdf") for index in range(11)]
    response = test_client.post(
        "/invoice",
        data={"_csrf": token, "invoices": files},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "au maximum 10 factures" in response.get_data(as_text=True)
    assert not list((tmp_path / "storage").glob("*/project.json"))


def test_name_changes_can_be_undone_without_touching_data(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasa,10\nRabat,20\n"), "ventes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    raw_path = next((tmp_path / "storage").glob("*/raw.csv"))
    original = raw_path.read_bytes()
    assert test_client.post(
        project_url + "/dictionary/apply",
        data={
            "_csrf": token,
            "column": "ville",
            "display_name": "City",
            "language_name": "English",
            "value_count": "0",
        },
    ).status_code == 302

    undone = test_client.post(
        project_url + "/dictionary/undo",
        data={"_csrf": token},
        follow_redirects=True,
    )
    assert undone.status_code == 200
    assert json.loads((raw_path.parent / "dictionary.json").read_text(encoding="utf-8")) == []
    assert raw_path.read_bytes() == original
    assert "La dernière modification de noms a été annulée" in undone.get_data(as_text=True)


def test_category_merge_requires_explicit_confirmation(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    upload = test_client.post(
        "/upload",
        data={
            "_csrf": token,
            "dataset": (io.BytesIO(b"ville,montant\nCasa,1\nCasablanca,2\nRabat,3\n"), "villes.csv"),
        },
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    project_url = upload.headers["Location"]
    payload = {
        "_csrf": token,
        "column": "ville",
        "display_name": "Ville",
        "language_name": "Français",
        "include_values": "1",
        "value_count": "3",
        "source_0": "Casa",
        "value_label_0": "Casablanca",
        "source_1": "Casablanca",
        "value_label_1": "Casablanca",
        "source_2": "Rabat",
        "value_label_2": "Rabat",
    }
    rejected = test_client.post(project_url + "/dictionary/apply", data=payload)
    assert "Confirmez explicitement" in rejected.get_data(as_text=True)
    dictionary_path = next((tmp_path / "storage").glob("*/dictionary.json"), None)
    assert dictionary_path is None

    payload["confirm_merge"] = "1"
    accepted = test_client.post(project_url + "/dictionary/apply", data=payload)
    assert accepted.status_code == 302
    dictionary_path = next((tmp_path / "storage").glob("*/dictionary.json"))
    saved = json.loads(dictionary_path.read_text(encoding="utf-8"))[0]
    assert saved["allow_value_merge"] is True


def test_invoice_import_requires_human_review_then_confirmation(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    token = csrf(test_client)
    fields = {
        "supplier": {"value": "Atlas SARL"},
        "invoice_number": {"value": "F-42"},
        "invoice_date": {"value": "2026-07-17"},
        "subtotal_excluding_tax": {"value": 1000.0},
        "vat_amount": {"value": 200.0},
        "vat_rate": {"value": 20.0},
        "total_including_tax": {"value": 1200.0},
        "currency": {"value": "MAD"},
    }
    monkeypatch.setattr(
        datapilot_app.invoice_features,
        "process_invoice",
        lambda _path: {
            "status": "needs_review",
            "message": "8 champs détectés. Vérifiez les valeurs avant de continuer.",
            "fields": fields,
            "review": {"required": True},
        },
    )
    upload = test_client.post(
        "/invoice",
        data={"_csrf": token, "invoices": (io.BytesIO(b"fake pdf"), "facture.pdf")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert upload.status_code == 302
    assert upload.headers["Location"].endswith("#invoice-review")
    project_url = upload.headers["Location"].split("#", 1)[0]
    page = test_client.get(project_url).get_data(as_text=True)
    assert "Vérifiez vos factures" in page
    assert "Atlas SARL" in page

    confirmed = test_client.post(
        project_url + "/invoice/confirm",
        data={
            "_csrf": token,
            "fournisseur_0": "Atlas SARL",
            "numero_facture_0": "F-42",
            "date_facture_0": "2026-07-17",
            "total_ht_0": "1000",
            "tva_0": "200",
            "taux_tva_0": "20",
            "total_ttc_0": "1200",
            "devise_0": "MAD",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 302
    metadata = json.loads(next((tmp_path / "storage").glob("*/project.json")).read_text(encoding="utf-8"))
    assert metadata["cleaned"] is True
    assert metadata["invoice_confirmed_at"]
