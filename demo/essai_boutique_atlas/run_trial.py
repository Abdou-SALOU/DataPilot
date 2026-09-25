"""Essai HTTP réel : Excel + TXT + photo manuscrite, puis validation humaine."""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from pathlib import Path

import requests
from bs4 import BeautifulSoup


BASE = os.environ.get("DATAPILOT_TEST_URL", "http://127.0.0.1:5071")
FILES = Path(__file__).resolve().parent


def main() -> None:
    session = requests.Session()
    home = session.get(BASE, timeout=15)
    home.raise_for_status()
    token = BeautifulSoup(home.text, "html.parser").select_one('input[name="_csrf"]')["value"]

    names = ["ventes_caisse.xlsx", "ventes_complementaires.txt", "carnet_manuscrit.png"]
    with ExitStack() as stack:
        uploads = [("documents", (name, stack.enter_context((FILES / name).open("rb")))) for name in names]
        review = session.post(BASE + "/documents", data={"_csrf": token, "purpose": "sales"}, files=uploads, timeout=90)
    review.raise_for_status()
    assert "/documents/review" in review.url, review.url
    project_url = review.url.rsplit("/documents/review", 1)[0]

    soup = BeautifulSoup(review.text, "html.parser")
    documents = [node.get_text(" ", strip=True) for node in soup.select(".capture-document")]
    fields = [field for field in soup.select("[data-capture-rows] > fieldset.capture-row")
              if field.select_one('input[name="product[]"]').get("value", "")]
    assert len(fields) == 10, f"10 lignes attendues, {len(fields)} trouvées"
    data = [("_csrf", token)]
    products = []
    amounts = []
    for field in fields:
        inputs = {node["name"]: node.get("value", "") for node in field.select("input[name]")}
        name = "Thé" if inputs["product[]"] == "The" else inputs["product[]"]
        date = "2026-09-23" if inputs["source[]"].endswith(".png") else inputs["date[]"]
        for key, value in (("date[]", date), ("product[]", name), ("quantity[]", inputs["quantity[]"]),
                           ("amount[]", inputs["amount[]"]), ("source[]", inputs["source[]"])):
            data.append((key, value))
        products.append(name)
        amounts.append(float(inputs["amount[]"].replace(",", ".")))
    assert round(sum(amounts), 2) == 291.5

    result = session.post(project_url + "/documents/confirm", data=data, timeout=30)
    result.raise_for_status()
    assert "Ventes déclarées" in result.text
    assert "291,50" in result.text
    dashboard = BeautifulSoup(result.text, "html.parser").select_one("[data-dashboard-chart-data]")
    charts = json.loads(dashboard.string)
    assert charts, "Aucun graphique généré"

    report = {
        "entreprise": "Boutique Atlas (fictive)",
        "documents": names,
        "prelecture": documents,
        "lignes_confirmees": len(fields),
        "correction_humaine": "The → Thé ; date de la photo ajoutée",
        "montant_total_attendu": 291.5,
        "montant_total_observe": round(sum(amounts), 2),
        "graphiques": len(charts),
        "url_locale": result.url,
    }
    (FILES / "resultat_essai.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
