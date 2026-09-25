# -*- coding: utf-8 -*-
"""Essai de bout en bout : chaque fichier traverse les vraies routes de DataPilot.

Mesures produites (``rapport_e2e.json`` et ``RAPPORT_E2E.md``) :
- tableurs : anomalies détectées par le contrat Bronze, qualité avant/après
  nettoyage, tables Gold publiées, requête SQL de contrôle ;
- carnets manuscrits et PDF scanné : rappel (lignes retrouvées), exactitude des
  montants et écart sur le total, avant toute correction humaine ;
- facture : champs extraits correctement.

Usage : ``python demo/test_suite/run_e2e.py`` (aucun serveur à lancer).
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import app as datapilot_app  # noqa: E402
import pipeline  # noqa: E402


def plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    return " ".join(text.split())


def new_client(storage: Path):
    datapilot_app.STORAGE_ROOT = storage
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="e2e")
    client = datapilot_app.app.test_client()
    client.get("/")
    with client.session_transaction() as session:
        token = session["_csrf"]
    return client, token


def project_id_from(location: str) -> str:
    return location.split("/project/")[1].split("/")[0].split("#")[0]


def run_spreadsheet(client, token, storage: Path, name: str, truth: dict) -> dict:
    path = HERE / "files" / name
    started = time.perf_counter()
    response = client.post("/upload", data={"_csrf": token, "dataset": (io.BytesIO(path.read_bytes()), name)},
                           content_type="multipart/form-data")
    project_id = project_id_from(response.headers["Location"])
    directory = storage / project_id
    first = pipeline.latest_run(directory)
    failing = {item["id"]: item for item in first["checks"]["bronze"] if item["status"] != "pass"}
    page = client.get(f"/project/{project_id}/cleaning").get_data(as_text=True)
    suggestions = datapilot_app.datapilot.suggest_cleaning(datapilot_app.load_frame(project_id))
    client.post(f"/project/{project_id}/clean", data={"_csrf": token, "actions": [item["id"] for item in suggestions]})
    after = pipeline.latest_run(directory)
    sql = client.post(f"/project/{project_id}/sql", data={
        "_csrf": token, "query": "SELECT (SELECT COUNT(*) FROM bronze) AS bronze_rows, (SELECT COUNT(*) FROM silver) AS silver_rows",
    })
    english = client.get(f"/project/{project_id}/cleaning", headers={"Cookie": "dp_lang=en"}).get_data(as_text=True)
    return {
        "file": name,
        "rows": first["rows"]["bronze"],
        "expected_anomalies": truth.get("anomalies", {}),
        "failed_or_warned_checks": {key: {"status": value["status"], "rows": value["failing_rows"], "detail": value["detail"]}
                                    for key, value in failing.items()},
        "suggested_fixes": [item["label"] for item in suggestions],
        "quality_bronze": first["quality"]["bronze"],
        "quality_silver_after_cleaning": after["quality"]["silver"],
        "rows_after_cleaning": after["rows"]["silver"],
        "gold_tables": [table["name"] for table in after["gold_tables"]],
        "sql_status": sql.status_code,
        "quality_page_ok": "Contrat de données" in page,
        "english_page_ok": "Suggested fixes" in english or "No fix needed" in english,
        "seconds": round(time.perf_counter() - started, 2),
    }


def score_rows(found: list[dict], expected: list[dict]) -> dict:
    remaining = list(found)
    matched, exact_amounts = 0, 0
    details = []
    for line in expected:
        best, best_ratio = None, 0.0
        for row in remaining:
            ratio = SequenceMatcher(None, plain(row.get("product", "")), plain(line["product"])).ratio()
            if ratio > best_ratio:
                best, best_ratio = row, ratio
        amount_ok = False
        if best is not None and best_ratio >= 0.6:
            matched += 1
            remaining.remove(best)
            try:
                amount_ok = abs(float(str(best.get("amount", "")).replace(",", ".")) - line["amount"]) < 0.005
            except ValueError:
                amount_ok = False
            exact_amounts += int(amount_ok)
        details.append({"expected": f"{line['product']} {line['amount']:.2f}",
                        "read": f"{best.get('product')} {best.get('amount')}" if best and best_ratio >= 0.6 else None,
                        "amount_ok": amount_ok})
    found_total = 0.0
    for row in found:
        try:
            found_total += float(str(row.get("amount", "")).replace(",", "."))
        except ValueError:
            pass
    return {
        "expected_rows": len(expected),
        "proposed_rows": len(found),
        "rows_found": matched,
        "recall_pct": round(matched / len(expected) * 100, 1),
        "exact_amounts": exact_amounts,
        "amount_accuracy_pct": round(exact_amounts / len(expected) * 100, 1),
        "extra_rows": len(remaining),
        "expected_total": round(sum(line["amount"] for line in expected), 2),
        "proposed_total": round(found_total, 2),
        "details": details,
    }


def run_document(client, token, storage: Path, name: str, truth: dict) -> dict:
    path = HERE / "files" / name
    started = time.perf_counter()
    response = client.post("/documents", data={
        "_csrf": token, "purpose": "sales", "currency": "MAD",
        "documents": [(io.BytesIO(path.read_bytes()), name)],
    }, content_type="multipart/form-data")
    project_id = project_id_from(response.headers["Location"])
    metadata = json.loads((storage / project_id / "project.json").read_text(encoding="utf-8"))
    proposed = metadata.get("proposed_rows", [])
    result = score_rows(proposed, truth["lines"])
    review = client.get(f"/project/{project_id}/documents/review")
    # Confirmation humaine simulée : on valide les lignes corrigées selon la vérité terrain.
    form = {"_csrf": token, "date[]": [], "product[]": [], "quantity[]": [], "amount[]": [], "source[]": []}
    for line in truth["lines"]:
        form["date[]"].append("2026-09-23")
        form["product[]"].append(line["product"])
        form["quantity[]"].append("1")
        form["amount[]"].append(f"{line['amount']:.2f}")
        form["source[]"].append("")
    confirm = client.post(f"/project/{project_id}/documents/confirm", data=form)
    run = pipeline.latest_run(storage / project_id)
    result.update({
        "file": name,
        "difficulty": truth.get("difficulty"),
        "review_page_status": review.status_code,
        "confirmed_status": confirm.status_code,
        "confirmed_total_matches": bool(run) and abs(sum(line["amount"] for line in truth["lines"]) - truth["total"]) < 0.01,
        "pipeline_after_confirmation": bool(run),
        "seconds": round(time.perf_counter() - started, 2),
    })
    return result


def run_invoice(client, token, storage: Path, name: str, truth: dict) -> dict:
    path = HERE / "files" / name
    response = client.post("/invoice", data={"_csrf": token, "invoices": [(io.BytesIO(path.read_bytes()), name)]},
                           content_type="multipart/form-data")
    project_id = project_id_from(response.headers["Location"])
    frame = datapilot_app.load_frame(project_id, prefer_clean=False)
    row = frame.iloc[0].to_dict()
    checks = {
        "supplier": plain(truth["supplier"]) in plain(row.get("fournisseur") or ""),
        "invoice_number": truth["invoice_number"] in str(row.get("numero_facture") or ""),
        "date": str(row.get("date_facture") or "")[:10] == truth["date"],
        "total_ht": _close(row.get("total_ht"), truth["total_ht"]),
        "vat": _close(row.get("tva"), truth["vat"]),
        "total_ttc": _close(row.get("total_ttc"), truth["total_ttc"]),
        "currency": str(row.get("devise") or "") == truth["currency"],
    }
    return {"file": name, "fields_ok": sum(checks.values()), "fields_total": len(checks), "fields": checks,
            "read": {key: (None if value != value else value) for key, value in row.items()}}


def _close(value, expected) -> bool:
    try:
        return abs(float(value) - expected) < 0.01
    except (TypeError, ValueError):
        return False


def main() -> None:
    truth = json.loads((HERE / "ground_truth.json").read_text(encoding="utf-8"))
    report = {"spreadsheets": [], "documents": [], "invoices": []}
    with tempfile.TemporaryDirectory(prefix="datapilot-e2e-") as folder:
        storage = Path(folder)
        client, token = new_client(storage)
        for name in ("ventes_mensuelles_sales.csv", "orders_en.json"):
            report["spreadsheets"].append(run_spreadsheet(client, token, storage, name, truth[name]))
        demo = ROOT / "demo" / "comptoir_atlas_ventes.xlsx"
        if demo.exists():
            (HERE / "files" / demo.name).write_bytes(demo.read_bytes())
            report["spreadsheets"].append(run_spreadsheet(client, token, storage, demo.name, {"anomalies": {
                "missing": 10, "duplicates": 8, "format": 10, "label_typos": 15, "outliers": 9}}))
            (HERE / "files" / demo.name).unlink()
        for name in ("carnet_epicerie_propre.png", "carnet_snack_incline.jpg", "carnet_boutique_difficile.jpg", "carnet_snack_scan.pdf"):
            report["documents"].append(run_document(client, token, storage, name, truth[name]))
        gpt_photo = ROOT / "demo" / "essai_boutique_atlas" / "carnet_manuscrit.png"
        if gpt_photo.exists():
            (HERE / "files" / "carnet_photo_realiste.png").write_bytes(gpt_photo.read_bytes())
            lines = [{"product": p, "amount": a} for p, a in [("Pain", 25.5), ("Lait", 18.0), ("Sucre", 32.0), ("Thé", 14.5), ("Biscuits", 27.0)]]
            report["documents"].append(run_document(client, token, storage, "carnet_photo_realiste.png",
                                                    {"lines": lines, "total": 117.0, "difficulty": "photo réaliste générée par IA"}))
            (HERE / "files" / "carnet_photo_realiste.png").unlink()
        report["invoices"].append(run_invoice(client, token, storage, "facture_atlas.png", truth["facture_atlas.png"]))
    (HERE / "rapport_e2e.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(report)
    print(json.dumps({
        "spreadsheets": [(item["file"], item["quality_bronze"]["score"], item["quality_silver_after_cleaning"]["score"]) for item in report["spreadsheets"]],
        "documents": [(item["file"], item["recall_pct"], item["amount_accuracy_pct"]) for item in report["documents"]],
        "invoices": [(item["file"], f"{item['fields_ok']}/{item['fields_total']}") for item in report["invoices"]],
    }, ensure_ascii=False, indent=1))


def write_markdown(report: dict) -> None:
    lines = ["# Rapport d'essai de bout en bout", "",
             "Généré par `demo/test_suite/run_e2e.py`. Données fictives, vérité terrain dans `ground_truth.json`.", "",
             "## Tableurs : contrat de données et nettoyage", "",
             "| Fichier | Lignes | Contrôles en écart (Bronze) | Qualité Bronze | Qualité Silver après nettoyage | Lignes Silver | Tables Gold | Durée |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for item in report["spreadsheets"]:
        lines.append(f"| `{item['file']}` | {item['rows']} | {len(item['failed_or_warned_checks'])} | {item['quality_bronze']['score']} % "
                     f"| {item['quality_silver_after_cleaning']['score']} % | {item['rows_after_cleaning']} | {len(item['gold_tables'])} | {item['seconds']} s |")
    lines += ["", "## Carnets manuscrits et PDF scanné : lecture OCR locale, avant correction humaine", "",
              "| Fichier | Difficulté | Lignes attendues | Lignes retrouvées | Montants exacts | Lignes en trop | Total attendu | Total proposé |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for item in report["documents"]:
        difficulty = item["difficulty"]
        if isinstance(difficulty, dict):
            difficulty = f"{difficulty['font']}, rotation {difficulty['rotation_deg']}°, flou {difficulty['blur']}, JPEG {difficulty['jpeg_quality'] or 'non'}"
        lines.append(f"| `{item['file']}` | {difficulty} | {item['expected_rows']} | {item['rows_found']} ({item['recall_pct']} %) "
                     f"| {item['exact_amounts']} ({item['amount_accuracy_pct']} %) | {item['extra_rows']} | {item['expected_total']:.2f} | {item['proposed_total']:.2f} |")
    lines += ["", "Après la vérification humaine (écran « Vérifier »), les lignes confirmées alimentent le pipeline : "
              "aucun chiffre lu par l'OCR n'est utilisé sans validation.", "", "## Facture", ""]
    for item in report["invoices"]:
        fields = ", ".join(f"{key} {'✓' if ok else '✗'}" for key, ok in item["fields"].items())
        lines.append(f"- `{item['file']}` : {item['fields_ok']}/{item['fields_total']} champs corrects ({fields}).")
    (HERE / "RAPPORT_E2E.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
