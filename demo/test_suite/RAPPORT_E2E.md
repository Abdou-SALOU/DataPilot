# Rapport d'essai de bout en bout

Généré par `demo/test_suite/run_e2e.py`. Données fictives, vérité terrain dans `ground_truth.json`.

## Tableurs : contrat de données et nettoyage

| Fichier | Lignes | Contrôles en écart (Bronze) | Qualité Bronze | Qualité Silver après nettoyage | Lignes Silver | Tables Gold | Durée |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ventes_mensuelles_sales.csv` | 306 | 7 | 68 % | 87 % | 300 | 3 | 1.23 s |
| `orders_en.json` | 121 | 4 | 78 % | 100 % | 120 | 3 | 0.77 s |
| `comptoir_atlas_ventes.xlsx` | 900 | 18 | 61 % | 98 % | 892 | 3 | 5.78 s |

## Carnets manuscrits et PDF scanné : lecture OCR locale, avant correction humaine

| Fichier | Difficulté | Lignes attendues | Lignes retrouvées | Montants exacts | Lignes en trop | Total attendu | Total proposé |
|---|---|---:|---:|---:|---:|---:|---:|
| `carnet_epicerie_propre.png` | Inkfree.ttf, rotation 0.0°, flou 0.0, JPEG non | 5 | 5 (100.0 %) | 5 (100.0 %) | 0 | 104.00 | 104.00 |
| `carnet_snack_incline.jpg` | segoepr.ttf, rotation 2.8°, flou 0.6, JPEG 62 | 6 | 6 (100.0 %) | 6 (100.0 %) | 0 | 73.00 | 73.00 |
| `carnet_boutique_difficile.jpg` | LHANDW.TTF, rotation -4.5°, flou 1.1, JPEG 45 | 4 | 4 (100.0 %) | 4 (100.0 %) | 0 | 203.50 | 203.50 |
| `carnet_snack_scan.pdf` | segoepr.ttf, rotation 2.8°, flou 0.6, JPEG 62 | 6 | 6 (100.0 %) | 6 (100.0 %) | 0 | 73.00 | 73.00 |
| `carnet_photo_realiste.png` | photo réaliste générée par IA | 5 | 5 (100.0 %) | 5 (100.0 %) | 0 | 117.00 | 117.00 |

Après la vérification humaine (écran « Vérifier »), les lignes confirmées alimentent le pipeline : aucun chiffre lu par l'OCR n'est utilisé sans validation.

## Facture

- `facture_atlas.png` : 7/7 champs corrects (supplier ✓, invoice_number ✓, date ✓, total_ht ✓, vat ✓, total_ttc ✓, currency ✓).
