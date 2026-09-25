# Essai Boutique Atlas

Entreprise fictive. Trois documents de ventes sont importés dans un seul lot :

| Fichier | Lignes attendues | Total attendu |
|---|---:|---:|
| `ventes_caisse.xlsx` | 3 | 127,50 MAD |
| `ventes_complementaires.txt` | 2 | 47,00 MAD |
| `carnet_manuscrit.png` | 5 | 117,00 MAD |
| **Total** | **10** | **291,50 MAD** |

`carnet_scan.pdf` est une version PDF scannée de la même photo. Elle sert à
tester l'OCR des PDF sans texte : 5 lignes ont été proposées lors de l'essai
local. Ne l'importez pas avec la photo dans le même lot, sinon les ventes seront
comptées deux fois.

La photo a été créée avec GPT Image avec cette demande : « Photographie réaliste
d'un carnet manuscrit de la Boutique Atlas, avec cinq ventes : Pain 25,50 ;
Lait 18,00 ; Sucre 32,00 ; Thé 14,50 ; Biscuits 27,00. Écritures de styles
différents, date 23/09/2026, lumière naturelle, aucun autre total. » Elle est
entièrement fictive et sert de test OCR, pas de preuve de performance générale.

Pour refaire l'essai, démarrez DataPilot puis exécutez :

```powershell
python demo/essai_boutique_atlas/run_trial.py
```

Si DataPilot tourne sur un autre port, définissez d'abord
`$env:DATAPILOT_TEST_URL = "http://127.0.0.1:5083"`.

Le script utilise l'interface HTTP locale, vérifie les lignes proposées,
remplace « The » par « Thé », ajoute la date visible sur la photo, confirme les
10 ventes et contrôle le total ainsi que la présence de graphiques. Résultat de
l'exécution : [`resultat_essai.json`](resultat_essai.json).
