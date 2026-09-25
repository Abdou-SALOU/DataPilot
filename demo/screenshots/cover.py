# -*- coding: utf-8 -*-
"""Compose l'image de couverture (4:3) à partir des vraies captures de l'application.

    python demo/screenshots/cover.py

Produit docs/screenshots/fr/00_couverture.png et docs/screenshots/en/00_cover.png.
Les chiffres affichés proviennent de demo/test_suite/rapport_e2e.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
LOGO = ROOT / "static" / "images" / "datapilot-app-icon-v2.png"

COPY = {
    "fr": {
        "file": "fr/00_couverture.png",
        "eyebrow": "Projet data engineering · Python",
        "title": "Des fichiers désordonnés<br><span>aux données fiables.</span>",
        "lead": "Tableurs, PDF et carnets manuscrits passent par un pipeline Bronze → Silver → Gold, contrôlé par un contrat de données.",
        "quality": "Qualité des données",
        "ocr": "Carnets manuscrits lus",
        "ocr_detail": "montants exacts avant correction",
        "checks": "contrôles par exécution",
        "front": "fr/04_qualite_apres.png",
        "back": "fr/05_pipeline.png",
    },
    "en": {
        "file": "en/00_cover.png",
        "eyebrow": "Data engineering project · Python",
        "title": "Messy files in.<br><span>Trusted data out.</span>",
        "lead": "Spreadsheets, PDFs and handwritten ledgers flow through a Bronze → Silver → Gold pipeline enforced by a data contract.",
        "quality": "Data quality",
        "ocr": "Handwritten pages read",
        "ocr_detail": "exact amounts before review",
        "checks": "checks per run",
        "front": "en/04_quality_after.png",
        "back": "en/05_pipeline.png",
    },
}

STACK = ["Python", "Pandas", "DuckDB SQL", "Parquet", "Flask", "Celery · Redis", "RapidOCR", "Docker", "pytest"]


def page_html(lang: str, stats: dict) -> str:
    text = COPY[lang]
    chips = "".join(f"<span>{item}</span>" for item in STACK)
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0}}
body{{width:1600px;height:1200px;overflow:hidden;font-family:Inter,system-ui,sans-serif;color:#1d1d1f;
background:#f5f5f7}}
.left{{position:absolute;left:88px;top:92px;width:640px}}
.brand{{display:flex;align-items:center;gap:14px;font-size:26px;font-weight:700;letter-spacing:-.02em}}
.brand img{{width:48px;height:48px;border-radius:12px;object-fit:cover;transform:scale(1);box-shadow:0 0 0 1px #e8e8ed}}
.eyebrow{{margin-top:64px;color:#0066cc;font-size:20px;font-weight:600}}
h1{{margin-top:14px;font-size:74px;line-height:1.02;letter-spacing:-.045em;font-weight:800}}
h1 span{{color:#0071e3}}
.lead{{margin-top:26px;color:#6e6e73;font-size:23px;line-height:1.45}}
.metrics{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:40px}}
.metric{{padding:20px 22px;border-radius:20px;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.04),0 10px 30px rgba(0,0,0,.06)}}
.metric small{{display:block;color:#6e6e73;font-size:15px;font-weight:600}}
.metric b{{display:block;margin-top:6px;font-size:40px;letter-spacing:-.035em;font-variant-numeric:tabular-nums}}
.metric b i{{font-style:normal;color:#86868b;font-weight:600}}
.metric b em{{font-style:normal;color:#1a7f37}}
.metric p{{margin-top:2px;color:#6e6e73;font-size:14px}}
.stack{{display:flex;flex-wrap:wrap;gap:8px;margin-top:30px}}
.stack span{{padding:7px 13px;border-radius:999px;background:#fff;border:1px solid #e8e8ed;font-size:15px;font-weight:600;color:#424245}}
.shot{{position:absolute;border-radius:22px;overflow:hidden;background:#fff;box-shadow:0 30px 80px rgba(0,0,0,.18),0 0 0 1px rgba(0,0,0,.06)}}
.shot img{{display:block;width:100%}}
.back{{left:800px;top:110px;width:900px;transform:rotate(1.2deg);opacity:.96}}
.front{{left:760px;top:520px;width:900px}}
</style></head><body>
<div class="left">
  <div class="brand"><img src="{LOGO.as_uri()}">DataPilot</div>
  <div class="eyebrow">{text['eyebrow']}</div>
  <h1>{text['title']}</h1>
  <p class="lead">{text['lead']}</p>
  <div class="metrics">
    <div class="metric"><small>{text['quality']}</small><b><i>{stats['bronze']}%</i> → <em>{stats['silver']}%</em></b><p>Bronze → Silver · {stats['rows']} {'lignes' if lang == 'fr' else 'rows'}</p></div>
    <div class="metric"><small>{text['ocr']}</small><b>{stats['ocr_ok']}/{stats['ocr_total']}</b><p>{stats['ocr_pct']}% {text['ocr_detail']}</p></div>
  </div>
  <div class="stack">{chips}</div>
</div>
<div class="shot back"><img src="{(SHOTS / text['back']).as_uri()}"></div>
<div class="shot front"><img src="{(SHOTS / text['front']).as_uri()}"></div>
</body></html>"""


def main() -> None:
    report = json.loads((ROOT / "demo" / "test_suite" / "rapport_e2e.json").read_text(encoding="utf-8"))
    demo = next(item for item in report["spreadsheets"] if item["file"].startswith("comptoir"))
    documents = report["documents"]
    exact = sum(item["exact_amounts"] for item in documents)
    expected = sum(item["expected_rows"] for item in documents)
    stats = {
        "bronze": demo["quality_bronze"]["score"],
        "silver": demo["quality_silver_after_cleaning"]["score"],
        "rows": demo["rows"],
        "ocr_ok": sum(1 for item in documents if item["amount_accuracy_pct"] == 100),
        "ocr_total": len(documents),
        "ocr_pct": round(exact / expected * 100) if expected else 0,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", args=["--disable-lcd-text"])
        page = browser.new_page(viewport={"width": 1600, "height": 1200}, device_scale_factor=1.5)
        for lang in ("fr", "en"):
            html = ROOT / "demo" / "screenshots" / f"_cover_{lang}.html"
            html.write_text(page_html(lang, stats), encoding="utf-8")
            page.goto(html.as_uri())
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(600)
            target = SHOTS / COPY[lang]["file"]
            page.screenshot(path=str(target))
            html.unlink()
            print("✓", target.relative_to(ROOT))
        browser.close()


if __name__ == "__main__":
    main()
