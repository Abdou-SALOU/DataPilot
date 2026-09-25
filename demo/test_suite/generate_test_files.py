# -*- coding: utf-8 -*-
"""Génère un jeu de test reproductible avec vérité terrain connue.

Contenu (dossier ``files/``) :
- deux tableurs « sales » (CSV français à point-virgule, JSON anglais imbriqué) ;
- trois pages de carnet manuscrit simulées avec des polices d'écriture Windows,
  inclinaison, bruit, compression JPEG et flou pour dégrader la lecture ;
- un PDF scanné (image seule, sans texte) ;
- une facture fournisseur en image.

``ground_truth.json`` décrit ce que chaque fichier contient réellement : c'est la
référence utilisée par ``run_e2e.py`` pour mesurer ce que DataPilot détecte.
Toutes les données sont fictives.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "files"
FONTS = Path("C:/Windows/Fonts")
random.seed(2026)


# ---------------------------------------------------------------------------
# Tableurs avec anomalies injectées
# ---------------------------------------------------------------------------

PRODUCTS = [
    ("Thé vert 200 g", "Épicerie", 32.0), ("Huile d'olive 1 L", "Épicerie", 89.0),
    ("Café moulu 250 g", "Épicerie", 45.0), ("Savon noir", "Hygiène", 25.0),
    ("Shampooing argan", "Hygiène", 68.0), ("Jus d'orange 1 L", "Boissons", 18.0),
    ("Eau minérale 6x1,5 L", "Boissons", 36.0), ("Dattes Medjool 500 g", "Épicerie", 75.0),
]
CITIES = ["Casablanca", "Rabat", "Marrakech", "Tanger", "Fès"]
CITY_TYPOS = {"Casablanca": "Casblanca", "Marrakech": "Marrakesh", "Fès": "Fes", "Rabat": "rabat"}


def messy_sales_csv(path: Path) -> dict:
    rows = []
    for index in range(1, 301):
        product, category, price = random.choice(PRODUCTS)
        quantity = random.randint(1, 6)
        day = random.randint(1, 28)
        month = random.choice([6, 7, 8, 9])
        rows.append({
            "id_vente": f"V-{index:04d}",
            "date_vente": f"{day:02d}/{month:02d}/2026",
            "ville": random.choice(CITIES),
            "produit": product,
            "categorie": category,
            "quantite": str(quantity),
            "prix_unitaire": f"{price:.2f}".replace(".", ","),
            "montant": f"{price * quantity:.2f}".replace(".", ","),
            "note_satisfaction": str(random.randint(2, 5)),
        })
    truth = {"duplicates": [], "label_typos": [], "negative_quantity": [], "text_in_amount": [],
             "missing_city": [], "out_of_scale_rating": [], "iso_dates": [], "padded_text": []}
    for index in random.sample(range(300), 12):
        city = rows[index]["ville"]
        if city in CITY_TYPOS:
            rows[index]["ville"] = CITY_TYPOS[city]
            truth["label_typos"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 3):
        rows[index]["quantite"] = "-" + rows[index]["quantite"]
        truth["negative_quantity"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 2):
        rows[index]["montant"] = "N/A"
        truth["text_in_amount"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 4):
        rows[index]["ville"] = ""
        truth["missing_city"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 2):
        rows[index]["note_satisfaction"] = "9"
        truth["out_of_scale_rating"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 5):
        rows[index]["produit"] = f"  {rows[index]['produit']} "
        truth["padded_text"].append(rows[index]["id_vente"])
    for index in random.sample(range(300), 6):
        rows.append(dict(rows[index]))
        truth["duplicates"].append(rows[index]["id_vente"])
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "anomalies": {key: len(value) for key, value in truth.items() if value}, "ids": truth}


def english_orders_json(path: Path) -> dict:
    records = []
    for index in range(1, 121):
        product, category, price = random.choice(PRODUCTS)
        quantity = random.randint(1, 4)
        records.append({
            "order_id": f"ORD-{index:04d}",
            "order_date": f"2026-{random.choice(['07', '08', '09'])}-{random.randint(1, 28):02d}",
            "customer": {"city": random.choice(CITIES), "segment": random.choice(["Retail", "Wholesale"])},
            "product": product,
            "quantity": quantity,
            "amount": round(price * quantity, 2),
            "rating": random.randint(3, 5),
        })
    records[5]["amount"] = None
    records[17]["customer"]["city"] = "casablanca "
    records.append(dict(records[3]))
    path.write_text(json.dumps({"data": records}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"rows": len(records), "anomalies": {"duplicates": 1, "missing_amount": 1, "label_variant": 1}}


# ---------------------------------------------------------------------------
# Carnets manuscrits simulés
# ---------------------------------------------------------------------------

NOTEBOOKS = [
    {
        "name": "carnet_epicerie_propre.png", "font": "Inkfree.ttf", "size": 46, "angle": 0.0,
        "blur": 0.0, "noise": 6, "jpeg": None,
        "lines": [("Pain", "12,50"), ("Lait", "18,00"), ("Sucre", "32,00"), ("Thé", "14,50"), ("Biscuits", "27,00")],
    },
    {
        "name": "carnet_snack_incline.jpg", "font": "segoepr.ttf", "size": 44, "angle": 2.8,
        "blur": 0.6, "noise": 14, "jpeg": 62,
        "lines": [("Sandwich", "25,00"), ("Jus orange", "15,00"), ("Msemen", "6,00"), ("Café", "10,00"), ("Eau", "5,00"), ("Harira", "12,00")],
    },
    {
        "name": "carnet_boutique_difficile.jpg", "font": "LHANDW.TTF", "size": 40, "angle": -4.5,
        "blur": 1.1, "noise": 22, "jpeg": 45,
        "lines": [("Savon noir", "25,00"), ("Henné", "40,00"), ("Argan", "120,00"), ("Encens", "18,50")],
    },
]


def paper(width: int, height: int, noise: int) -> Image.Image:
    base = Image.new("RGB", (width, height), (246, 241, 226))
    draw = ImageDraw.Draw(base)
    for y in range(140, height, 64):
        draw.line([(40, y), (width - 40, y)], fill=(170, 196, 222), width=2)
    draw.line([(110, 0), (110, height)], fill=(222, 140, 140), width=2)
    pixels = base.load()
    for _ in range(width * height // 9):
        x, y = random.randrange(width), random.randrange(height)
        r, g, b = pixels[x, y]
        delta = random.randint(-noise, noise)
        pixels[x, y] = (max(0, min(255, r + delta)), max(0, min(255, g + delta)), max(0, min(255, b + delta)))
    return base


def handwritten_notebook(spec: dict, path: Path) -> dict:
    width, height = 1100, 140 + 64 * (len(spec["lines"]) + 3)
    image = paper(width, height, spec["noise"])
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(FONTS / spec["font"]), spec["size"])
    title_font = ImageFont.truetype(str(FONTS / spec["font"]), spec["size"] - 6)
    draw.text((140, 70), "Ventes du 23/09", font=title_font, fill=(40, 52, 110))
    for index, (item, amount) in enumerate(spec["lines"]):
        y = 140 + 64 * (index + 1) - spec["size"] - 4
        ink = random.choice([(28, 36, 92), (20, 20, 30), (38, 58, 140)])
        draw.text((140 + random.randint(-6, 10), y), item, font=font, fill=ink)
        draw.text((720 + random.randint(-8, 12), y), amount, font=font, fill=ink)
    if spec["angle"]:
        image = image.rotate(spec["angle"], resample=Image.BICUBIC, expand=True, fillcolor=(90, 84, 76))
    if spec["blur"]:
        image = image.filter(ImageFilter.GaussianBlur(spec["blur"]))
    if spec["jpeg"]:
        image.save(path, "JPEG", quality=spec["jpeg"])
    else:
        image.save(path)
    return {
        "rows": len(spec["lines"]),
        "difficulty": {"font": spec["font"], "rotation_deg": spec["angle"], "blur": spec["blur"], "jpeg_quality": spec["jpeg"]},
        "lines": [{"product": item, "amount": float(amount.replace(",", "."))} for item, amount in spec["lines"]],
        "total": round(sum(float(amount.replace(",", ".")) for _item, amount in spec["lines"]), 2),
    }


def scanned_pdf(source: Path, path: Path) -> None:
    Image.open(source).convert("RGB").save(path, "PDF", resolution=150)


def invoice_image(path: Path) -> dict:
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    bold = ImageFont.truetype(str(FONTS / "arialbd.ttf"), 44)
    regular = ImageFont.truetype(str(FONTS / "arial.ttf"), 30)
    draw.text((90, 90), "ATLAS DISTRIBUTION SARL", font=bold, fill="black")
    draw.text((90, 150), "Zone industrielle Sidi Maarouf, Casablanca", font=regular, fill=(60, 60, 60))
    draw.text((90, 260), "FACTURE N° F-2026-0412", font=bold, fill="black")
    draw.text((90, 330), "Date : 12/09/2026", font=regular, fill="black")
    y = 450
    for label, quantity, unit, total in [("Huile d'olive 1 L", 20, "45,00", "900,00"), ("Thé vert 200 g", 10, "35,00", "350,00")]:
        draw.text((90, y), label, font=regular, fill="black")
        draw.text((650, y), str(quantity), font=regular, fill="black")
        draw.text((800, y), unit, font=regular, fill="black")
        draw.text((1000, y), total, font=regular, fill="black")
        y += 60
    draw.line([(90, y + 20), (1150, y + 20)], fill="black", width=2)
    draw.text((700, y + 60), "Total HT : 1 250,00 MAD", font=regular, fill="black")
    draw.text((700, y + 110), "TVA 20 % : 250,00 MAD", font=regular, fill="black")
    draw.text((700, y + 160), "Total TTC : 1 500,00 MAD", font=bold, fill="black")
    image.save(path)
    return {"supplier": "ATLAS DISTRIBUTION SARL", "invoice_number": "F-2026-0412", "date": "2026-09-12",
            "total_ht": 1250.0, "vat": 250.0, "total_ttc": 1500.0, "currency": "MAD"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    truth = {
        "ventes_mensuelles_sales.csv": messy_sales_csv(OUT / "ventes_mensuelles_sales.csv"),
        "orders_en.json": english_orders_json(OUT / "orders_en.json"),
    }
    for spec in NOTEBOOKS:
        truth[spec["name"]] = handwritten_notebook(spec, OUT / spec["name"])
    scanned_pdf(OUT / "carnet_snack_incline.jpg", OUT / "carnet_snack_scan.pdf")
    truth["carnet_snack_scan.pdf"] = dict(truth["carnet_snack_incline.jpg"], note="PDF image seule du carnet incliné")
    truth["facture_atlas.png"] = invoice_image(OUT / "facture_atlas.png")
    (HERE / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(truth)} fichiers générés dans {OUT}")


if __name__ == "__main__":
    main()
