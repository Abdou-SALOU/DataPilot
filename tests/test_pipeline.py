import io
import json

import pandas as pd
import pytest

import app as datapilot_app
import datapilot
import pipeline


MESSY_SALES = (
    "id_vente,date_vente,ville,produit,quantite,prix_unitaire,montant,note_satisfaction\n"
    "V-1,01/07/2026,Casablanca,Thé,2,10,20,4\n"
    "V-2,02/07/2026,Casblanca,Café,1,15,15,5\n"
    "V-2,02/07/2026,Casblanca,Café,1,15,15,5\n"
    "V-3,03/07/2026,Rabat,Thé,-1,10,abc,9\n"
    "V-4,,Rabat,Café,3,15,45,3\n"
    "V-5,05/07/2026,Rabat,Café,2,15,30,4\n"
)


def build_project(tmp_path, csv_text=MESSY_SALES, clean=False):
    source = tmp_path / "source.csv"
    source.write_text(csv_text, encoding="utf-8")
    frame = datapilot.read_dataset(source)
    frame.to_csv(tmp_path / "raw.csv", index=False, encoding="utf-8")
    silver = frame
    if clean:
        actions = [item["id"] for item in datapilot.suggest_cleaning(frame)]
        silver, _log = datapilot.apply_cleaning(frame, actions)
        silver = datapilot.restore_semantic_types(silver)
    run = pipeline.run_pipeline(tmp_path, source_path=source, bronze_path=tmp_path / "raw.csv", silver=silver, trigger="test")
    return run


def check(run, layer, check_id):
    return next(item for item in run["checks"][layer] if item["id"] == check_id)


def test_contract_detects_documented_anomalies_on_bronze(tmp_path):
    run = build_project(tmp_path)
    assert check(run, "bronze", "table.no_duplicates")["failing_rows"] == 1
    assert check(run, "bronze", "id_vente.unique")["failing_rows"] == 1
    assert check(run, "bronze", "quantite.min")["failing_rows"] == 1
    assert check(run, "bronze", "montant.type")["failing_rows"] == 1
    assert check(run, "bronze", "note_satisfaction.max")["failing_rows"] == 1
    assert check(run, "bronze", "date_vente.not_null")["failing_rows"] == 1
    variants = check(run, "bronze", "ville.consistent")
    assert variants["status"] == "fail" and "Casblanca → Casablanca" in variants["detail"]


def test_cleaning_improves_silver_quality_and_keeps_bronze_untouched(tmp_path):
    run = build_project(tmp_path, clean=True)
    assert run["quality"]["silver"]["score"] > run["quality"]["bronze"]["score"]
    assert check(run, "silver", "table.no_duplicates")["status"] == "pass"
    assert check(run, "silver", "ville.consistent")["status"] == "pass"
    bronze = pd.read_parquet(tmp_path / "lakehouse" / "bronze" / "data.parquet")
    assert len(bronze) == 6
    assert {"_ingested_at", "_source_file", "_row_number"} <= set(bronze.columns)
    assert "Casblanca" in set(bronze["ville"])


def test_run_is_traced_with_fingerprint_and_stage_metrics(tmp_path):
    run = build_project(tmp_path)
    assert [stage["name"] for stage in run["stages"]] == [
        "ingest_bronze", "validate_contract", "transform_silver", "publish_gold",
    ]
    assert len(run["input"]["sha256"]) == 64
    assert all(stage["duration_ms"] >= 0 for stage in run["stages"])
    build_project(tmp_path)
    runs = pipeline.list_runs(tmp_path)
    assert len(runs) == 2
    assert runs[0]["input"]["sha256"] == runs[1]["input"]["sha256"]


def test_gold_tables_aggregate_business_values(tmp_path):
    build_project(tmp_path, clean=True)
    tables = pipeline.available_tables(tmp_path)
    assert {"bronze", "silver", "gold_column_profile", "gold_monthly_trend"} <= set(tables)
    by_product = pipeline.read_table(tmp_path, "gold_montant_by_produit")
    assert set(by_product.columns) >= {"produit", "total", "transactions", "share_pct"}
    assert by_product["share_pct"].sum() == pytest.approx(100, abs=0.2)


def test_sql_console_reads_layers(tmp_path):
    build_project(tmp_path, clean=True)
    result = pipeline.run_sql(tmp_path, "SELECT ville, COUNT(*) AS n FROM silver GROUP BY ville ORDER BY n DESC")
    assert result["columns"] == ["ville", "n"]
    assert result["rows"][0][0] == "Rabat"
    assert "silver" in result["tables"]


