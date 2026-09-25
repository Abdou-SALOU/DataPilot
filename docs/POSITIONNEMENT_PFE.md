# DataPilot : positionnement pour un stage PFE

## Proposition de valeur

DataPilot aide un commerçant qui n'a ni analyste ni formation en IA à comprendre un fichier de ventes : import, contrôle de qualité, corrections approuvées, indicateurs, puis une synthèse en langage courant avec calculs et limites visibles. L'objectif est de rendre l'analyse accessible, pas de promettre une hausse automatique du bénéfice.

**Pitch court :** « J'ai construit un assistant local qui transforme les fichiers de ventes de petites entreprises en décisions vérifiables. Il montre d'où viennent les chiffres, demande une validation humaine avant de corriger les données et n'invente pas de rentabilité quand les coûts manquent. »

## Ce que les employeurs peuvent y voir

| Cible | Problème concret | Démonstration DataPilot | Limite à annoncer |
|---|---|---|---|
| Banque, équipe PME ou innovation | Les petites entreprises manquent souvent d'états financiers et de données structurées ; les canaux numériques et l'analyse peuvent améliorer la visibilité de leur activité. | Préparation des données, chiffres traçables, synthèse simple pour le client ou le chargé d'affaires. | DataPilot n'évalue pas la solvabilité, ne produit pas de score de crédit et ne remplace pas les contrôles bancaires. |
| Banque, gouvernance des données ou risque opérationnel | L'IA en banque exige une gouvernance des données et des risques maîtrisés. | Traitement local par défaut, corrections explicites, limites des calculs affichées, appel externe facultatif fondé sur des agrégats. | Prototype local sans gestion des identités, piste d'audit complète ni homologation de sécurité bancaire. |
| Cabinet de conseil | Un analyste doit analyser des données et expliquer des recommandations aux clients. | Parcours « fichier brut → qualité → constat → action à vérifier », compréhensible par un non-spécialiste. | Une recommandation issue de données descriptives reste une hypothèse à valider sur le terrain. |

Ces angles s'appuient sur les sources primaires suivantes, consultées en septembre 2026 : [IFC, *MSME Banking in the Digital Era*](https://www.ifc.org/en/insights-reports/2025/msme-banking-in-the-digital-era) ; [Comité de Bâle, *Digitalisation of finance*](https://www.bis.org/media-releases/20240516-basel-committee-publishes-report-digitalisation-finance) ; [EBA, *Special topic – Artificial intelligence*](https://www.eba.europa.eu/publications-and-media/publications/special-topic-artificial-intelligence) ; [McKinsey, rôle de Business Analyst](https://www.mckinsey.com/careers/our-roles/consulting-roles) ; [BCG, rôle d'Associate](https://careers.bcg.com/global/en/locations/korea/associate-jobs).

## Démonstration en cinq minutes

1. Cliquer sur **Essayer la démo** (Comptoir Atlas, 900 ventes fictives) et montrer le schéma Bronze → Contrat → Silver → Gold de l'accueil.
2. Page **Qualité & corrections** : les contrôles du contrat en échec sur Bronze (doublons, « Casblanca », prix négatifs), puis appliquer les corrections validées ; la qualité passe de 61 % à 98 %. Montrer qu'une étape s'annule.
3. Page **Pipeline** : les quatre étapes avec durées et volumes, l'empreinte SHA-256 et l'historique des exécutions (lineage).
4. Page **SQL** : interroger la table Gold du chiffre d'affaires par produit, puis montrer qu'un `DELETE` est refusé.
5. Importer une photo de carnet manuscrit inclinée : les lignes lues avec leur confiance OCR, la date d'en-tête reprise, la validation humaine avant tout calcul.
6. Basculer en anglais (bouton EN) pour montrer que l'outil est utilisable par une équipe internationale.
7. Conclure sur les limites : un total n'est pas un bénéfice, et le contrat inféré reste à valider par le métier.

## Suite prioritaire pour un PFE

1. **Validation terrain :** cinq entretiens avec commerçants et chargés d'affaires PME, puis tâches observées sans assistance. Mesurer le temps nécessaire pour trouver un chiffre exact, comprendre une limite et choisir une action.
2. **Mesure d'impact :** constituer un jeu de données synthétiques avec résultats attendus ; mesurer exactitude des montants, taux de recommandations étayées et erreurs d'interprétation. Ne parler de gain financier qu'après étude contrôlée.
3. **Traçabilité :** conserver version du fichier, règles appliquées, horodatage, choix de l'utilisateur et formule des indicateurs dans un rapport exportable.
4. **Déploiement professionnel :** ajouter authentification, isolation des entreprises, chiffrement, sauvegardes, journal d'accès, durée de conservation et tests de sécurité avant toute donnée bancaire ou client réelle.
5. **Extension métier :** importer dépenses, coûts d'achat et stocks avec validation de leur définition avant de calculer marge, rotation ou besoins de trésorerie.

Le cadrage prudent du crédit est important : l'[EBA décrit des exigences supplémentaires pour les usages d'IA liés à la solvabilité et au score de crédit](https://www.eba.europa.eu/sites/default/files/2025-11/d8b999ce-a1d9-4964-9606-971bbc2aaf89/AI%20Act%20implications%20for%20the%20EU%20banking%20sector.pdf). DataPilot doit rester un outil de compréhension des données tant que ces exigences ne sont pas étudiées et satisfaites.
