# -*- coding: utf-8 -*-
"""Parcours automatisé de DataPilot et captures pour portfolio (FR et EN).

Prérequis : DataPilot lancé (par défaut sur http://127.0.0.1:5093) avec un
stockage vide, et Microsoft Edge ou Chrome installé.

    python demo/screenshots/capture.py --lang fr --out docs/screenshots/fr
    python demo/screenshots/capture.py --lang en --out docs/screenshots/en
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "demo" / "test_suite" / "files"


def shot(page: Page, out: Path, name: str, *, full: bool = False, clip_selector: str | None = None) -> None:
    page.wait_for_timeout(700)
    target = out / f"{name}.png"
    if clip_selector:
        page.locator(clip_selector).first.screenshot(path=str(target))
    else:
        page.screenshot(path=str(target), full_page=full)
    print("✓", target.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5093")
    parser.add_argument("--lang", default="fr", choices=["fr", "en"])
    parser.add_argument("--out", default="docs/screenshots/fr")
    parser.add_argument("--scale", type=float, default=2)
    parser.add_argument("--channel", default="msedge")
    args = parser.parse_args()
    out = (ROOT / args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel, args=["--disable-lcd-text", "--lang=" + ("fr-FR" if args.lang == "fr" else "en-US")])
        context = browser.new_context(viewport={"width": 1440, "height": 1080}, device_scale_factor=args.scale,
                                      locale="fr-FR" if args.lang == "fr" else "en-US",
                                      reduced_motion="reduce")
        page = context.new_page()
        page.goto(f"{args.base}/lang/{args.lang}?next=/")
        page.wait_for_load_state("networkidle")

        # 1. Démo : crée le projet Comptoir Atlas (900 ventes) puis revient à l'accueil.
        page.locator("form[action='/demo'] button").first.click()
        page.wait_for_url(re.compile(r".*/project/[0-9a-f]{32}.*"))
        project_url = re.sub(r"(/project/[0-9a-f]{32}).*", r"\1", page.url)

        # 2. Qualité : contrat Bronze vs Silver avant nettoyage.
        page.goto(f"{project_url}/cleaning")
        shot(page, out, "03_qualite_avant" if args.lang == "fr" else "03_quality_before")

        # Applique les corrections proposées (cochées par défaut + toutes les autres).
        page.locator(".suggestion input[type=checkbox]").evaluate_all("els => els.forEach(e => e.checked = true)")
        page.locator("#cleaning form button[type=submit]").first.click()
        page.wait_for_load_state("networkidle")
        page.goto(f"{project_url}/cleaning")
        page.locator("[data-toggle-checks]").check()
        page.evaluate("window.scrollTo(0, 0)")
        shot(page, out, "04_qualite_apres" if args.lang == "fr" else "04_quality_after")

        # 3. Vue d'ensemble après nettoyage.
        page.goto(project_url)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)
        shot(page, out, "02_vue_ensemble" if args.lang == "fr" else "02_overview")
        page.evaluate("document.querySelector('#automatic-dashboard').scrollIntoView({block: 'start'}); window.scrollBy(0, -90)")
        page.wait_for_timeout(900)
        shot(page, out, "02b_tableau_de_bord" if args.lang == "fr" else "02b_dashboard")

        # 4. Pipeline et lineage.
        page.goto(f"{project_url}/pipeline")
        shot(page, out, "05_pipeline")
        page.evaluate("document.querySelector('#layers-title').scrollIntoView({block: 'start'}); window.scrollBy(0, -80)")
        shot(page, out, "05b_couches_lineage" if args.lang == "fr" else "05b_layers_lineage")

        # 5. Console SQL sur la couche Gold.
        page.goto(f"{project_url}/sql")
        page.locator("[data-sql-editor]").fill(
            "SELECT produit, total, transactions, share_pct\nFROM gold_chiffre_affaires_net_mad_by_produit\nORDER BY total DESC\nLIMIT 8"
        )
        page.locator("#sql-console form button[type=submit]").click()
        page.wait_for_load_state("networkidle")
        page.evaluate("window.scrollTo(0, 0)")
        shot(page, out, "06_console_sql" if args.lang == "fr" else "06_sql_console")

        # 6. Graphique personnalisé + assistant.
        page.goto(f"{project_url}/charts")
        page.select_option("#chart-dimension", "ville")
        page.locator("#chart-studio details.chart-advanced-options summary").click()
        page.select_option("#chart-aggregation", "sum")
        page.select_option("#chart-measure", "chiffre_affaires_net_mad")
        page.locator("#chart-studio form button[type=submit]").click()
        page.wait_for_timeout(1500)
        page.locator("[data-chatbot-toggle]").click()
        question = "Que faut-il retenir de ce fichier ?" if args.lang == "fr" else "What are the key takeaways from this file?"
        page.locator("[data-chatbot-input]").fill(question)
        page.locator(".chatbot-form button[type=submit]").click()
        page.wait_for_timeout(2500)
        page.evaluate("""() => {
            const heading = document.querySelector('.custom-chart-heading');
            if (heading) window.scrollTo(0, heading.getBoundingClientRect().top + window.scrollY - 90);
            const region = document.querySelector('[data-answer-region]');
            const turns = region.querySelectorAll('.chat-turn');
            const last = turns[turns.length - 1];
            region.scrollTop = last.offsetTop - region.offsetTop - 8;
        }""")
        shot(page, out, "07_graphiques_assistant" if args.lang == "fr" else "07_charts_assistant")
        page.locator("[data-chatbot-close]").click()

        # 7. Carnet manuscrit : OCR puis vérification humaine.
        page.goto(f"{args.base}/#start")
        page.set_input_files("#documents", [str(FILES / "carnet_snack_incline.jpg")])
        page.locator("#documents-import form button[type=submit]").click()
        page.wait_for_url(re.compile(r".*/documents/review"), timeout=120000)
        page.wait_for_load_state("networkidle")
        shot(page, out, "08_carnet_manuscrit_ocr" if args.lang == "fr" else "08_handwritten_ocr")

        # 8. Accueil en dernier : le schéma affiche les chiffres réels de la démo nettoyée.
        page.goto(f"{args.base}/")
        page.wait_for_load_state("networkidle")
        page.evaluate("window.scrollTo(0, 0)")
        shot(page, out, "01_accueil" if args.lang == "fr" else "01_home")

        browser.close()


if __name__ == "__main__":
    main()