@pytest.mark.parametrize("query", [
    "DELETE FROM silver",
    "SELECT 1; DROP TABLE silver",
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "COPY silver TO 'out.csv'",
    "ATTACH 'x.db'",
    "SELECT * FROM glob('*')",
    "",
])
def test_sql_console_rejects_writes_and_file_access(tmp_path, query):
    build_project(tmp_path)
    with pytest.raises(pipeline.SQLError):
        pipeline.run_sql(tmp_path, query)


def test_sql_console_cannot_read_files_even_with_a_table_path(tmp_path):
    build_project(tmp_path)
    with pytest.raises(pipeline.SQLError):
        pipeline.run_sql(tmp_path, "SELECT * FROM 'raw.csv'")


def test_semicolon_inside_string_literal_is_allowed(tmp_path):
    build_project(tmp_path)
    result = pipeline.run_sql(tmp_path, "SELECT * FROM silver WHERE produit = 'a;b'")
    assert result["row_count"] == 0


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(datapilot_app, "STORAGE_ROOT", tmp_path / "storage")
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return datapilot_app.app.test_client()


def upload(test_client):
    test_client.get("/")
    with test_client.session_transaction() as session:
        token = session["_csrf"]
    response = test_client.post(
        "/upload",
        data={"_csrf": token, "dataset": (io.BytesIO(MESSY_SALES.encode("utf-8")), "ventes.csv")},
        content_type="multipart/form-data",
    )
    return response.headers["Location"], token


def test_import_runs_pipeline_and_pages_render(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_url, token = upload(test_client)
    project_id = project_url.rstrip("/").split("/")[-1]
    assert (tmp_path / "storage" / project_id / "lakehouse" / "_runs.jsonl").exists()
    quality = test_client.get(project_url + "/cleaning").get_data(as_text=True)
    assert "Contrat de données : avant et après nettoyage" in quality
    assert "Casblanca → Casablanca" in quality
    pipeline_page = test_client.get(project_url + "/pipeline").get_data(as_text=True)
    assert "Ingestion → Bronze" in pipeline_page and "Publication → Gold" in pipeline_page
    assert "Historique des exécutions" in pipeline_page
    assert "contract.json" in pipeline_page
    sql_page = test_client.get(project_url + "/sql").get_data(as_text=True)
    assert "data-sql-editor" in sql_page


def test_cleaning_triggers_a_new_traced_run(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_url, token = upload(test_client)
    project_id = project_url.rstrip("/").split("/")[-1]
    test_client.post(project_url + "/clean", data={"_csrf": token, "actions": ["drop_duplicates"]})
    runs = pipeline.list_runs(tmp_path / "storage" / project_id)
    assert [run["trigger"] for run in runs[:2]] == ["cleaning", "import"]
    assert runs[0]["rows"]["silver"] == 5


def test_sql_route_returns_results_and_errors(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_url, token = upload(test_client)
    ok = test_client.post(project_url + "/sql", data={"_csrf": token, "query": "SELECT COUNT(*) AS lignes FROM bronze"})
    assert ok.status_code == 200
    assert "lignes" in ok.get_data(as_text=True)
    refused = test_client.post(project_url + "/sql", data={"_csrf": token, "query": "DROP TABLE silver"})
    assert refused.status_code == 422
    assert "Seules les requêtes SELECT" in refused.get_data(as_text=True)


def test_json_api_exposes_layers_quality_and_runs(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_url, _token = upload(test_client)
    project_id = project_url.rstrip("/").split("/")[-1]
    table = test_client.get(f"/api/v1/projects/{project_id}/tables/silver?limit=2").get_json()
    assert table["total_rows"] == 6 and len(table["rows"]) == 2
    quality = test_client.get(f"/api/v1/projects/{project_id}/quality").get_json()
    assert quality["summary"]["bronze"]["total"] > 0
    assert quality["contract"]["columns"]
    runs = test_client.get(f"/api/v1/projects/{project_id}/runs").get_json()
    assert runs["runs"][0]["trigger"] == "import"
    assert test_client.get(f"/api/v1/projects/{project_id}/tables/..%2Fsecret").status_code == 404
    assert test_client.get(f"/api/v1/projects/{project_id}/tables/unknown").status_code == 404
